import concurrent.futures
import json
import re
import time
import urllib.request
import uuid
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
You are a voice appointment assistant for Medical Clinic speaking over a live phone connection. Your output will be read aloud by a TTS engine. Speak in clean, concise, 1-2 sentence spoken English — never emit code, markdown lists, bullet points, or machine formats.

RULE 1 — NEVER SPEAK MACHINE FORMATS OR EXAMPLE STRINGS
Never say ISO 8601 strings (e.g. YYYY-MM-DDTHH:MM:SSZ), format templates, or example date strings to the caller. Callers speak naturally; ask naturally (e.g. "What date works for you?").

RULE 2 — NEVER CALL A TOOL WITH INCOMPLETE OR AMBIGUOUS PARAMETERS
Before calling any tool (verify_identity, book_appointment, propose_booking, cancel_appointment, reschedule_appointment), ensure all required parameters are complete and unambiguous. If any piece is missing, ask specifically for only the missing piece.

RULE 3 — IDENTITY VERIFICATION IS STICKY
Once verify_identity returns verified=true, identity remains verified for the entire session. Do not ask for name or date of birth again.

RULE 4 — CONTEXTUAL AFFIRMATIONS
Treat short caller responses ("Yeah", "Sure", "Okay") as confirmation of the immediately preceding question. Use brief 1-sentence questions.

RULE 5 — SPOKEN TEXT HYGIENE
Ensure every sentence contains full words. Preserve natural contractions ("what's", "it's"). Never emit raw symbols or standalone punctuation.

RULE 6 — CLARIFY BEFORE ACTING
If input is garbled or ambiguous, ask a short clarifying question instead of guessing parameters.

RULE 7 — MUST EXECUTE TOOLS (NEVER PRETEND OR HALLUCINATE TOOL RESULTS)
You CANNOT verify, check, propose, book, confirm, cancel, or reschedule by speaking text alone. You MUST invoke the corresponding tool function (`verify_identity`, `check_availability`, `propose_booking`, `confirm_booking`, `book_appointment`, `cancel_appointment`, `reschedule_appointment`). NEVER say "Your appointment is booked" or "I've saved it to the database" without issuing the tool call and receiving a success response.

RULE 8 — NO PRE-NARRATION OF TOOL CALLS AND ERROR HANDLING
Do not speak filler text like "I am booking...", "Let me check the database...", or "I will call propose_booking" unless the tool call is issued in that exact turn.

RULE 9 — AUTOMATIC IDENTITY VERIFICATION WHEN NAME & DOB ARE PRESENT
As soon as both full name (first + last) and date of birth (month, day, year) are present in conversation history, invoke verify_identity(first_name, last_name, date_of_birth) IMMEDIATELY in that turn.

RULE 10 — NEVER CONFUSE PATIENT DATE OF BIRTH WITH APPOINTMENT BOOKING DATE
Patient Date of Birth is strictly for verify_identity. Appointments are booked in the present/future (current year 2026). Never use DOB as an appointment booking date.

RULE 11 — CLINIC DOCTOR DIRECTORY & VALID DOCTOR IDS
Active doctors: Dr. Smith (id: 1), Dr. Sarah Connor (id: 27), Dr. John Smith (id: 28), Dr. Emily Wong (id: 29), Dr. Michael Brown (id: 30). Pass exact integer doctor_id.

RULE 12 — CLINIC TIMEZONE
All working hours and appointment slots operate in Indian Standard Time (IST / UTC+05:30).

RULE 13 — CONCISE AVAILABILITY PRESENTATION
When listing availability from check_availability, state at most 2-3 options naturally (e.g. "We have 9:00 AM, 11:00 AM, or 2:00 PM available"). Never read out long lists.

RULE 14 — ALWAYS CONFIRM COMPLETED TOOL ACTIONS IMMEDIATELY
As soon as a tool call (such as cancel_appointment, book_appointment, confirm_booking, reschedule_appointment, or verify_identity) completes successfully, your VERY FIRST sentence in the immediate response MUST explicitly state and confirm the action result to the caller (e.g. "Your appointment with ID 3313 has been cancelled" or "Your appointment is confirmed for July 30th at 9:00 AM"). NEVER skip this confirmation or ask follow-up questions without confirming the completed action first.
"""


from app.api.services.scheduling import normalize_datetime_utterance


def resolve_doctor_id(args: Dict[str, Any]) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
    """
    Resolves doctor_id from args against live FastAPI /doctors endpoint.
    Handles integer IDs, string IDs, and doctor names (e.g. "Dr. Sarah Connor" -> 27).
    Returns (resolved_doctor_id, None) if resolved successfully.
    Returns (None, error_payload) if unresolvable.
    """
    try:
        res = client.get("/doctors")
        doctors = res.json() if res.status_code == 200 and isinstance(res.json(), list) else []
    except Exception as e:
        logger.warning(f"Failed to fetch /doctors list: {e}")
        doctors = []

    valid_ids = {}
    if doctors:
        for doc in doctors:
            if isinstance(doc, dict) and "id" in doc:
                valid_ids[doc["id"]] = doc.get("name", "")
    
    if not valid_ids:
        valid_ids = {
            1: "Dr. Smith",
            27: "Dr. Sarah Connor",
            28: "Dr. John Smith",
            29: "Dr. Emily Wong",
            30: "Dr. Michael Brown",
        }

    raw_id = args.get("doctor_id") or args.get("doctor") or args.get("provider")

    if isinstance(raw_id, int) and raw_id in valid_ids:
        return raw_id, None
    if isinstance(raw_id, str) and raw_id.isdigit():
        val_int = int(raw_id)
        if val_int in valid_ids:
            return val_int, None

    if isinstance(raw_id, str):
        clean_name = re.sub(r"\bDr\.?\b", "", raw_id, flags=re.IGNORECASE).strip().lower()
        for doc_id, doc_name in valid_ids.items():
            clean_doc = re.sub(r"\bDr\.?\b", "", doc_name, flags=re.IGNORECASE).strip().lower()
            if clean_name and (clean_name in clean_doc or clean_doc in clean_name):
                return doc_id, None

    doc_options = ", ".join([f"{name} (ID {did})" for did, name in valid_ids.items()])
    return None, {
        "status": "needs_clarification",
        "error": f"Doctor ID '{raw_id}' is invalid or not found in clinic records. The valid clinic doctors are: {doc_options}. Please specify one of these doctors.",
    }


def _execute_tool_call_internal(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executes a tool call against the local FastAPI application.
    Validates datetime parameters using normalize_datetime_utterance before calling scheduling APIs.
    """
    if name == "verify_identity":
        res = client.post("/verify-identity", json=args)
        return res.json()
    elif name == "check_availability":
        resolved_id, error_res = resolve_doctor_id(args)
        if error_res:
            return error_res
        doctor_id = resolved_id
        date_str = args.get("date")
        if date_str and re.search(r"\b(19\d{2}|20[0-1]\d|202[0-5])\b", str(date_str)):
            return {
                "status": "needs_clarification",
                "error": f"The date specified ({date_str}) matches a birth year or past date, not an appointment date. Appointments must be in the current year. Please ask the caller specifically what day or date they would like to book.",
            }
        if date_str and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(date_str)):
            iso_str, _ = normalize_datetime_utterance(f"{date_str} at 10 AM")
            if iso_str:
                date_str = iso_str.split("T")[0]
        res = client.get(f"/doctors/{doctor_id}/availability?date={date_str}")
        return res.json()
    elif name in ("propose_booking", "book_appointment", "reschedule_appointment"):
        resolved_id, error_res = resolve_doctor_id(args)
        if error_res:
            return error_res
        args["doctor_id"] = resolved_id
        raw_datetime = args.get("start_time") or args.get("new_start_time")
        if not raw_datetime:
            return {
                "status": "needs_clarification",
                "error": "Missing appointment date/time parameter. Please ask the caller specifically for the date and time.",
            }
        if re.search(r"\b(19\d{2}|20[0-1]\d|202[0-5])\b", str(raw_datetime)):
            return {
                "status": "needs_clarification",
                "error": f"The appointment date ({raw_datetime}) matches a birth year or past date. Appointments cannot be booked in the past. Please ask the caller specifically what day or date in the current year they would like to book.",
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


def execute_tool_call(name: str, args: Dict[str, Any], timeout: float = 15.0) -> Dict[str, Any]:
    """
    Executes a tool call with a hard 15-second timeout and logs telemetry:
    request payload, response payload, end-to-end latency (ms), and success/error status.
    """
    start_time = time.perf_counter()
    status_success = False
    result = None

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_execute_tool_call_internal, name, args)
            result = future.result(timeout=timeout)

        status_success = "error" not in result if isinstance(result, dict) else True
        return result
    except concurrent.futures.TimeoutError:
        result = {
            "status": "error",
            "error": f"Tool call '{name}' timed out after {timeout} seconds.",
        }
        raise TimeoutError(f"Tool call '{name}' timed out after {timeout} seconds.") from None
    except Exception as e:
        result = {"status": "error", "error": str(e)}
        raise
    finally:
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        logger.info(
            f"[TOOL TELEMETRY] name={name} | latency_ms={latency_ms:.2f} | success={status_success} | request={args} | response={result}"
        )


def _call_ollama_api(
    messages: List[Dict[str, Any]], tools: List[Dict[str, Any]] = None, stream: bool = True
) -> Any:
    model_name = getattr(settings, "OLLAMA_MODEL", None) or getattr(settings, "LLM_MODEL", "qwen2.5:7b-instruct-q4_K_M")
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
    api_base = settings.LLM_API_BASE
    # Guard against literal string "None" from .env
    if not api_base or api_base.strip().lower() == "none":
        api_base = "https://api.groq.com/openai/v1"
    url = f"{api_base.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ClinicVoiceAssistant/1.0",
    }
    if settings.LLM_API_KEY:
        headers["Authorization"] = f"Bearer {settings.LLM_API_KEY}"

    formatted_messages = []
    for msg in messages:
        m = dict(msg)
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            tcs = []
            for idx, tc in enumerate(m["tool_calls"]):
                fn = tc.get("function", {})
                args = fn.get("arguments", {})
                if isinstance(args, (dict, list)):
                    args_str = json.dumps(args)
                elif isinstance(args, str):
                    args_str = args
                else:
                    args_str = json.dumps(args) if args is not None else "{}"

                tcs.append({
                    "id": tc.get("id") or f"call_{idx}",
                    "type": "function",
                    "function": {
                        "name": fn.get("name", ""),
                        "arguments": args_str,
                    },
                })
            m["tool_calls"] = tcs
        elif role == "tool":
            if "tool_call_id" not in m:
                m["tool_call_id"] = m.get("id") or "call_0"
        formatted_messages.append(m)

    payload = {
        "model": getattr(settings, "LLM_MODEL", "llama-3.3-70b-versatile"),
        "messages": formatted_messages,
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
                # Accumulate tool call deltas across SSE chunks.
                # Groq/OpenAI send tool_calls as incremental index-based deltas:
                #   {"index": 0, "function": {"name": "check_...", "arguments": ""}}
                #   {"index": 0, "function": {"arguments": '{"doc'}}
                #   {"index": 0, "function": {"arguments": 'tor_id": 30}'}}
                accumulated_tool_calls: Dict[int, Dict[str, Any]] = {}

                for line in response:
                    line_str = line.decode("utf-8").strip()
                    if not line_str.startswith("data: "):
                        continue
                    data_content = line_str[6:].strip()
                    if data_content == "[DONE]":
                        break
                    parsed = json.loads(data_content)
                    choices = parsed.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})

                    # Yield text content chunks immediately for streaming TTS
                    content = delta.get("content", "") or ""
                    if content:
                        yield {"message": {"content": content, "tool_calls": []}}

                    # Accumulate tool call deltas by index
                    for tc_delta in delta.get("tool_calls", []):
                        idx = tc_delta.get("index", 0)
                        fn_delta = tc_delta.get("function", {})
                        if idx not in accumulated_tool_calls:
                            accumulated_tool_calls[idx] = {
                                "id": tc_delta.get("id", ""),
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        if tc_delta.get("id"):
                            accumulated_tool_calls[idx]["id"] = tc_delta["id"]
                        if fn_delta.get("name"):
                            accumulated_tool_calls[idx]["function"]["name"] = fn_delta["name"]
                        if fn_delta.get("arguments"):
                            accumulated_tool_calls[idx]["function"]["arguments"] += fn_delta["arguments"]

                # After stream ends, yield accumulated tool calls as a single final chunk
                if accumulated_tool_calls:
                    final_tool_calls = []
                    for idx in sorted(accumulated_tool_calls.keys()):
                        tc = accumulated_tool_calls[idx]
                        # Parse arguments string to dict
                        args_str = tc["function"]["arguments"]
                        try:
                            args = json.loads(args_str) if args_str else {}
                        except json.JSONDecodeError:
                            args = {}
                        final_tool_calls.append({
                            "id": tc.get("id") or f"call_{idx}",
                            "type": "function",
                            "function": {
                                "name": tc["function"]["name"],
                                "arguments": args,
                            },
                        })
                    yield {"message": {"content": "", "tool_calls": final_tool_calls}}
            else:
                parsed = json.loads(response.read().decode("utf-8"))
                choices = parsed.get("choices", [])
                msg = choices[0].get("message", {}) if choices else {}
                # Normalize non-streaming tool_calls to match internal format
                tool_calls = msg.get("tool_calls", [])
                normalized_tcs = []
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    args = fn.get("arguments", "{}")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    normalized_tcs.append({
                        "function": {"name": fn.get("name", ""), "arguments": args}
                    })
                return {"message": {"content": msg.get("content", ""), "tool_calls": normalized_tcs}}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        logger.error(f"[OpenAI-Compatible API HTTP {e.code}]: {body}")
        raise RuntimeError(f"OpenAI-Compatible API HTTP {e.code}: {body}") from e


def call_llm_api(
    messages: List[Dict[str, Any]], tools: List[Dict[str, Any]] = None, stream: bool = True
) -> Any:
    """
    Sends a chat completion request to the configured LLM provider (Ollama or Groq/OpenAI).
    Implements a 3-retry attempt loop with rate-limit-aware backoff for 429 errors.
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
                # Parse rate-limit retry hint from 429 errors (e.g. "try again in 1.77s")
                err_str = str(e)
                wait_time = 0.5 * attempt  # default backoff
                if "429" in err_str or "rate_limit" in err_str:
                    retry_match = re.search(r"try again in (\d+(?:\.\d+)?)\s*s", err_str, re.IGNORECASE)
                    if retry_match:
                        wait_time = max(float(retry_match.group(1)) + 0.2, wait_time)
                    else:
                        wait_time = max(2.0 * attempt, wait_time)  # longer default for rate limits
                    logger.info(f"[Rate limit] Waiting {wait_time:.1f}s before retry...")
                time.sleep(wait_time)

    raise RuntimeError(f"LLM API request failed after {max_retries} attempts: {last_exception}")


call_ollama = call_llm_api


def sanitize_for_tts(text: str, has_tool_success: bool = False) -> str:
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
    # Strip complete <function...>...</function> or <tool_call>...</tool_call> tags and raw function markup
    sanitized = re.sub(r"<function.*?>.*?</function>", "", sanitized, flags=re.DOTALL | re.IGNORECASE)
    sanitized = re.sub(r"</?function.*?>", "", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"<tool_call.*?>.*?</tool_call>", "", sanitized, flags=re.DOTALL | re.IGNORECASE)
    sanitized = re.sub(r"</?tool_call.*?>", "", sanitized, flags=re.IGNORECASE)
    # Strip ISO 8601 timestamps and ISO dates (e.g. 2026-08-03T10:00:00Z or 2026-08-03) from spoken output
    sanitized = re.sub(r"\b\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)?\b", "", sanitized)
    # Strip hallucinated tool narration e.g. "I will call check_availability now." or "calling propose_booking"
    sanitized = re.sub(
        r"\b(?:I will|I'll|let me|I am going to|calling)\s+(?:call\s+)?(?:check_availability|propose_booking|confirm_booking|book_appointment|verify_identity|cancel_appointment|reschedule_appointment)(?:\s+now)?\.?",
        "",
        sanitized,
        flags=re.IGNORECASE,
    )
    # Strip natural-language action narration that stalls the caller (no tool call behind it)
    sanitized = re.sub(
        r"\b(?:I'm just|I am just|I'm now|I am now|Let me just|Let me go ahead and)\s+(?:verifying|checking|looking|pulling up|confirming|processing|scheduling|booking|rescheduling|cancelling|canceling)[^.!?]*[.!?]?",
        "",
        sanitized,
        flags=re.IGNORECASE,
    )
    sanitized = re.sub(
        r"\b(?:I'll|I will|Let me)\s+(?:check|verify|look|pull up|confirm|process|schedule|book|reschedule|cancel)\s+(?:that|this|it)\s+(?:for you|now|right now|right away)[^.!?]*[.!?]?",
        "",
        sanitized,
        flags=re.IGNORECASE,
    )
    # Strip raw function/tool names if leaked e.g. check_availability, propose_booking
    sanitized = re.sub(
        r"\b(?:check_availability|propose_booking|confirm_booking|book_appointment|verify_identity|cancel_appointment|reschedule_appointment)\b",
        "",
        sanitized,
        flags=re.IGNORECASE,
    )
    # Universal Anti-Hallucination Guardrail:
    # If no tool call succeeded in this turn, strip hallucinated success confirmations across ALL tools.
    if not has_tool_success:
        pattern = r".*?(?:(?<!\bDr)(?<!\bMr)(?<!\bMrs)(?<!\bMs)(?<!\bProf)(?<!\bSt)[.!?]+(?:\s+|$))"
        matches = list(re.finditer(pattern, sanitized, flags=re.DOTALL))
        if matches:
            cleaned_sentences = []
            for m in matches:
                s = m.group(0).strip()
                s_lower = s.lower()
                if ("appointment" in s_lower or "booking" in s_lower) and any(
                    w in s_lower for w in ["confirmed", "booked", "cancelled", "rescheduled"]
                ):
                    continue
                if "identity" in s_lower and any(w in s_lower for w in ["verified", "confirmed"]):
                    continue
                cleaned_sentences.append(s)
            sanitized = " ".join(cleaned_sentences).strip()
        else:
            s_lower = sanitized.lower()
            if ("appointment" in s_lower or "booking" in s_lower) and any(
                w in s_lower for w in ["confirmed", "booked", "cancelled", "rescheduled"]
            ):
                sanitized = ""
            elif "identity" in s_lower and any(w in s_lower for w in ["verified", "confirmed"]):
                sanitized = ""

    # Strip trailing periods at sentence end so TTS engine does not speak literal "dot"
    sanitized = re.sub(r"\.+\s*$", "", sanitized)

    # Strip double quotes ("), backticks (`), and asterisks (*)
    sanitized = re.sub(r"[\"`*]", "", sanitized)
    # Strip standalone or outer single quotes not part of a contraction word
    sanitized = re.sub(r"(?<!\w)'|'(?!\w)", "", sanitized)
    # Strip trailing periods at sentence end so TTS engine does not speak literal "dot"
    sanitized = re.sub(r"\.+\s*$", "", sanitized)
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


def extract_sentences_from_buffer(buffer: str, is_final: bool = True) -> Tuple[List[str], str]:
    """
    Extracts complete sentences ending with '.', '!', '?', or newline from buffer.
    Returns (list_of_completed_sentences, remaining_buffer).
    Sanitizes each sentence, strips <think> reasoning blocks, drops punctuation-only fragments,
    and merges short 1-2 word intro exclamations (e.g. "Great!", "Sure!") into the following sentence.
    Does NOT break or mangle title abbreviations (e.g. "Dr.", "Mr.").
    When is_final is False, holds back a trailing short 1-2 word exclamation so it can be merged
    with the following sentence when the next chunk arrives.
    """
    # Remove complete <think>...</think> and <function...>...</function> blocks from buffer
    buffer = re.sub(r"<think>.*?</think>", "", buffer, flags=re.DOTALL)
    buffer = re.sub(r"<function.*?>.*?</function>", "", buffer, flags=re.DOTALL | re.IGNORECASE)
    buffer = re.sub(r"<tool_call.*?>.*?</tool_call>", "", buffer, flags=re.DOTALL | re.IGNORECASE)

    # If buffer is currently inside an unclosed <think> or <function> block, wait for closing tag
    if "<think>" in buffer.lower() and "</think>" not in buffer.lower():
        return [], buffer
    if "<function" in buffer.lower() and "</function>" not in buffer.lower():
        return [], buffer
    if "<tool_call" in buffer.lower() and "</tool_call>" not in buffer.lower():
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

    # If streaming (is_final=False) and the only sentence extracted so far is a short 1-2 word intro exclamation,
    # hold it back in the remaining buffer so it merges smoothly with the next sentence.
    abbrevs = {"dr", "mr", "mrs", "ms", "prof", "st"}
    if not is_final and len(sentences) == 1:
        words = sentences[0].split()
        clean_curr_lower = re.sub(r"[.!?]+$", "", sentences[0].lower())
        if len(words) <= 2 and clean_curr_lower not in abbrevs:
            return [], buffer

    # Merge very short 1-2 word intro exclamations (e.g. "Great!", "Sure!") with the next sentence,
    # skipping title abbreviations (e.g. "Dr.", "Mr.")
    merged = []
    i = 0
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
        elif not is_final and i == len(sentences) - 1 and len(words) <= 2 and clean_curr_lower not in abbrevs:
            last_end = buffer.rfind(curr)
            break
        else:
            merged.append(curr)
            i += 1

    remaining = buffer[last_end:]
    return merged, remaining


def parse_spoken_dob(text: str) -> Optional[str]:
    """
    Parses a date of birth from user spoken text (e.g. "August 22, 1992", "22 August 1992", "1992-08-22").
    Returns YYYY-MM-DD string or None.
    """
    if not text:
        return None
    from dateutil import parser
    m = re.search(
        r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b",
        text,
        re.I,
    )
    if not m:
        m = re.search(
            r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:January|February|March|April|May|June|July|August|September|October|November|December),?\s+\d{4}\b",
            text,
            re.I,
        )
    if not m:
        m = re.search(r"\b(19\d{2}|20[0-2]\d)-\d{2}-\d{2}\b", text)

    if m:
        try:
            clean_str = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", m.group(0), flags=re.I)
            dt = parser.parse(clean_str)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    return None


def _try_auto_verify_identity_fallback(messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Auto-execution fallback: If identity has not been verified yet, but both full name
    and date of birth exist in recent user messages, execute verify_identity directly.
    """
    for m in messages:
        if m.get("role") == "tool":
            content_str = str(m.get("content", ""))
            if "verified" in content_str and "true" in content_str.lower():
                return None
        if m.get("role") == "assistant" and "tool_calls" in m:
            for tc in m["tool_calls"]:
                if tc.get("function", {}).get("name") == "verify_identity":
                    return None

    user_msgs = [m.get("content", "") for m in messages if m.get("role") == "user"]
    if not user_msgs:
        return None

    recent_user_text = " ".join(user_msgs[-2:])
    dob_iso = parse_spoken_dob(recent_user_text)
    if not dob_iso:
        return None

    last_user_msg = user_msgs[-1].strip()
    name_match = re.search(
        r"\b(?:my name is|I am|I'm|this is)?\s*([A-Za-z_0-9-]+)\s+([A-Za-z_0-9-]+)\b",
        last_user_msg,
    )
    if not name_match:
        name_match = re.search(
            r"\b(?:my name is|I am|I'm|this is)?\s*([A-Za-z_0-9-]+)\s+([A-Za-z_0-9-]+)\b",
            recent_user_text,
        )

    if name_match:
        f_name = name_match.group(1)
        l_name = name_match.group(2)
        reserved_words = {"Who", "What", "When", "Where", "How", "Yes", "No", "Is", "The", "Not", "Yeah", "Dr", "Doctor", "Check", "Book", "Cancel", "Reschedule"}
        if f_name in reserved_words or l_name in reserved_words:
            return None

        args = {"first_name": f_name, "last_name": l_name, "date_of_birth": dob_iso}
        res = execute_tool_call("verify_identity", args)
        if isinstance(res, dict) and res.get("verified") is True:
            logger.info(f"[AUTO-VERIFY FALLBACK EXECUTED] Verified {f_name} {l_name} ({dob_iso})")
            return {
                "function": {
                    "name": "verify_identity",
                    "arguments": args,
                },
                "_auto_result": res,
            }
    return None


_NARRATION_PATTERNS = [
    # "I'm checking/verifying/booking..." patterns
    re.compile(
        r"\b(?:I'm|I am|i'm|i am)\s+(?:just\s+)?(?:checking|verifying|booking|confirming|looking|pulling|scheduling|rescheduling|cancelling|canceling|processing)",
        re.IGNORECASE,
    ),
    # "I'll/I will/Let me check/verify/book..." patterns
    re.compile(
        r"\b(?:I'll|I will|Let me|Let's)\s+(?:just\s+)?(?:check|verify|book|confirm|look|pull|schedule|reschedule|cancel|process|go ahead|proceed|finalize)",
        re.IGNORECASE,
    ),
    # "checking/verifying for you now" patterns
    re.compile(
        r"\b(?:checking|verifying|booking|confirming|looking)\s+.*?\b(?:for you|now|right now|right away)",
        re.IGNORECASE,
    ),
    # "I will check the availability" / "I will call check_availability"
    re.compile(
        r"\b(?:I will|I'll)\s+(?:call\s+)?(?:check_availability|propose_booking|confirm_booking|book_appointment|verify_identity)",
        re.IGNORECASE,
    ),
]


def _detect_narrated_tool_intent(text: str) -> bool:
    """
    Returns True if the LLM's text response indicates it intended to call a tool
    (e.g. "I'm checking availability now", "Let me verify your identity")
    but did NOT actually emit a structured tool call.
    """
    if not text:
        return False
    clean_text = text.strip()
    # If the LLM is asking a question (e.g. "Is that correct?", "Which doctor?"),
    # it is asking the caller for input/confirmation, not stalling with fake narration.
    if clean_text.endswith("?") or "?" in clean_text[-60:]:
        return False
    for pattern in _NARRATION_PATTERNS:
        if pattern.search(clean_text):
            logger.debug(f"[NARRATION DETECTOR] Matched pattern: {pattern.pattern}")
            return True
    return False


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

    narration_retries = 0  # Track re-prompts for narrated-but-not-called tool actions

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
        has_yielded_sentences = False

        for chunk in chunks:
            msg = chunk.get("message", {})
            delta_content = msg.get("content", "")
            if delta_content:
                full_content += delta_content
                buffer += delta_content
                sentences, buffer = extract_sentences_from_buffer(buffer, is_final=False)
                for sentence in sentences:
                    has_yielded_sentences = True
                    yield sentence

            delta_tool_calls = msg.get("tool_calls", [])
            if delta_tool_calls:
                tool_calls_acc.extend(delta_tool_calls)

        if not tool_calls_acc:
            auto_tc = _try_auto_verify_identity_fallback(messages)
            if auto_tc:
                tool_calls_acc.append(auto_tc)

        if buffer.strip() and not tool_calls_acc:
            sentences, buffer = extract_sentences_from_buffer(buffer, is_final=True)
            for sentence in sentences:
                has_yielded_sentences = True
                yield sentence
            if buffer.strip():
                sanitized_buf = sanitize_for_tts(buffer.strip())
                if sanitized_buf:
                    has_yielded_sentences = True
                    yield sanitized_buf
                buffer = ""

        assistant_msg: Dict[str, Any] = {"role": "assistant"}
        clean_full_content = sanitize_for_tts(full_content)
        if clean_full_content:
            assistant_msg["content"] = clean_full_content
        if tool_calls_acc:
            clean_tool_calls = []
            for tc in tool_calls_acc:
                fn = tc.get("function", {})
                t_name = fn.get("name")
                t_args = fn.get("arguments", {})
                if isinstance(t_args, str):
                    try:
                        t_args = json.loads(t_args)
                    except Exception:
                        pass
                clean_tool_calls.append({
                    "function": {
                        "name": t_name,
                        "arguments": t_args if isinstance(t_args, dict) else {},
                    }
                })
            assistant_msg["tool_calls"] = clean_tool_calls
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

                # Retry tool call up to 2 times total
                result = None
                call_failed = False
                for attempt in range(1, 3):
                    try:
                        result = execute_tool_call(tool_name, tool_args, timeout=15.0)
                        if isinstance(result, dict) and "error" in result and result.get("status") not in ("needs_clarification",):
                            call_failed = True
                            if attempt < 2:
                                time.sleep(0.5 * attempt)
                                continue
                        else:
                            call_failed = False
                            break
                    except Exception as te:
                        logger.warning(f"[Tool Call '{tool_name}' Attempt {attempt}/2 Failed]: {te}")
                        call_failed = True
                        if attempt == 2:
                            result = {
                                "status": "error",
                                "error": "I'm having trouble completing this right now; let me connect you with a human assistant.",
                            }
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

                if call_failed or (isinstance(result, dict) and result.get("status") == "error"):
                    escalation_msg = "I'm having trouble completing this right now; let me connect you with a human assistant."
                    if logger_instance:
                        logger_instance.log_llm_response(escalation_msg)
                    yield escalation_msg
                    messages.append({"role": "assistant", "content": escalation_msg})
                    return escalation_msg, messages
            continue
        else:
            # Detect if the LLM narrated a tool action without actually calling it.
            # ONLY re-prompt if we have NOT yielded any sentences to TTS yet in this turn,
            # to prevent streaming duplicate speech run-ons.
            if narration_retries < 2 and not has_yielded_sentences and _detect_narrated_tool_intent(full_content):
                logger.warning(
                    f"[NARRATION DETECTED] LLM narrated a tool action without calling it "
                    f"(retry {narration_retries + 1}/2): {full_content[:200]}"
                )
                narration_retries += 1
                # Inject a system nudge to force the LLM to make the actual tool call
                messages.append({
                    "role": "user",
                    "content": (
                        "You just said you would perform an action (like checking availability, "
                        "booking, or verifying) but you did NOT call any tool function. "
                        "Do NOT narrate what you will do — actually call the appropriate tool "
                        "function NOW with the correct parameters. If you are missing required "
                        "parameters, ask the caller for them instead of narrating."
                    ),
                })
                continue  # Loop back to call the LLM again
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
