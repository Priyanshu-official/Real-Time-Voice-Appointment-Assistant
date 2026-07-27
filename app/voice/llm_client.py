import json
import re
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from fastapi.testclient import TestClient

from app.api.config import get_settings
from app.api.main import app
from app.voice.conversation_logger import ConversationLogger
from app.voice.tools import TOOLS

settings = get_settings()
client = TestClient(app)

SYSTEM_PROMPT = (
    "You are a helpful voice appointment assistant for a medical clinic. "
    "Speak naturally, concisely, and directly — keep responses to one short sentence per turn whenever possible. "
    "Do NOT use filler phrases like 'Once I have this information, we can proceed' or 'How else can I help you today?'.\n\n"
    "GREETING & INTENT FLOW: On the first turn, introduce yourself as the clinic assistant and ask what the caller needs (e.g. 'Hello! Welcome to Medical Clinic. Are you looking to book, reschedule, or cancel an appointment, or do you have a general question?').\n"
    "Do NOT ask for patient identity (name or DOB) on the first turn or when answering general questions (e.g. clinic hours, services). Only request identity verification when the caller asks for an action requiring patient records (booking, cancelling, rescheduling, checking existing appointments).\n\n"
    "CRITICAL SAFETY & PRIVACY RULE: You MUST verify patient identity using `verify_identity` (first_name, last_name, date_of_birth) BEFORE returning patient-specific data or executing booking/cancellation/rescheduling.\n"
    "DOB PARSING INSTRUCTION: Accept natural spoken dates (e.g. '16th February 2000', 'Feb 16 2005') and convert them to YYYY-MM-DD format before calling `verify_identity`.\n\n"
    "TWO-STEP BOOKING FLOW:\n"
    "1. When a patient requests an appointment, first call `propose_booking` to check slot availability and working hours.\n"
    "2. Read the returned proposal summary back to the caller and explicitly wait for their confirmation ('yes' or 'confirm').\n"
    "3. ONLY call `confirm_booking` with the `confirmation_token` AFTER the caller explicitly confirms ('yes'). If the caller says 'no' or asks to change something, do NOT call `confirm_booking` — offer to propose a different time instead.\n\n"
    "Available tools:\n"
    "1. `verify_identity`: Verify patient identity using first_name, last_name, date_of_birth (YYYY-MM-DD).\n"
    "2. `check_availability`: Retrieve open 30-minute appointment slots for a doctor on a date (YYYY-MM-DD).\n"
    "3. `propose_booking`: Validate appointment slot and get a confirmation token without writing to the database.\n"
    "4. `confirm_booking`: Finalize and write appointment to the database using the confirmation_token.\n"
    "5. `cancel_appointment`: Cancel an appointment for a verified patient.\n"
    "6. `reschedule_appointment`: Reschedule an appointment for a verified patient."
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


def call_ollama(
    messages: List[Dict[str, Any]], tools: List[Dict[str, Any]] = None, stream: bool = True
) -> Any:
    """
    Sends a chat completion request to the local Ollama instance.
    If stream=True, yields parsed JSON response objects line-by-line.
    If stream=False, returns the complete parsed JSON response object.
    """
    url = f"{settings.OLLAMA_HOST.rstrip('/')}/api/chat"
    payload = {
        "model": settings.OLLAMA_MODEL,
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
    with urllib.request.urlopen(req) as response:
        if stream:
            for line in response:
                line_str = line.strip()
                if line_str:
                    yield json.loads(line_str.decode("utf-8"))
        else:
            return json.loads(response.read().decode("utf-8"))


def extract_sentences_from_buffer(buffer: str) -> Tuple[List[str], str]:
    """
    Extracts complete sentences ending with '.', '!', '?', or newline from buffer.
    Returns (list_of_completed_sentences, remaining_buffer).
    """
    matches = list(re.finditer(r".*?[.!?\n]+(?:\s+|$)", buffer, flags=re.DOTALL))
    if not matches:
        return [], buffer

    sentences = []
    last_end = 0
    for match in matches:
        s = match.group(0).strip()
        if s:
            sentences.append(s)
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
        res = call_ollama(messages, tools=TOOLS, stream=True)
        if isinstance(res, dict):
            chunks = [res]
        else:
            chunks = res

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
            yield buffer.strip()
            buffer = ""

        assistant_msg: Dict[str, Any] = {"role": "assistant"}
        if full_content:
            assistant_msg["content"] = full_content
        if tool_calls_acc:
            assistant_msg["tool_calls"] = tool_calls_acc
        messages.append(assistant_msg)

        if full_content and logger_instance:
            logger_instance.log_llm_response(full_content)

        if tool_calls_acc:
            for tool_call in tool_calls_acc:
                fn = tool_call.get("function", {})
                tool_name = fn.get("name")
                tool_args = fn.get("arguments", {})
                if isinstance(tool_args, str):
                    tool_args = json.loads(tool_args)

                # Tool call -> await result -> append result to messages history -> THEN start next LLM generation
                result = execute_tool_call(tool_name, tool_args)
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
            return full_content, messages


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
