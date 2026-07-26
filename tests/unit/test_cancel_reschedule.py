import pytest
from fastapi.testclient import TestClient
from app.api.main import app
from app.db.models import Appointment, AuditLog, Patient
from app.db.session import SessionLocal

client = TestClient(app)


@pytest.fixture
def cancel_reschedule_setup(test_doctor):
    """
    Creates two isolated verified patients (Patient A and Patient B) and an initial appointment for Patient A.
    """
    doctor_id = test_doctor["doctor_id"]

    db = SessionLocal()
    patient_a = Patient(
        first_name="PatientAFirst",
        last_name="PatientALast",
        date_of_birth="1990-05-20",
        phone="555-0001",
    )
    patient_b = Patient(
        first_name="PatientBFirst",
        last_name="PatientBLast",
        date_of_birth="1992-10-15",
        phone="555-0002",
    )
    db.add_all([patient_a, patient_b])
    db.commit()
    db.refresh(patient_a)
    db.refresh(patient_b)

    id_a = patient_a.id
    id_b = patient_b.id
    db.close()

    # Verify identity for both patients
    res_a = client.post("/verify-identity", json={
        "first_name": "PatientAFirst",
        "last_name": "PatientALast",
        "date_of_birth": "1990-05-20",
    })
    assert res_a.status_code == 200 and res_a.json()["verified"] is True

    res_b = client.post("/verify-identity", json={
        "first_name": "PatientBFirst",
        "last_name": "PatientBLast",
        "date_of_birth": "1992-10-15",
    })
    assert res_b.status_code == 200 and res_b.json()["verified"] is True

    # Book an initial appointment for Patient A
    book_res = client.post("/appointments", json={
        "patient_id": id_a,
        "doctor_id": doctor_id,
        "start_time": "2026-08-03T10:00:00Z",
        "end_time": "2026-08-03T10:30:00Z",
        "reason": "Initial Checkup",
    })
    assert book_res.status_code == 200
    appt_a_id = book_res.json()["id"]

    yield {
        "doctor_id": doctor_id,
        "patient_a_id": id_a,
        "patient_b_id": id_b,
        "appt_a_id": appt_a_id,
    }

    # Teardown
    db = SessionLocal()
    db.query(AuditLog).filter(AuditLog.patient_id.in_([id_a, id_b])).delete()
    db.query(Appointment).filter(Appointment.patient_id.in_([id_a, id_b])).delete()
    db.query(Patient).filter(Patient.id.in_([id_a, id_b])).delete()
    db.commit()
    db.close()


def test_cancel_appointment_success(cancel_reschedule_setup):
    """
    Verifies that a patient can successfully cancel their own appointment.
    """
    patient_a_id = cancel_reschedule_setup["patient_a_id"]
    appt_a_id = cancel_reschedule_setup["appt_a_id"]

    res = client.delete(f"/appointments/{appt_a_id}?patient_id={patient_a_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == appt_a_id
    assert data["status"] == "cancelled"

    # Verify audit log recorded cancel_appointment
    db = SessionLocal()
    try:
        log = db.query(AuditLog).filter(
            AuditLog.patient_id == patient_a_id,
            AuditLog.action == "cancel_appointment",
            AuditLog.allowed == True,
        ).first()
        assert log is not None
    finally:
        db.close()


def test_cancel_another_patient_appointment_fails(cancel_reschedule_setup):
    """
    Verifies that Patient B cannot cancel Patient A's appointment (returns 403 Forbidden).
    """
    patient_b_id = cancel_reschedule_setup["patient_b_id"]
    appt_a_id = cancel_reschedule_setup["appt_a_id"]

    res = client.delete(f"/appointments/{appt_a_id}?patient_id={patient_b_id}")
    assert res.status_code == 403
    assert "cannot cancel another patient's appointment" in res.json()["detail"].lower()


def test_cancel_unverified_caller_fails(cancel_reschedule_setup):
    """
    Verifies that an unverified patient_id cannot cancel an appointment (returns 403 Forbidden).
    """
    appt_a_id = cancel_reschedule_setup["appt_a_id"]

    res = client.delete(f"/appointments/{appt_a_id}?patient_id=888888")
    assert res.status_code == 403
    assert "verified" in res.json()["detail"].lower()


def test_reschedule_appointment_success(cancel_reschedule_setup):
    """
    Verifies that a patient can reschedule their appointment to an available slot.
    """
    doctor_id = cancel_reschedule_setup["doctor_id"]
    patient_a_id = cancel_reschedule_setup["patient_a_id"]
    appt_a_id = cancel_reschedule_setup["appt_a_id"]

    reschedule_payload = {
        "patient_id": patient_a_id,
        "doctor_id": doctor_id,
        "start_time": "2026-08-03T11:00:00Z",
        "end_time": "2026-08-03T11:30:00Z",
        "reason": "Rescheduled Checkup",
    }

    res = client.patch(f"/appointments/{appt_a_id}", json=reschedule_payload)
    assert res.status_code == 200
    data = res.json()
    assert data["patient_id"] == patient_a_id
    assert data["doctor_id"] == doctor_id
    assert data["status"] == "booked"

    # Verify old appointment is now cancelled in DB
    db = SessionLocal()
    try:
        old_appt = db.query(Appointment).filter(Appointment.id == appt_a_id).first()
        assert old_appt.status == "cancelled"
    finally:
        db.close()


def test_reschedule_to_occupied_slot_fails_409(cancel_reschedule_setup):
    """
    Verifies that attempting to reschedule to an occupied slot returns 409 Conflict.
    """
    doctor_id = cancel_reschedule_setup["doctor_id"]
    patient_a_id = cancel_reschedule_setup["patient_a_id"]
    patient_b_id = cancel_reschedule_setup["patient_b_id"]
    appt_a_id = cancel_reschedule_setup["appt_a_id"]

    # Book 11:00-11:30 for Patient B
    res_b = client.post("/appointments", json={
        "patient_id": patient_b_id,
        "doctor_id": doctor_id,
        "start_time": "2026-08-03T11:00:00Z",
        "end_time": "2026-08-03T11:30:00Z",
    })
    assert res_b.status_code == 200

    # Attempt to reschedule Patient A's appointment to 11:00-11:30 (already occupied by Patient B)
    reschedule_payload = {
        "patient_id": patient_a_id,
        "doctor_id": doctor_id,
        "start_time": "2026-08-03T11:00:00Z",
        "end_time": "2026-08-03T11:30:00Z",
    }

    res = client.patch(f"/appointments/{appt_a_id}", json=reschedule_payload)
    assert res.status_code == 409
    assert "no longer available" in res.json()["detail"].lower()
