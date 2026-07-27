import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
LOG_DIR = Path(__file__).parent.parent.parent / "logs" / "conversations"


def redact_pii_string(text: str) -> str:
    """
    Redacts DOB patterns (YYYY-MM-DD) to '[DOB provided]' in text.
    """
    if not isinstance(text, str):
        return text

    # Redact YYYY-MM-DD dates
    text = re.sub(r"\b(19|20)\d{2}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b", "[DOB provided]", text)
    return text


def redact_tool_args(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Returns a copy of tool arguments with DOB redacted to '[DOB provided]'
    and last_name redacted to initial (e.g. 'S.').
    """
    if not isinstance(args, dict):
        return args

    redacted = dict(args)
    if "date_of_birth" in redacted:
        redacted["date_of_birth"] = "[DOB provided]"
    if "last_name" in redacted and redacted["last_name"]:
        redacted["last_name"] = f"{redacted['last_name'][0]}."
    return redacted


class ConversationLogger:
    """
    Logger that creates one log file per call session with IST timestamps
    and redacted PII (DOB and last name).
    """

    def __init__(self, call_id: Optional[str] = None):
        self.call_id = call_id or uuid.uuid4().hex[:8]
        now_ist = datetime.now(IST)
        timestamp_str = now_ist.strftime("%Y-%m-%dT%H-%M-%S")
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.filepath = LOG_DIR / f"{timestamp_str}_{self.call_id}.log"
        self._write_line(f"[CALL START] Session ID: {self.call_id}")

    def _get_ist_time(self) -> str:
        return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S %Z")

    def _write_line(self, line: str):
        with open(self.filepath, "a", encoding="utf-8") as f:
            f.write(f"[{self._get_ist_time()}] {line}\n")

    def log_stt(self, transcript: str):
        redacted = redact_pii_string(transcript)
        self._write_line(f"[STT] Transcribed user audio: '{redacted}'")

    def log_llm_response(self, response_text: str):
        redacted = redact_pii_string(response_text)
        self._write_line(f"[LLM] Assistant response: '{redacted}'")

    def log_tool_call(self, tool_name: str, args: Dict[str, Any], result: Dict[str, Any]):
        redacted_args = redact_tool_args(args)
        status = "FAILED" if (isinstance(result, dict) and "error" in result) else "SUCCESS"
        redacted_result = redact_pii_string(str(result))
        self._write_line(
            f"[TOOL CALL] name={tool_name} status={status} args={redacted_args} result={redacted_result}"
        )
