from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.services.booking import (
    cancel_appointment,
    create_appointment,
    reschedule_appointment,
)
from app.db.session import get_db

router = APIRouter(prefix="/appointments", tags=["appointments"])


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
