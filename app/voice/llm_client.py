import json
import urllib.request
from typing import Any, Dict, List, Tuple

from fastapi.testclient import TestClient
from app.api.config import get_settings
from app.api.main import app
from app.voice.tools import TOOLS

settings = get_settings()
client = TestClient(app)

SYSTEM_PROMPT = (
    "You are a helpful voice appointment assistant for a medical clinic.\n"
    "CRITICAL SAFETY & PRIVACY RULE: You MUST verify the patient's identity "
    "(using the `verify_identity` tool with first_name, last_name, and date_of_birth) "
    "BEFORE returning any patient-specific data or performing any booking, cancellation, "
    "or rescheduling actions.\n\n"
    "Available tools:\n"
    "1. `verify_identity`: Verify patient identity using first_name, last_name, date_of_birth.\n"
    "2. `check_availability`: Retrieve open 30-minute appointment slots for a doctor on a date.\n"
    "3. `book_appointment`: Book an appointment slot for a verified patient.\n"
    "4. `cancel_appointment`: Cancel an appointment for a verified patient.\n"
    "5. `reschedule_appointment`: Reschedule an appointment for a verified patient."
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


def call_ollama(messages: List[Dict[str, Any]], tools: List[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Sends a chat completion request to the local Ollama instance.
    """
    url = f"{settings.OLLAMA_HOST.rstrip('/')}/api/chat"
    payload = {
        "model": settings.OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode("utf-8"))


def run_chat_turn(
    history: List[Dict[str, Any]], user_message: str
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Runs a single turn of conversation with tool calling execution.
    Appends system prompt if history is empty.
    If the model emits a tool call, executes it via FastAPI and calls Ollama again with tool result.
    Returns (assistant_response_text, updated_history).
    """
    messages = list(history)
    if not messages or messages[0].get("role") != "system":
        messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})

    messages.append({"role": "user", "content": user_message})

    # Call Ollama model with tools
    response = call_ollama(messages, tools=TOOLS)
    msg = response.get("message", {})
    messages.append(msg)

    tool_calls = msg.get("tool_calls", [])
    if tool_calls:
        for tool_call in tool_calls:
            fn = tool_call.get("function", {})
            tool_name = fn.get("name")
            tool_args = fn.get("arguments", {})
            if isinstance(tool_args, str):
                tool_args = json.loads(tool_args)

            result = execute_tool_call(tool_name, tool_args)
            messages.append(
                {
                    "role": "tool",
                    "content": json.dumps(result),
                    "name": tool_name,
                }
            )

        # Round-trip call back to Ollama with tool result
        final_response = call_ollama(messages, tools=TOOLS)
        final_msg = final_response.get("message", {})
        messages.append(final_msg)
        return final_msg.get("content", ""), messages
    else:
        return msg.get("content", ""), messages
