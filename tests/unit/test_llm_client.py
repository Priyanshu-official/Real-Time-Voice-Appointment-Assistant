import uuid
from unittest.mock import patch
import pytest
from app.db.models import AuditLog, Patient
from app.db.session import SessionLocal
from app.voice.llm_client import SYSTEM_PROMPT, execute_tool_call, run_chat_turn


@pytest.fixture
def llm_test_patient():
    unique_suffix = str(uuid.uuid4())[:8]
    first_name = f"LLMFirst_{unique_suffix}"
    last_name = f"LLMLast_{unique_suffix}"

    db = SessionLocal()
    patient = Patient(
        first_name=first_name,
        last_name=last_name,
        date_of_birth="1985-07-20",
        phone="555-4444",
    )
    db.add(patient)
    db.commit()
    db.refresh(patient)
    patient_id = patient.id
    yield patient

    # Teardown in FK-safe order
    db.query(AuditLog).filter(AuditLog.patient_id == patient_id).delete()
    db.query(Patient).filter(Patient.id == patient_id).delete()
    db.commit()
    db.close()


def test_execute_verify_identity_tool(llm_test_patient):
    args = {
        "first_name": llm_test_patient.first_name,
        "last_name": llm_test_patient.last_name,
        "date_of_birth": llm_test_patient.date_of_birth.isoformat(),
    }
    result = execute_tool_call("verify_identity", args)
    assert result["verified"] is True
    assert result["patient_id"] == llm_test_patient.id


def test_execute_check_availability_tool(test_doctor):
    args = {
        "doctor_id": test_doctor["doctor_id"],
        "date": "2026-08-03",
    }
    result = execute_tool_call("check_availability", args)
    assert result["doctor_id"] == test_doctor["doctor_id"]
    assert "available_slots" in result


def test_execute_unknown_tool():
    result = execute_tool_call("non_existent_tool", {})
    assert "error" in result
    assert "Unknown tool" in result["error"]


@patch("app.voice.llm_client.call_ollama")
def test_run_chat_turn_without_tool_calls(mock_call_ollama):
    mock_call_ollama.return_value = {
        "message": {
            "role": "assistant",
            "content": "Hello! How can I assist you today?",
        }
    }
    history = []
    response_text, updated_history = run_chat_turn(history, "Hello")

    assert response_text == "Hello! How can I assist you today?"
    assert updated_history[0]["role"] == "system"
    assert SYSTEM_PROMPT in updated_history[0]["content"]
    assert updated_history[1]["role"] == "user"
    assert updated_history[1]["content"] == "Hello"


@patch("app.voice.llm_client.call_ollama")
def test_run_chat_turn_with_tool_calls(mock_call_ollama, llm_test_patient):
    # First response emits a tool call to verify_identity
    first_response = {
        "message": {
            "role": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "verify_identity",
                        "arguments": {
                            "first_name": llm_test_patient.first_name,
                            "last_name": llm_test_patient.last_name,
                            "date_of_birth": llm_test_patient.date_of_birth.isoformat(),
                        },
                    }
                }
            ],
        }
    }
    # Second response (after tool result) returns final text
    second_response = {
        "message": {
            "role": "assistant",
            "content": f"Identity verified! Your patient ID is {llm_test_patient.id}.",
        }
    }
    mock_call_ollama.side_effect = [first_response, second_response]

    history = []
    response_text, updated_history = run_chat_turn(
        history,
        f"Hi, I'm {llm_test_patient.first_name} {llm_test_patient.last_name}, born {llm_test_patient.date_of_birth.isoformat()}.",
    )

    assert f"Your patient ID is {llm_test_patient.id}" in response_text
    # Verify tool execution result was appended to messages
    tool_msgs = [m for m in updated_history if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert "verified" in tool_msgs[0]["content"]
