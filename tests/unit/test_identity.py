import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.db.models import AuditLog, Patient
from app.db.session import SessionLocal
from app.voice.pipeline import sanitize_for_tts

client = TestClient(app)


@pytest.fixture
def identity_test_patient():
    db = SessionLocal()
    patient = Patient(
        first_name="IdentityTestFirst",
        last_name="IdentityTestLast",
        date_of_birth="1988-04-12",
        phone="555-9999",
    )
    db.add(patient)
    db.commit()
    db.refresh(patient)
    patient_id = patient.id
    yield patient

    # Teardown
    db.query(AuditLog).filter(AuditLog.patient_id == patient_id).delete()
    db.query(Patient).filter(Patient.id == patient_id).delete()
    db.commit()
    db.close()


def test_verify_identity_success(identity_test_patient):
    payload = {
        "first_name": identity_test_patient.first_name,
        "last_name": identity_test_patient.last_name,
        "date_of_birth": identity_test_patient.date_of_birth.isoformat(),
    }
    response = client.post("/verify-identity", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["verified"] is True
    assert data["patient_id"] == identity_test_patient.id
    assert "verified successfully" in data["message"].lower()

    # Check audit log
    db = SessionLocal()
    try:
        log = (
            db.query(AuditLog)
            .filter(AuditLog.patient_id == identity_test_patient.id, AuditLog.action == "verify_identity")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert log is not None
        assert log.allowed is True
    finally:
        db.close()


def test_verify_identity_dob_trailing_period(identity_test_patient):
    """
    Test: DOB with trailing period ("1988-04-12.") verifies successfully
    against a seeded patient with that exact DOB.
    """
    payload = {
        "first_name": identity_test_patient.first_name,
        "last_name": identity_test_patient.last_name,
        "date_of_birth": f"{identity_test_patient.date_of_birth.isoformat()}.",
    }
    response = client.post("/verify-identity", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["verified"] is True
    assert data["patient_id"] == identity_test_patient.id


def test_verify_identity_case_insensitive_name(identity_test_patient):
    """
    Test: name with different case ("identitytestfirst." vs seeded "IdentityTestFirst")
    verifies successfully.
    """
    payload = {
        "first_name": "identitytestfirst.",
        "last_name": "IDENTITYTESTLAST,",
        "date_of_birth": identity_test_patient.date_of_birth.isoformat(),
    }
    response = client.post("/verify-identity", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["verified"] is True
    assert data["patient_id"] == identity_test_patient.id


def test_verify_identity_genuinely_wrong_credentials(identity_test_patient):
    """
    Test: genuinely wrong DOB/name still correctly fails (don't over-loosen).
    """
    payload = {
        "first_name": identity_test_patient.first_name,
        "last_name": identity_test_patient.last_name,
        "date_of_birth": "1900-01-01",
    }
    response = client.post("/verify-identity", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["verified"] is False
    assert data["patient_id"] is None
    assert "does not match our records" in data["message"]


def test_verify_identity_unparseable_dob_does_not_crash(identity_test_patient):
    """
    Test: unparseable date string doesn't crash — returns a clean "not verified" response.
    """
    payload = {
        "first_name": identity_test_patient.first_name,
        "last_name": identity_test_patient.last_name,
        "date_of_birth": "not-a-valid-date-string!!!",
    }
    response = client.post("/verify-identity", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["verified"] is False
    assert data["patient_id"] is None
    assert "does not match our records" in data["message"]


def test_sanitize_for_tts():
    """
    Tests TTS text sanitization rules:
    - Strip quote characters (' and "), backticks, and markdown asterisks (*).
    - Collapse repeated whitespace.
    - Drop chunks containing no alphanumeric characters.
    """
    assert sanitize_for_tts("Hello! 'How' *are* `you`?") == "Hello! How are you?"
    assert sanitize_for_tts(".") == ""
    assert sanitize_for_tts("   !  ?  .   ") == ""
    assert sanitize_for_tts("**Dr. Smith** is ready.") == "Dr. Smith is ready."
    assert sanitize_for_tts("  Hello   world!  ") == "Hello world!"
    assert sanitize_for_tts('"Yes, confirmed."') == "Yes, confirmed."
