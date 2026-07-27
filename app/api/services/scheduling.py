from datetime import datetime, timezone
import re
from typing import Optional, Tuple
import dateparser
from loguru import logger

SPOKEN_NUMBERS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
    "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18", "nineteen": "19",
    "twenty": "20", "thirty": "30", "forty": "40", "fifty": "50", "sixty": "60",
    "seventy": "70", "eighty": "80", "ninety": "90"
}

MONTH_NAMES = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec"
]

DAY_NAMES = [
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun"
]

RELATIVE_DATE_WORDS = ["today", "tomorrow", "yesterday", "next", "this", "coming"]


def _preprocess_spoken_datetime(text: str) -> str:
    if not isinstance(text, str):
        return ""
    # Strip ordinal suffixes e.g. 28th -> 28
    cleaned = re.sub(r"\b(\d+)(st|nd|rd|th)\b", r"\1", text, flags=re.IGNORECASE)
    
    # Standardize time phrases
    cleaned = re.sub(r"\b(in the morning)\b", "AM", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(in the afternoon|in the evening)\b", "PM", cleaned, flags=re.IGNORECASE)

    # Convert spoken words "next Tuesday" -> "Tuesday" so dateparser PREFER_DATES_FROM='future' handles it
    cleaned = re.sub(
        r"\b(next|this|coming)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        r"\2",
        cleaned,
        flags=re.IGNORECASE,
    )
    
    # Replace spoken numbers for time e.g. "at ten" -> "at 10"
    for word, num in SPOKEN_NUMBERS.items():
        cleaned = re.sub(rf"\bat\s+{word}\b", f"at {num}", cleaned, flags=re.IGNORECASE)

    return cleaned.strip()


def _has_time_component(text: str) -> bool:
    lower = text.lower()
    if re.search(r"\b(?:am|pm|noon|midnight|o'clock|morning|afternoon|evening|night)\b", lower):
        return True
    if re.search(r"(?:t|\b)\d{1,2}:\d{2}", lower):
        return True
    if re.search(r"\bat\s+(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b", lower):
        return True
    return False


def _has_date_component(text: str) -> bool:
    lower = text.lower()
    # Check full ISO pattern YYYY-MM-DD
    if re.search(r"\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}", lower):
        return True
    if re.search(r"\b\d{1,2}[-/.]\d{1,2}(?:[-/.]\d{2,4})?", lower):
        return True

    # Strip time phrases to avoid matching hours as day numbers
    stripped_text = re.sub(
        r"\b(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm|in the morning|in the afternoon|in the evening)?\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    stripped_text = re.sub(r"\b(?:am|pm|noon|midnight|o'clock)\b", "", stripped_text, flags=re.IGNORECASE)
    lower_stripped = stripped_text.lower()

    if any(m in lower_stripped for m in MONTH_NAMES):
        return True
    if any(d in lower_stripped for d in DAY_NAMES):
        return True
    if any(r in lower_stripped for r in RELATIVE_DATE_WORDS):
        return True
    if re.search(r"\b(?:the\s+)?\d{1,2}(?:st|nd|rd|th)?\b", lower_stripped):
        return True
    return False


def normalize_datetime_utterance(
    utterance: str,
    reference_datetime: Optional[datetime] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Normalizes natural language date/time utterances (e.g. "November 28th at 10 AM",
    "next Tuesday at 10 in the morning") into ISO 8601 strings ("2026-11-28T10:00:00Z").

    Returns:
        (iso_8601_string, None) if fully resolved.
        (None, error_reason) if unparseable, incomplete, or ambiguous.
    """
    if not utterance or not utterance.strip():
        return None, "Empty date/time utterance"

    raw_text = utterance.strip()
    
    # Check if date and time components are present
    has_date = _has_date_component(raw_text)
    has_time = _has_time_component(raw_text)

    if not has_date and not has_time:
        return None, "Unparseable date/time utterance: missing both date and time"
    if not has_date:
        return None, "Missing date component: please specify the day or date"
    if not has_time:
        return None, "Missing time component: please specify the time of day"

    preprocessed = _preprocess_spoken_datetime(raw_text)

    ref_dt = reference_datetime or datetime.now(timezone.utc)
    ref_base = ref_dt.replace(tzinfo=None) if ref_dt.tzinfo else ref_dt

    try:
        parsed = dateparser.parse(
            preprocessed,
            settings={
                "RELATIVE_BASE": ref_base,
                "PREFER_DATES_FROM": "future",
                "RETURN_AS_TIMEZONE_AWARE": False,
            },
        )
    except Exception as e:
        logger.warning(f"Error parsing datetime utterance '{utterance}': {e}")
        parsed = None

    if parsed is None:
        return None, f"Unparseable date/time utterance: '{utterance}'"

    # Make aware as UTC
    parsed_utc = parsed.replace(tzinfo=timezone.utc)
    iso_str = parsed_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    return iso_str, None
