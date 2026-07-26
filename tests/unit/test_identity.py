import pytest
from fastapi.testclient import TestClient
from app.api.main import app
from app.db.models import AuditLog, Patient
from app.db.session import SessionLocal

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


def test_verify_identity_mismatch_first_name(identity_test_patient):
    payload = {
        "first_name": "WrongFirst",
        "last_name": identity_test_patient.last_name,
        "date_of_birth": identity_test_patient.date_of_birth.isoformat(),
    }
    response = client.post("/verify-identity", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["verified"] is False
    assert data["patient_id"] is None
    assert "does not match our records" in data["message"]
    assert "first_name" not in data["message"]


def test_verify_identity_mismatch_dob(identity_test_patient):
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
