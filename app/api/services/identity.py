from datetime import date
from typing import Optional, Tuple, Union
from dateutil import parser
from loguru import logger
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import AuditLog, Patient


import re

def _normalize_string(val: str) -> str:
    if not isinstance(val, str):
        return ""
    # Strip leading and trailing whitespace, quotes, and punctuation
    cleaned = re.sub(r"^['\"`\s.,!?:;]+|['\"`\s.,!?:;]+$", "", val)
    # Collapse internal whitespace
    return re.sub(r"\s+", " ", cleaned).strip()


def verify_patient_identity(
    db: Session,
    first_name: str,
    last_name: str,
    date_of_birth: Union[date, str],
) -> Tuple[bool, Optional[int], str]:
    """
    Verifies a caller's identity against the patients table using case-insensitive match
    on first_name, last_name, and date_of_birth, after stripping leading/trailing whitespace
    and punctuation/quotes.

    Writes an audit log entry for every verification attempt (allowed=True on match, allowed=False on mismatch).

    Returns:
        (verified: bool, patient_id: Optional[int], message: str)
    """
    clean_first = _normalize_string(first_name)
    clean_last = _normalize_string(last_name)

    parsed_dob = None
    if isinstance(date_of_birth, date):
        parsed_dob = date_of_birth
        raw_dob_str = date_of_birth.isoformat()
    elif isinstance(date_of_birth, str):
        clean_dob_str = _normalize_string(date_of_birth)
        raw_dob_str = clean_dob_str
        try:
            parsed_dob = parser.parse(clean_dob_str).date()
        except Exception:
            logger.warning(f"DOB unparseable: {clean_dob_str}")
            parsed_dob = None
    else:
        raw_dob_str = str(date_of_birth)

    if parsed_dob is None:
        patient = None
    else:
        patient = (
            db.query(Patient)
            .filter(
                func.lower(Patient.first_name) == clean_first.lower(),
                func.lower(Patient.last_name) == clean_last.lower(),
                Patient.date_of_birth == parsed_dob,
            )
            .first()
        )

    if patient:
        audit = AuditLog(
            patient_id=patient.id,
            action="verify_identity",
            allowed=True,
            details={
                "first_name": clean_first,
                "last_name": clean_last,
                "date_of_birth": parsed_dob.isoformat() if hasattr(parsed_dob, "isoformat") else str(parsed_dob),
            },
        )
        db.add(audit)
        db.commit()
        return True, patient.id, "Identity verified successfully."
    else:
        audit = AuditLog(
            patient_id=None,
            action="verify_identity",
            allowed=False,
            details={
                "first_name": clean_first,
                "last_name": clean_last,
                "date_of_birth": parsed_dob.isoformat() if (parsed_dob and hasattr(parsed_dob, "isoformat")) else raw_dob_str,
            },
        )
        db.add(audit)
        db.commit()
        return (
            False,
            None,
            "Identity verification failed. Information provided does not match our records.",
        )
