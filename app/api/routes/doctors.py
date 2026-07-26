from datetime import date, datetime, time, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session, joinedload

from app.db.models import Appointment, Doctor
from app.db.session import get_db

router = APIRouter(prefix="/doctors", tags=["doctors"])


class DoctorResponse(BaseModel):
    id: int
    name: str
    specialty_id: int
    specialty_name: str
    working_days: List[int]
    working_start_time: str
    working_end_time: str

    model_config = ConfigDict(from_attributes=True)


class TimeSlot(BaseModel):
    start_time: str
    end_time: str


class DoctorAvailabilityResponse(BaseModel):
    doctor_id: int
    date: date
    available_slots: List[TimeSlot]


@router.get("", response_model=List[DoctorResponse])
def list_doctors(db: Session = Depends(get_db)):
    doctors = db.query(Doctor).options(joinedload(Doctor.specialty)).all()
    result = []
    for doc in doctors:
        start_str = (
            doc.working_start_time.strftime("%H:%M")
            if isinstance(doc.working_start_time, time)
            else str(doc.working_start_time)
        )
        end_str = (
            doc.working_end_time.strftime("%H:%M")
            if isinstance(doc.working_end_time, time)
            else str(doc.working_end_time)
        )
        result.append(
            DoctorResponse(
                id=doc.id,
                name=doc.name,
                specialty_id=doc.specialty_id,
                specialty_name=doc.specialty.name if doc.specialty else "",
                working_days=doc.working_days,
                working_start_time=start_str,
                working_end_time=end_str,
            )
        )
    return result


@router.get("/{doctor_id}/availability", response_model=DoctorAvailabilityResponse)
def get_doctor_availability(
    doctor_id: int,
    date_param: date = Query(..., alias="date", description="Date in YYYY-MM-DD format"),
    db: Session = Depends(get_db),
):
    doctor = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail=f"Doctor with id {doctor_id} not found")

    # Day of week in Postgres convention: 0=Sunday, 1=Monday, ..., 6=Saturday
    # strftime("%w") returns '0' for Sunday .. '6' for Saturday
    dow = int(date_param.strftime("%w"))
    if dow not in doctor.working_days:
        return DoctorAvailabilityResponse(
            doctor_id=doctor.id,
            date=date_param,
            available_slots=[],
        )

    start_dt = datetime.combine(date_param, doctor.working_start_time).replace(
        tzinfo=timezone.utc
    )
    end_dt = datetime.combine(date_param, doctor.working_end_time).replace(
        tzinfo=timezone.utc
    )

    slot_duration = timedelta(minutes=30)
    candidate_slots = []
    curr = start_dt
    while curr + slot_duration <= end_dt:
        slot_start = curr
        slot_end = curr + slot_duration
        candidate_slots.append((slot_start, slot_end))
        curr += slot_duration

    # Query active booked appointments for doctor
    appointments = (
        db.query(Appointment)
        .filter(
            Appointment.doctor_id == doctor_id,
            Appointment.status == "booked",
        )
        .all()
    )

    open_slots = []
    for s_start, s_end in candidate_slots:
        is_booked = False
        for appt in appointments:
            if appt.appointment_range is None:
                continue
            appt_start = appt.appointment_range.lower
            appt_end = appt.appointment_range.upper

            if appt_start.tzinfo is None:
                appt_start = appt_start.replace(tzinfo=timezone.utc)
            if appt_end.tzinfo is None:
                appt_end = appt_end.replace(tzinfo=timezone.utc)

            # Overlap check: s_start < appt_end and s_end > appt_start
            if s_start < appt_end and s_end > appt_start:
                is_booked = True
                break

        if not is_booked:
            open_slots.append(
                TimeSlot(
                    start_time=s_start.strftime("%H:%M"),
                    end_time=s_end.strftime("%H:%M"),
                )
            )

    return DoctorAvailabilityResponse(
        doctor_id=doctor.id,
        date=date_param,
        available_slots=open_slots,
    )
