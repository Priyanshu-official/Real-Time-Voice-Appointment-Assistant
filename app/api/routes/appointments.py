import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from psycopg2.extras import DateTimeTZRange

from app.api.services.booking import (
    cancel_appointment,
    create_appointment,
    reschedule_appointment,
)
from app.db.models import Appointment, AuditLog, Doctor, Patient
from app.db.session import get_db

router = APIRouter(prefix="/appointments", tags=["appointments"])

CONFIRMATION_TOKENS: Dict[str, dict] = {}


class ProposeBookingRequest(BaseModel):
    patient_id: int
    doctor_id: int
    start_time: datetime
    end_time: datetime
    reason: Optional[str] = None


class ProposeBookingResponse(BaseModel):
    status: str
    confirmation_token: str
    summary: str
    expires_in_seconds: int


class ConfirmBookingRequest(BaseModel):
    confirmation_token: str


class CreateAppointmentRequest(BaseModel):
    patient_id: int
    doctor_id: int
    start_time: datetime
    end_time: datetime
    reason: Optional[str] = None


class RescheduleAppointmentRequest(BaseModel):
    patient_id: int
    doctor_id: int
    start_time: datetime
    end_time: datetime
    reason: Optional[str] = None


class AppointmentResponse(BaseModel):
    id: int
    doctor_id: int
    patient_id: int
    start_time: str
    end_time: str
    status: str
    reason: Optional[str] = None


class AppointmentDetailedResponse(BaseModel):
    id: int
    doctor_id: int
    doctor_name: str
    patient_id: int
    patient_name: str
    start_time: str
    end_time: str
    status: str
    reason: Optional[str] = None


@router.get("", response_model=List[AppointmentDetailedResponse])
def get_upcoming_appointments(
    db: Session = Depends(get_db),
):
    appts = (
        db.query(Appointment, Doctor, Patient)
        .join(Doctor, Appointment.doctor_id == Doctor.id)
        .join(Patient, Appointment.patient_id == Patient.id)
        .filter(Appointment.status != "cancelled")
        .order_by(Appointment.appointment_range)
        .all()
    )
    results = []
    for appt, doc, pat in appts:
        doc_name = doc.name if doc.name.startswith("Dr.") else f"Dr. {doc.name}"
        pat_name = f"{pat.first_name} {pat.last_name}"
        results.append(
            AppointmentDetailedResponse(
                id=appt.id,
                doctor_id=doc.id,
                doctor_name=doc_name,
                patient_id=pat.id,
                patient_name=pat_name,
                start_time=appt.appointment_range.lower.isoformat(),
                end_time=appt.appointment_range.upper.isoformat(),
                status=appt.status,
                reason=appt.reason,
            )
        )
    return results


@router.post("/propose", response_model=ProposeBookingResponse)
def propose_booking(
    payload: ProposeBookingRequest,
    db: Session = Depends(get_db),
):
    # 1. Verification check: patient_id must have a successful verify_identity audit log
    verified_log = (
        db.query(AuditLog)
        .filter(
            AuditLog.patient_id == payload.patient_id,
            AuditLog.action == "verify_identity",
            AuditLog.allowed == True,
        )
        .first()
    )
    if not verified_log:
        raise HTTPException(
            status_code=403,
            detail="Patient identity must be verified prior to proposing an appointment.",
        )

    # 2. Check doctor exists
    doc = db.query(Doctor).filter(Doctor.id == payload.doctor_id).first()
    if not doc:
        raise HTTPException(
            status_code=404, detail=f"Doctor with id {payload.doctor_id} not found."
        )

    # 3. Check patient exists
    pat = db.query(Patient).filter(Patient.id == payload.patient_id).first()
    if not pat:
        raise HTTPException(
            status_code=404, detail=f"Patient with id {payload.patient_id} not found."
        )

    start_time = payload.start_time
    end_time = payload.end_time
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)
    if end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=timezone.utc)

    # 4. Working-hours check
    dow = int(start_time.strftime("%w"))
    if dow not in doc.working_days:
        raise HTTPException(
            status_code=400, detail="Appointment is outside doctor working hours"
        )
    if start_time.time() < doc.working_start_time or end_time.time() > doc.working_end_time:
        raise HTTPException(
            status_code=400, detail="Appointment is outside doctor working hours"
        )

    # 5. Overlap / availability check
    req_range = DateTimeTZRange(start_time, end_time)
    overlapping = (
        db.query(Appointment)
        .filter(
            Appointment.doctor_id == payload.doctor_id,
            Appointment.status != "cancelled",
            Appointment.appointment_range.op("&&")(req_range),
        )
        .first()
    )
    if overlapping:
        raise HTTPException(
            status_code=409, detail="That slot is no longer available"
        )

    token = uuid.uuid4().hex
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=120)
    CONFIRMATION_TOKENS[token] = {
        "patient_id": payload.patient_id,
        "doctor_id": payload.doctor_id,
        "start_time": start_time,
        "end_time": end_time,
        "reason": payload.reason,
        "expires_at": expires_at,
    }

    doc_name = doc.name if doc.name.startswith("Dr.") else f"Dr. {doc.name}"
    date_str = start_time.strftime("%Y-%m-%d")
    time_str = f"{start_time.strftime('%H:%M')} to {end_time.strftime('%H:%M')} UTC"
    summary = f"Proposed appointment with {doc_name} on {date_str} at {time_str}."

    return ProposeBookingResponse(
        status="proposed",
        confirmation_token=token,
        summary=summary,
        expires_in_seconds=120,
    )


@router.post("/confirm", response_model=AppointmentResponse)
def confirm_booking(
    payload: ConfirmBookingRequest,
    db: Session = Depends(get_db),
):
    token_data = CONFIRMATION_TOKENS.get(payload.confirmation_token)
    if not token_data:
        raise HTTPException(
            status_code=400,
            detail="Confirmation token is missing, expired, or already used.",
        )

    now = datetime.now(timezone.utc)
    if now > token_data["expires_at"]:
        CONFIRMATION_TOKENS.pop(payload.confirmation_token, None)
        raise HTTPException(
            status_code=400,
            detail="Confirmation token has expired (2-minute limit). Please request to propose the booking again.",
        )

    # Pop token so it cannot be reused
    CONFIRMATION_TOKENS.pop(payload.confirmation_token, None)

    appt = create_appointment(
        db=db,
        doctor_id=token_data["doctor_id"],
        patient_id=token_data["patient_id"],
        start_time=token_data["start_time"],
        end_time=token_data["end_time"],
        reason=token_data.get("reason"),
    )

    start_str = appt.appointment_range.lower.isoformat()
    end_str = appt.appointment_range.upper.isoformat()

    return AppointmentResponse(
        id=appt.id,
        doctor_id=appt.doctor_id,
        patient_id=appt.patient_id,
        start_time=start_str,
        end_time=end_str,
        status=appt.status,
        reason=appt.reason,
    )


@router.post("", response_model=AppointmentResponse)
def book_appointment(
    payload: CreateAppointmentRequest,
    db: Session = Depends(get_db),
):
    appt = create_appointment(
        db=db,
        doctor_id=payload.doctor_id,
        patient_id=payload.patient_id,
        start_time=payload.start_time,
        end_time=payload.end_time,
        reason=payload.reason,
    )

    start_str = appt.appointment_range.lower.isoformat()
    end_str = appt.appointment_range.upper.isoformat()

    return AppointmentResponse(
        id=appt.id,
        doctor_id=appt.doctor_id,
        patient_id=appt.patient_id,
        start_time=start_str,
        end_time=end_str,
        status=appt.status,
        reason=appt.reason,
    )


@router.delete("/{appointment_id}", response_model=AppointmentResponse)
def cancel(
    appointment_id: int,
    patient_id: int = Query(..., description="Verified patient ID making the cancellation request"),
    db: Session = Depends(get_db),
):
    appt = cancel_appointment(
        db=db,
        appointment_id=appointment_id,
        patient_id=patient_id,
    )

    start_str = appt.appointment_range.lower.isoformat()
    end_str = appt.appointment_range.upper.isoformat()

    return AppointmentResponse(
        id=appt.id,
        doctor_id=appt.doctor_id,
        patient_id=appt.patient_id,
        start_time=start_str,
        end_time=end_str,
        status=appt.status,
        reason=appt.reason,
    )


@router.patch("/{appointment_id}", response_model=AppointmentResponse)
def reschedule(
    appointment_id: int,
    payload: RescheduleAppointmentRequest,
    db: Session = Depends(get_db),
):
    appt = reschedule_appointment(
        db=db,
        appointment_id=appointment_id,
        patient_id=payload.patient_id,
        doctor_id=payload.doctor_id,
        new_start_time=payload.start_time,
        new_end_time=payload.end_time,
        reason=payload.reason,
    )

    start_str = appt.appointment_range.lower.isoformat()
    end_str = appt.appointment_range.upper.isoformat()

    return AppointmentResponse(
        id=appt.id,
        doctor_id=appt.doctor_id,
        patient_id=appt.patient_id,
        start_time=start_str,
        end_time=end_str,
        status=appt.status,
        reason=appt.reason,
    )
