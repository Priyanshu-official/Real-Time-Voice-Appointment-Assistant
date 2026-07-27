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

SYSTEM_PROMPT = """
You are a voice appointment assistant for Medical Clinic. You are speaking to a caller
over a live phone/voice connection. Everything you say will be converted to speech by
a TTS engine, so your output must be clean, natural, spoken English — never
machine-readable formats, symbols, or notation of any kind.

=====================================================================
RULE 1 — NEVER SPEAK MACHINE FORMATS OR EXAMPLE STRINGS
=====================================================================
- NEVER say ISO 8601 strings, format templates, or examples like:
  "YYYY-MM-DDTHH:MM:SSZ", "2023-11-16T10:00:00Z", "MM/DD/YYYY", etc.
- NEVER instruct the caller to provide a date/time in a specific machine format.
  Callers speak naturally — you must understand and normalize their speech, not the
  other way around.
- Ask for dates/times the way a human receptionist would:
  ✅ "What day and time works best for you?"
  ✅ "And what's your date of birth?"
  ❌ "Please provide the date in this format: YYYY-MM-DDTHH:MM:SSZ"
- Internally, YOU are responsible for converting whatever the caller says
  ("next Tuesday at 10 in the morning", "November 28th at 10 AM", "nineteen
  seventy eight, December third") into the ISO 8601 format required by tool calls.
  The caller must never see or hear that format.

=====================================================================
RULE 2 — NEVER CALL A TOOL WITH INCOMPLETE OR AMBIGUOUS PARAMETERS
=====================================================================
This applies to EVERY tool: verify_identity, book_appointment,
reschedule_appointment, cancel_appointment, and any other tool you have access to.

Before calling any tool:
1. Confirm you have ALL required parameters, fully resolved:
   - verify_identity: full name (first + last) AND complete date of birth
     (month, day, AND year — all three, unambiguous).
   - book_appointment / reschedule_appointment: doctor/provider name, AND a
     fully resolved date (month, day, year) AND time (hour + AM/PM or equivalent).
   - cancel_appointment: appointment ID or enough identifying detail (doctor,
     date) to uniquely resolve which appointment.
2. If ANY component is missing, ambiguous, or was only partially captured
   (e.g. caller said "1978" but not the month/day, or "Friday" with no time),
   DO NOT call the tool. Instead, ask the caller specifically for ONLY the
   missing piece — do not re-ask for information you already have.
   ✅ "Got the date — what time on the 28th works for you?"
   ❌ Silently guessing a time and proceeding to call the tool anyway.
3. If speech-to-text output looks garbled, cut off, or nonsensical (e.g. "to lie",
   "any ideas scheduling", stray fragments), do NOT treat it as valid input for a
   tool call. Ask the caller to repeat or clarify rather than guessing or
   fabricating a value to fill the gap.
4. Never fabricate, infer, or "round to the nearest reasonable value" for any
   parameter. If you are not certain, ask.

=====================================================================
RULE 3 — IDENTITY VERIFICATION IS STICKY
=====================================================================
- Once verify_identity returns verified=true, identity is locked as verified
  for the remainder of the session. Do NOT re-ask for name or date of birth
  again in this call, even if the topic changes (e.g. moving from reschedule
  to booking a new appointment).

=====================================================================
RULE 4 — CONTEXTUAL AFFIRMATIONS
=====================================================================
- Distinguish between a caller's first utterance (e.g. "Hi", "Hello") which
  warrants the full greeting, and a mid-conversation short affirmative
  ("Yeah", "Sure", "Okay") which should NOT re-trigger the greeting or reset
  the flow — treat it as confirmation of the immediately preceding question.

=====================================================================
RULE 5 — SPOKEN TEXT HYGIENE
=====================================================================
- Every sentence you emit must contain at least one full word — never emit a
  standalone punctuation mark, single letter, or fragment as its own sentence
  (e.g. never emit "." or "!" alone).
- When combining short exclamations with a following sentence (e.g. "Got it!"
  + "Dr. Smith on tomorrow."), always ensure a single space separates them.
  Never allow two sentences or abbreviations to be concatenated with no space
  (e.g. "Dr.Smith" is invalid — it must render as "Dr. Smith").
- Preserve natural contractions ("what's", "it's", "let's") — do not strip
  their apostrophes.
- Do not use unnecessary quotation marks or all-caps emphasis in spoken text.

=====================================================================
RULE 6 — CLARIFY BEFORE ACTING, ALWAYS
=====================================================================
When in doubt about ANY tool call parameter, prefer asking a short, specific
clarifying question over calling the tool. A single well-placed question is
always better than a failed tool call, an incorrect booking, or a fabricated
value.
"""


from app.api.services.scheduling import normalize_datetime_utterance


def execute_tool_call(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executes a tool call against the local FastAPI application.
    Validates datetime parameters using normalize_datetime_utterance before calling scheduling APIs.
    """
    if name == "verify_identity":
        res = client.post("/verify-identity", json=args)
        return res.json()
    elif name == "check_availability":
        doctor_id = args.get("doctor_id")
        date_str = args.get("date")
        if date_str and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(date_str)):
            iso_str, _ = normalize_datetime_utterance(f"{date_str} at 10 AM")
            if iso_str:
                date_str = iso_str.split("T")[0]
        res = client.get(f"/doctors/{doctor_id}/availability?date={date_str}")
        return res.json()
    elif name in ("propose_booking", "book_appointment", "reschedule_appointment"):
        raw_datetime = args.get("start_time") or args.get("new_start_time")
        if not raw_datetime:
            return {
                "status": "needs_clarification",
                "error": "Missing appointment date/time parameter. Please ask the caller specifically for the date and time.",
            }
        
        iso_str, reason = normalize_datetime_utterance(str(raw_datetime))
        if not iso_str:
            return {
                "status": "needs_clarification",
                "error": f"Invalid or incomplete appointment date/time parameter ({reason}). Do not guess — ask the caller specifically for the missing date or time.",
            }

        # Update payload with normalized ISO 8601 string
        if "start_time" in args:
            args["start_time"] = iso_str
        if "new_start_time" in args:
            args["new_start_time"] = iso_str

        # Ensure end_time is present and valid
        end_val = args.get("end_time") or args.get("new_end_time")
        if end_val:
            end_iso, _ = normalize_datetime_utterance(str(end_val))
            if end_iso:
                if "end_time" in args:
                    args["end_time"] = end_iso
                if "new_end_time" in args:
                    args["new_end_time"] = end_iso
        else:
            from datetime import datetime, timedelta, timezone
            dt_start = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            dt_end = dt_start + timedelta(minutes=30)
            end_iso = dt_end.strftime("%Y-%m-%dT%H:%M:%SZ")
            if "end_time" in args:
                args["end_time"] = end_iso
            elif "new_end_time" in args:
                args["new_end_time"] = end_iso
            elif name == "propose_booking":
                args["end_time"] = end_iso

        if name == "propose_booking":
            res = client.post("/appointments/propose", json=args)
            return res.json()
        elif name == "book_appointment":
            res = client.post("/appointments", json=args)
            return res.json()
        elif name == "reschedule_appointment":
            appointment_id = args.get("appointment_id")
            res = client.patch(f"/appointments/{appointment_id}", json=args)
            return res.json()
    elif name == "confirm_booking":
        res = client.post("/appointments/confirm", json=args)
        return res.json()
    elif name == "cancel_appointment":
        appointment_id = args.get("appointment_id")
        patient_id = args.get("patient_id")
        res = client.delete(f"/appointments/{appointment_id}?patient_id={patient_id}")
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
    Strips <think>...</think> reasoning blocks, double quotes ("), backticks (`), and markdown asterisks (*).
    Preserves single quotes (') inside words for contractions (e.g. what's, I'm, it's).
    Strips standalone or outer single quotes.
    Collapses repeated whitespace.
    Returns empty string if the sanitized text contains no alphanumeric characters.
    """
    if not text:
        return ""
    # Strip complete <think>...</think> reasoning blocks
    sanitized = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    # Strip standalone or unclosed <think> or </think> tags
    sanitized = re.sub(r"</?think>", "", sanitized, flags=re.IGNORECASE)
    # Strip double quotes ("), backticks (`), and asterisks (*)
    sanitized = re.sub(r"[\"`*]", "", sanitized)
    # Strip standalone or outer single quotes not part of a contraction word
    sanitized = re.sub(r"(?<!\w)'|'(?!\w)", "", sanitized)
    # Collapse repeated whitespace
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    # If chunk contains no alphanumeric characters, drop it entirely
    if not any(c.isalnum() for c in sanitized):
        return ""
    return sanitized


def format_tts_text(sentences: List[str]) -> str:
    """
    Ensures sentence fragments are always joined with a single space, stripping leading/trailing
    whitespace and preventing run-on punctuation artifacts (e.g. "Dr.Smith" -> "Dr. Smith").
    """
    if not sentences:
        return ""
    cleaned = [s.strip() for s in sentences if s and s.strip()]
    merged = " ".join(cleaned)
    # Ensure a single space after sentence/title punctuation preceding a word character
    merged = re.sub(r"(\b[A-Za-z]+[.!?])([A-Za-z])", r"\1 \2", merged)
    return merged


def extract_sentences_from_buffer(buffer: str) -> Tuple[List[str], str]:
    """
    Extracts complete sentences ending with '.', '!', '?', or newline from buffer.
    Returns (list_of_completed_sentences, remaining_buffer).
    Sanitizes each sentence, strips <think> reasoning blocks, drops punctuation-only fragments,
    and merges short 1-2 word intro exclamations (e.g. "Great!", "Sure!") into the following sentence.
    Does NOT break or mangle title abbreviations (e.g. "Dr.", "Mr.").
    """
    # Remove complete <think>...</think> blocks from buffer
    buffer = re.sub(r"<think>.*?</think>", "", buffer, flags=re.DOTALL)

    # If buffer is currently inside an unclosed <think> block, wait for closing tag
    if "<think>" in buffer.lower() and "</think>" not in buffer.lower():
        return [], buffer

    pattern = r".*?(?:(?<!\bDr)(?<!\bMr)(?<!\bMrs)(?<!\bMs)(?<!\bProf)(?<!\bSt)[.!?\n]+(?:\s+|$))"
    matches = list(re.finditer(pattern, buffer, flags=re.DOTALL))
    if not matches:
        return [], buffer

    sentences = []
    last_end = 0
    for match in matches:
        s = match.group(0).strip()
        if s:
            sanitized = sanitize_for_tts(s)
            if sanitized and any(c.isalnum() for c in sanitized):
                sentences.append(sanitized)
        last_end = match.end()

    # Merge very short 1-2 word intro exclamations (e.g. "Great!", "Sure!") with the next sentence,
    # skipping title abbreviations (e.g. "Dr.", "Mr.")
    merged = []
    i = 0
    abbrevs = {"dr", "mr", "mrs", "ms", "prof", "st"}
    while i < len(sentences):
        curr = sentences[i]
        words = curr.split()
        clean_curr_lower = re.sub(r"[.!?]+$", "", curr.lower())
        if (
            len(words) <= 2
            and i + 1 < len(sentences)
            and any(c.isalnum() for c in curr)
            and clean_curr_lower not in abbrevs
        ):
            next_sent = sentences[i + 1]
            clean_curr = re.sub(r"[.!?]+$", "", curr)
            merged.append(f"{clean_curr}, {next_sent[0].lower()}{next_sent[1:]}")
            i += 2
        else:
            merged.append(curr)
            i += 1

    remaining = buffer[last_end:]
    return merged, remaining


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
            return format_tts_text(sentences), history
