from datetime import date
from typing import Optional, Tuple
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import AuditLog, Patient


def verify_patient_identity(
    db: Session,
    first_name: str,
    last_name: str,
    date_of_birth: date,
) -> Tuple[bool, Optional[int], str]:
    """
    Verifies a caller's identity against the patients table using case-insensitive match
    on first_name, last_name, and date_of_birth.

    Writes an audit log entry for every verification attempt (allowed=True on match, allowed=False on mismatch).

    Returns:
        (verified: bool, patient_id: Optional[int], message: str)
    """
    clean_first = first_name.strip()
    clean_last = last_name.strip()

    patient = (
        db.query(Patient)
        .filter(
            func.lower(Patient.first_name) == clean_first.lower(),
            func.lower(Patient.last_name) == clean_last.lower(),
            Patient.date_of_birth == date_of_birth,
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
                "date_of_birth": date_of_birth.isoformat(),
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
                "date_of_birth": date_of_birth.isoformat(),
            },
        )
        db.add(audit)
        db.commit()
        return (
            False,
            None,
            "Identity verification failed. Information provided does not match our records.",
        )
