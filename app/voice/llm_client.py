import json
import re
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from fastapi.testclient import TestClient
from loguru import logger

from app.api.config import get_settings
from app.api.main import app
from app.voice.conversation_logger import ConversationLogger
from app.voice.tools import TOOLS

settings = get_settings()
client = TestClient(app)

FALLBACK_ERROR_MESSAGE = "Something went wrong. Please call us again after an hour."

SYSTEM_PROMPT = (
    '''
    You are a helpful voice appointment assistant for a medical clinic.
    Speak naturally, concisely, and directly — keep responses to one short sentence per turn whenever possible. Do not speak special characters or format instructions.
    
    IMPORTANT INSTRUCTIONS & CONSTRAINTS:
    - Do NOT use filler phrases like 'Once I have this information...' or 'How else can I help you?'.
    - GREETING & INTENT FLOW: On the first turn, introduce yourself as the clinic assistant and ask what the caller needs (e.g. 'Hello! Welcome to Medical Clinic. Are you looking to book, reschedule, or cancel an appointment, or do you have a general question?').
    - Do NOT ask for patient identity on the first turn or when answering general questions (e.g. clinic hours, services). Only request identity verification when performing patient record actions (booking, cancelling, rescheduling).
    - DOB & IDENTITY PARSING: Accept natural spoken dates in ANY format (e.g. 'August 22 1992', '22nd of August 1992', '1992 08 22'). Convert the spoken date silently into YYYY-MM-DD format and call `verify_identity` ONLY when all three components (month, day, and year) are present.
    - PARTIAL DATE GUARD: NEVER call `verify_identity` with a partial date (e.g. year-only or month-only like '1978' or 'December 1978'). If month, day, or year is missing, ask the caller directly for the missing detail (e.g. 'Which day in December were you born?').
    - NO EXAMPLE FORMATS: NEVER speak example date strings (such as '1992-08-22', 'January 1, 1980', or 'YYYY-MM-DD format') to the caller. Do not tell the caller how to format their input or repeat fake example names like 'John Doe'.
    - VERIFICATION FAILURE: If `verify_identity` returns verified=false, state politely that the identity details could not be matched with our records, and ask the caller to re-confirm their full name and full date of birth.
    - VERIFICATION SUCCESS & PERSISTENCE: ONCE `verify_identity` returns verified=false, you may retry. BUT ONCE `verify_identity` returns verified=true, THE PATIENT IS FULLY VERIFIED FOR THE REST OF THE SESSION. NEVER call `verify_identity` again or ask for name/DOB again in this session. Proceed directly to checking availability, proposing bookings, or answering caller questions.
    
    TWO-STEP BOOKING FLOW:
    1. When a patient requests an appointment, first call `propose_booking` to check slot availability and working hours.
    2. Read the returned proposal summary back to the caller and explicitly wait for their confirmation ('yes' or 'confirm').
    3. ONLY call `confirm_booking` with the `confirmation_token` AFTER the caller explicitly confirms ('yes'). If the caller says 'no' or asks to change something, do NOT call `confirm_booking` — offer to propose a different time instead.
    
    **Available tools**
    * `verify_identity`: Verify patient identity using first_name, last_name, date_of_birth (YYYY-MM-DD).
    * `check_availability`: Retrieve open 30-minute appointment slots for a doctor on a date (YYYY-MM-DD).
    * `propose_booking`: Validate appointment slot and get a confirmation token without writing to the database.
    * `confirm_booking`: Finalize and write appointment to the database using the confirmation_token.
    * `cancel_appointment`: Cancel an appointment for a verified patient.
    * `reschedule_appointment`: Reschedule an appointment for a verified patient.
    '''
)


def execute_tool_call(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executes a tool call against the local FastAPI application.
    """
    if name == "verify_identity":
        res = client.post("/verify-identity", json=args)
        return res.json()
    elif name == "check_availability":
        doctor_id = args.get("doctor_id")
        date_str = args.get("date")
        res = client.get(f"/doctors/{doctor_id}/availability?date={date_str}")
        return res.json()
    elif name == "propose_booking":
        res = client.post("/appointments/propose", json=args)
        return res.json()
    elif name == "confirm_booking":
        res = client.post("/appointments/confirm", json=args)
        return res.json()
    elif name == "book_appointment":
        res = client.post("/appointments", json=args)
        return res.json()
    elif name == "cancel_appointment":
        appointment_id = args.get("appointment_id")
        patient_id = args.get("patient_id")
        res = client.delete(f"/appointments/{appointment_id}?patient_id={patient_id}")
        return res.json()
    elif name == "reschedule_appointment":
        appointment_id = args.get("appointment_id")
        res = client.patch(f"/appointments/{appointment_id}", json=args)
        return res.json()
    else:
        return {"error": f"Unknown tool: {name}"}


def _call_ollama_api(
    messages: List[Dict[str, Any]], tools: List[Dict[str, Any]] = None, stream: bool = True
) -> Any:
    model_name = getattr(settings, "LLM_MODEL", None) or getattr(settings, "OLLAMA_MODEL", "qwen2.5:7b-instruct-q4_K_M")
    url = f"{settings.OLLAMA_HOST.rstrip('/')}/api/chat"
    payload = {
        "model": model_name,
        "messages": messages,
        "stream": stream,
    }
    if tools:
        payload["tools"] = tools

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as response:
            if stream:
                for line in response:
                    line_str = line.strip()
                    if line_str:
                        yield json.loads(line_str.decode("utf-8"))
            else:
                return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        logger.error(f"[Ollama API HTTP {e.code}]: {body}")
        raise RuntimeError(f"Ollama API HTTP {e.code}: {body}") from e


def _call_openai_compatible_api(
    messages: List[Dict[str, Any]], tools: List[Dict[str, Any]] = None, stream: bool = True
) -> Any:
    api_base = settings.LLM_API_BASE or "https://api.groq.com/openai/v1"
    url = f"{api_base.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if settings.LLM_API_KEY:
        headers["Authorization"] = f"Bearer {settings.LLM_API_KEY}"

    payload = {
        "model": getattr(settings, "LLM_MODEL", "qwen-2.5-7b-instruct"),
        "messages": messages,
        "stream": stream,
    }
    if tools:
        payload["tools"] = tools

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req) as response:
            if stream:
                for line in response:
                    line_str = line.decode("utf-8").strip()
                    if line_str.startswith("data: "):
                        data_content = line_str[6:].strip()
                        if data_content == "[DONE]":
                            break
                        parsed = json.loads(data_content)
                        choices = parsed.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            yield {"message": {"content": delta.get("content", ""), "tool_calls": delta.get("tool_calls", [])}}
            else:
                parsed = json.loads(response.read().decode("utf-8"))
                choices = parsed.get("choices", [])
                msg = choices[0].get("message", {}) if choices else {}
                return {"message": msg}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        logger.error(f"[OpenAI-Compatible API HTTP {e.code}]: {body}")
        raise RuntimeError(f"OpenAI-Compatible API HTTP {e.code}: {body}") from e


def call_llm_api(
    messages: List[Dict[str, Any]], tools: List[Dict[str, Any]] = None, stream: bool = True
) -> Any:
    """
    Sends a chat completion request to the configured LLM provider (Ollama or Groq/OpenAI).
    Implements a 3-retry attempt loop on network/server errors.
    """
    max_retries = 3
    last_exception = None
    provider = getattr(settings, "LLM_PROVIDER", "ollama").lower()
    for attempt in range(1, max_retries + 1):
        try:
            if provider in ("groq", "openai"):
                res_gen = _call_openai_compatible_api(messages, tools=tools, stream=stream)
                return list(res_gen) if stream else res_gen
            else:
                res_gen = _call_ollama_api(messages, tools=tools, stream=stream)
                return list(res_gen) if stream else res_gen
        except Exception as e:
            last_exception = e
            logger.warning(f"[LLM API Attempt {attempt}/{max_retries} Failed]: {e}")
            if attempt < max_retries:
                time.sleep(0.5 * attempt)

    raise RuntimeError(f"LLM API request failed after {max_retries} attempts: {last_exception}")


call_ollama = call_llm_api


def sanitize_for_tts(text: str) -> str:
    """
    Sanitizes LLM output text prior to TTS synthesis.
    Strips <think>...</think> reasoning blocks, quotes (' and "), backticks (`), and markdown asterisks (*).
    Collapses repeated whitespace.
    Returns empty string if the sanitized text contains no alphanumeric characters.
    """
    if not text:
        return ""
    # Strip complete <think>...</think> reasoning blocks
    sanitized = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    # Strip standalone or unclosed <think> or </think> tags
    sanitized = re.sub(r"</?think>", "", sanitized, flags=re.IGNORECASE)
    # Strip quotes (' and "), backticks (`), and asterisks (*)
    sanitized = re.sub(r"['\"`*]", "", sanitized)
    # Collapse repeated whitespace
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    # If chunk contains no alphanumeric characters, drop it entirely
    if not any(c.isalnum() for c in sanitized):
        return ""
    return sanitized


def extract_sentences_from_buffer(buffer: str) -> Tuple[List[str], str]:
    """
    Extracts complete sentences ending with '.', '!', '?', or newline from buffer.
    Returns (list_of_completed_sentences, remaining_buffer).
    Sanitizes each sentence, strips <think> reasoning blocks, and drops punctuation-only fragments.
    """
    # Remove complete <think>...</think> blocks from buffer
    buffer = re.sub(r"<think>.*?</think>", "", buffer, flags=re.DOTALL)

    # If buffer is currently inside an unclosed <think> block, wait for closing tag
    if "<think>" in buffer.lower() and "</think>" not in buffer.lower():
        return [], buffer

    matches = list(re.finditer(r".*?[.!?\n]+(?:\s+|$)", buffer, flags=re.DOTALL))
    if not matches:
        return [], buffer

    sentences = []
    last_end = 0
    for match in matches:
        s = match.group(0).strip()
        if s:
            sanitized = sanitize_for_tts(s)
            if sanitized:
                sentences.append(sanitized)
        last_end = match.end()

    remaining = buffer[last_end:]
    return sentences, remaining


def run_chat_turn_stream(
    history: List[Dict[str, Any]],
    user_message: str,
    logger_instance: Optional[ConversationLogger] = None,
):
    """
    Generator yielding completed text sentences as they arrive in the stream.
    Handles tool calls transparently with strict sequential context ordering.
    Returns (final_response_text, updated_history) via generator return.
    """
    messages = list(history)
    if not messages or messages[0].get("role") != "system":
        messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})

    messages.append({"role": "user", "content": user_message})
    if logger_instance:
        logger_instance.log_stt(user_message)

    while True:
        try:
            res = call_ollama(messages, tools=TOOLS, stream=True)
            if isinstance(res, dict):
                chunks = [res]
            else:
                chunks = res
        except Exception as e:
            logger.error(f"[LLM Stream Error after retries]: {e}")
            yield FALLBACK_ERROR_MESSAGE
            return FALLBACK_ERROR_MESSAGE, history

        full_content = ""
        tool_calls_acc: List[Dict[str, Any]] = []
        buffer = ""

        for chunk in chunks:
            msg = chunk.get("message", {})
            delta_content = msg.get("content", "")
            if delta_content:
                full_content += delta_content
                buffer += delta_content
                sentences, buffer = extract_sentences_from_buffer(buffer)
                for sentence in sentences:
                    yield sentence

            delta_tool_calls = msg.get("tool_calls", [])
            if delta_tool_calls:
                tool_calls_acc.extend(delta_tool_calls)

        if buffer.strip() and not tool_calls_acc:
            sanitized_buf = sanitize_for_tts(buffer.strip())
            if sanitized_buf:
                yield sanitized_buf
            buffer = ""

        assistant_msg: Dict[str, Any] = {"role": "assistant"}
        clean_full_content = sanitize_for_tts(full_content)
        if clean_full_content:
            assistant_msg["content"] = clean_full_content
        if tool_calls_acc:
            assistant_msg["tool_calls"] = tool_calls_acc
        messages.append(assistant_msg)

        if full_content and logger_instance:
            logger_instance.log_llm_response(clean_full_content)

        if tool_calls_acc:
            for tool_call in tool_calls_acc:
                fn = tool_call.get("function", {})
                tool_name = fn.get("name")
                tool_args = fn.get("arguments", {})
                if isinstance(tool_args, str):
                    tool_args = json.loads(tool_args)

                # Retry tool call up to 3 times
                result = None
                for attempt in range(1, 4):
                    try:
                        result = execute_tool_call(tool_name, tool_args)
                        break
                    except Exception as te:
                        logger.warning(f"[Tool Call '{tool_name}' Attempt {attempt}/3 Failed]: {te}")
                        if attempt == 3:
                            result = {"error": "Internal service error after retries."}
                        else:
                            time.sleep(0.5 * attempt)

                if logger_instance:
                    logger_instance.log_tool_call(tool_name, tool_args, result)
                messages.append(
                    {
                        "role": "tool",
                        "content": json.dumps(result),
                        "name": tool_name,
                    }
                )
            continue
        else:
            return clean_full_content, messages


def run_chat_turn(
    history: List[Dict[str, Any]],
    user_message: str,
    logger_instance: Optional[ConversationLogger] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Runs a single turn of conversation with tool calling execution.
    Returns (assistant_response_text, updated_history).
    """
    gen = run_chat_turn_stream(history, user_message, logger_instance=logger_instance)
    sentences = []
    try:
        while True:
            sentences.append(next(gen))
    except StopIteration as e:
        if e.value:
            final_text, updated_history = e.value
            return final_text, updated_history
        else:
            return " ".join(sentences), history
