from datetime import datetime, timezone
from typing import Optional
from fastapi import HTTPException
from psycopg2.extras import DateTimeTZRange
from sqlalchemy.orm import Session

from app.db.models import Appointment, AuditLog, Patient


def create_appointment(
    db: Session,
    doctor_id: int,
    patient_id: int,
    start_time: datetime,
    end_time: datetime,
    reason: Optional[str] = None,
) -> Appointment:
    """
    Creates an appointment for a verified patient_id.
    
    Enforces:
    1. Identity verification check (must have an allowed 'verify_identity' row in audit_log).
    2. Concurrency-safe booking via DB EXCLUDE constraint (returns 409 on overlap).
    3. Working-hours check via DB trigger (returns 400 on working-hours violation).
    4. Audit log entry written for every attempt (success or rejection).
    """
    # Check if patient exists in DB to safely reference in audit_log foreign key constraint
    patient_exists = db.query(Patient.id).filter(Patient.id == patient_id).first()
    audit_patient_id = patient_id if patient_exists else None

    # 1. Verification check: patient_id must have a successful verify_identity audit log
    verified_log = (
        db.query(AuditLog)
        .filter(
            AuditLog.patient_id == patient_id,
            AuditLog.action == "verify_identity",
            AuditLog.allowed == True,
        )
        .first()
    )
    if not verified_log:
        audit = AuditLog(
            patient_id=audit_patient_id,
            action="book_appointment",
            allowed=False,
            details={
                "doctor_id": doctor_id,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "error": "Unverified patient identity",
            },
        )
        db.add(audit)
        db.commit()
        raise HTTPException(
            status_code=403,
            detail="Patient identity must be verified prior to booking an appointment.",
        )

    # Ensure timezone awareness
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)
    if end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=timezone.utc)

    appointment_range = DateTimeTZRange(start_time, end_time)
    appt = Appointment(
        doctor_id=doctor_id,
        patient_id=patient_id,
        appointment_range=appointment_range,
        status="booked",
        reason=reason,
    )
    db.add(appt)
    try:
        db.flush()
    except Exception as e:
        db.rollback()
        err_msg = str(e)
        if (
            "exclusion constraint" in err_msg.lower()
            or "conflicts with existing" in err_msg.lower()
            or "exclusionviolation" in err_msg.lower()
        ):
            status_code = 409
            detail = "That slot is no longer available"
        elif (
            "working hours" in err_msg.lower()
            or "does not work" in err_msg.lower()
            or "raiseexception" in err_msg.lower()
        ):
            status_code = 400
            detail = "Appointment is outside doctor working hours"
        else:
            status_code = 400
            detail = f"Booking failed: {err_msg}"

        audit = AuditLog(
            patient_id=audit_patient_id,
            action="book_appointment",
            allowed=False,
            details={
                "doctor_id": doctor_id,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "error": detail,
            },
        )
        db.add(audit)
        db.commit()
        raise HTTPException(status_code=status_code, detail=detail)

    audit = AuditLog(
        patient_id=audit_patient_id,
        action="book_appointment",
        allowed=True,
        details={
            "doctor_id": doctor_id,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "appointment_id": appt.id,
        },
    )
    db.add(audit)
    db.commit()
    db.refresh(appt)
    return appt
