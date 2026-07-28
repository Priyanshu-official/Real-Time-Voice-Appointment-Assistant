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
    - Preserve single quotes in contractions (what's, It's, I'm).
    - Strip double quote characters ("), backticks, outer single quotes, and markdown asterisks (*).
    - Collapse repeated whitespace.
    - Drop chunks containing no alphanumeric characters.
    """
    assert sanitize_for_tts("Hello! 'How' *are* `you`?") == "Hello! How are you?"
    assert sanitize_for_tts(".") == ""
    assert sanitize_for_tts("   !  ?  .   ") == ""
    assert sanitize_for_tts("**Dr. Smith** is ready.") == "Dr. Smith is ready"
    assert sanitize_for_tts("  Hello   world!  ") == "Hello world!"
    assert sanitize_for_tts('"Yes, confirmed."') == "Yes, confirmed"
    assert sanitize_for_tts("What's your name?") == "What's your name?"
    assert sanitize_for_tts("It's ready.") == "It's ready"


def test_verify_identity_messy_stt_formatting(identity_test_patient):
    """
    Test: STT input with quotes, extra spaces, and trailing periods
    verifies successfully.
    """
    payload = {
        "first_name": " 'IdentityTestFirst.' ",
        "last_name": "\"IDENTITYTESTLAST,\"",
        "date_of_birth": identity_test_patient.date_of_birth.isoformat() + ".",
    }
    response = client.post("/verify-identity", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["verified"] is True


def test_extract_sentences_from_buffer_sanitizes_punctuation():
    """
    Test: extract_sentences_from_buffer filters out lone punctuation sentences and merges short 1-word exclamations.
    """
    from app.voice.llm_client import extract_sentences_from_buffer

    sentences, rem = extract_sentences_from_buffer("Thank you. . ")
    assert sentences == ["Thank you"]
    assert rem == ""

    sentences, rem = extract_sentences_from_buffer("Great! Now let's check available slots.")
    assert sentences == ["Great, now let's check available slots"]
    assert rem == ""


def test_normalize_spoken_date():
    """
    Tests spoken date normalization (ordinals, number words).
    """
    from app.api.services.identity import _normalize_spoken_date

    assert _normalize_spoken_date("August 22nd 1992") == "August 22 1992"
    assert _normalize_spoken_date("nineteen seventy eight") == "19 70 8"
    assert _normalize_spoken_date("December 3rd 1985") == "December 3 1985"


def test_verify_identity_incomplete_dob_rejection(identity_test_patient):
    """
    Tests that incomplete year-only DOB ('1988') is rejected gracefully with explanatory message.
    """
    payload = {
        "first_name": identity_test_patient.first_name,
        "last_name": identity_test_patient.last_name,
        "date_of_birth": "1988",
    }
    response = client.post("/verify-identity", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["verified"] is False
    assert data["patient_id"] is None
    assert "Incomplete date of birth provided" in data["message"]


def test_conversation_logger_eval_dataset(tmp_path):
    """
    Tests ConversationLogger writes log_eval_sample entries to eval_dataset.jsonl.
    """
    from app.voice.conversation_logger import ConversationLogger, LOG_DIR
    import json

    logger_inst = ConversationLogger(call_id="test_call_123")
    logger_inst.log_tool_call(
        tool_name="verify_identity",
        args={"first_name": "Test", "last_name": "User", "date_of_birth": "1990-01-01"},
        result={"verified": True, "patient_id": 1, "message": "Success"}
    )

    eval_path = LOG_DIR.parent / "eval_dataset.jsonl"
    assert eval_path.exists()
    content = eval_path.read_text()
    assert "test_call_123" in content
    assert "verify_identity" in content


