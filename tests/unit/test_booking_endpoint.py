from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from app.api.main import app
from app.api.routes.appointments import CONFIRMATION_TOKENS
from app.db.models import Appointment, AuditLog, Patient
from app.db.session import SessionLocal

client = TestClient(app)


@pytest.fixture
def verified_booking_setup(test_doctor):
    """
    Creates a dedicated test patient and performs an identity verification
    so that patient_id is verified in audit_log.
    """
    doctor_id = test_doctor["doctor_id"]

    db = SessionLocal()
    patient = Patient(
        first_name="BookingTestFirst",
        last_name="BookingTestLast",
        date_of_birth="1992-03-15",
        phone="555-1234",
    )
    db.add(patient)
    db.commit()
    db.refresh(patient)
    patient_id = patient.id
    db.close()

    # Verify identity for this unique patient
    payload_id = {
        "first_name": "BookingTestFirst",
        "last_name": "BookingTestLast",
        "date_of_birth": "1992-03-15",
    }
    res_id = client.post("/verify-identity", json=payload_id)
    assert res_id.status_code == 200
    assert res_id.json()["verified"] is True
    assert res_id.json()["patient_id"] == patient_id

    yield {"doctor_id": doctor_id, "patient_id": patient_id}

    # Teardown
    db = SessionLocal()
    db.query(AuditLog).filter(AuditLog.patient_id == patient_id).delete()
    db.query(Appointment).filter(Appointment.patient_id == patient_id).delete()
    db.query(Patient).filter(Patient.id == patient_id).delete()
    db.commit()
    db.close()


def test_booking_unverified_patient_fails(test_doctor):
    """
    Verifies that calling POST /appointments for an unverified patient_id
    returns 403 Forbidden and writes an audit log entry.
    """
    payload = {
        "patient_id": 999999,
        "doctor_id": test_doctor["doctor_id"],
        "start_time": "2026-08-03T10:00:00Z",
        "end_time": "2026-08-03T10:30:00Z",
    }
    response = client.post("/appointments", json=payload)
    assert response.status_code == 403
    assert "verified" in response.json()["detail"].lower()


def test_propose_and_confirm_booking_success(verified_booking_setup):
    """
    Tests the two-step propose -> confirm flow.
    """
    doctor_id = verified_booking_setup["doctor_id"]
    patient_id = verified_booking_setup["patient_id"]

    propose_payload = {
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "start_time": "2026-08-03T10:00:00Z",
        "end_time": "2026-08-03T10:30:00Z",
        "reason": "Consultation",
    }
    res_prop = client.post("/appointments/propose", json=propose_payload)
    assert res_prop.status_code == 200
    prop_data = res_prop.json()
    assert prop_data["status"] == "proposed"
    assert "confirmation_token" in prop_data
    token = prop_data["confirmation_token"]

    confirm_payload = {"confirmation_token": token}
    res_conf = client.post("/appointments/confirm", json=confirm_payload)
    assert res_conf.status_code == 200
    conf_data = res_conf.json()
    assert conf_data["doctor_id"] == doctor_id
    assert conf_data["patient_id"] == patient_id
    assert conf_data["status"] == "booked"


def test_confirm_booking_expired_token(verified_booking_setup):
    """
    Verifies that an expired confirmation token is rejected with HTTP 400.
    """
    doctor_id = verified_booking_setup["doctor_id"]
    patient_id = verified_booking_setup["patient_id"]

    propose_payload = {
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "start_time": "2026-08-03T11:00:00Z",
        "end_time": "2026-08-03T11:30:00Z",
    }
    res_prop = client.post("/appointments/propose", json=propose_payload)
    assert res_prop.status_code == 200
    token = res_prop.json()["confirmation_token"]

    # Manually expire the token
    CONFIRMATION_TOKENS[token]["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)

    res_conf = client.post("/appointments/confirm", json={"confirmation_token": token})
    assert res_conf.status_code == 400
    assert "expired" in res_conf.json()["detail"].lower()


def test_confirm_booking_invalid_or_reused_token(verified_booking_setup):
    """
    Verifies that using an invalid or already used confirmation token fails with HTTP 400.
    """
    res_conf = client.post("/appointments/confirm", json={"confirmation_token": "non_existent_token"})
    assert res_conf.status_code == 400
    assert "missing" in res_conf.json()["detail"].lower() or "expired" in res_conf.json()["detail"].lower()


def test_booking_success(verified_booking_setup):
    """
    Verifies that booking a valid slot for a verified patient succeeds (200 OK),
    and creates rows in appointments and audit_log tables.
    """
    doctor_id = verified_booking_setup["doctor_id"]
    patient_id = verified_booking_setup["patient_id"]

    payload = {
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "start_time": "2026-08-03T12:00:00Z",
        "end_time": "2026-08-03T12:30:00Z",
        "reason": "Routine Checkup",
    }
    response = client.post("/appointments", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["doctor_id"] == doctor_id
    assert data["patient_id"] == patient_id
    assert data["status"] == "booked"
    assert data["reason"] == "Routine Checkup"


def test_booking_double_booking_returns_409(verified_booking_setup):
    """
    Verifies that attempting to book an overlapping slot returns 409 Conflict.
    """
    doctor_id = verified_booking_setup["doctor_id"]
    patient_id = verified_booking_setup["patient_id"]

    payload = {
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "start_time": "2026-08-03T13:00:00Z",
        "end_time": "2026-08-03T13:30:00Z",
    }
    res1 = client.post("/appointments", json=payload)
    assert res1.status_code == 200

    # Overlapping attempt
    res2 = client.post("/appointments", json=payload)
    assert res2.status_code == 409
    assert "no longer available" in res2.json()["detail"].lower()


def test_booking_outside_working_hours_returns_400(test_doctor_normal_hours):
    """
    Verifies that booking outside doctor's working hours returns 400 Bad Request.
    """
    doctor_id = test_doctor_normal_hours["doctor_id"]
    patient_id = test_doctor_normal_hours["patient_id"]

    # Verify identity for Carol Test
    res_id = client.post("/verify-identity", json={
        "first_name": "Carol",
        "last_name": "Test",
        "date_of_birth": "1990-01-01",
    })
    assert res_id.status_code == 200
    assert res_id.json()["verified"] is True

    # 2026-08-03 20:00 (outside 09:00-17:00 working hours)
    payload = {
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "start_time": "2026-08-03T20:00:00Z",
        "end_time": "2026-08-03T20:30:00Z",
    }
    response = client.post("/appointments", json=payload)
    assert response.status_code == 400
    assert "working hours" in response.json()["detail"].lower()


def test_booking_endpoint_concurrency(verified_booking_setup):
    """
    Verifies that concurrent requests to POST /appointments for the exact same slot
    results in exactly 1 success (200) and the rest 409 Conflict responses.
    """
    doctor_id = verified_booking_setup["doctor_id"]
    patient_id = verified_booking_setup["patient_id"]

    payload = {
        "patient_id": patient_id,
        "doctor_id": doctor_id,
        "start_time": "2026-08-03T14:00:00Z",
        "end_time": "2026-08-03T14:30:00Z",
    }

    def make_booking_request():
        # Fresh client per thread for realistic concurrent HTTP requests
        c = TestClient(app)
        return c.post("/appointments", json=payload)

    num_threads = 5
    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(make_booking_request) for _ in range(num_threads)]
        results = [f.result() for f in futures]

    statuses = [r.status_code for r in results]
    successes = statuses.count(200)
    conflicts = statuses.count(409)

    assert successes == 1, f"Expected 1 success, got {successes} (statuses: {statuses})"
    assert conflicts == num_threads - 1, f"Expected {num_threads - 1} conflicts, got {conflicts}"


def test_get_upcoming_appointments():
    """
    Verifies GET /appointments returns a list of appointments containing doctor_name and patient_name.
    """
    response = client.get("/appointments")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    if data:
        first = data[0]
        assert "doctor_name" in first
        assert "patient_name" in first
        assert "start_time" in first
        assert "end_time" in first
        assert "status" in first
