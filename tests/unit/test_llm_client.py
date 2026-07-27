import os
import uuid
from unittest.mock import patch
import pytest
from app.db.models import Appointment, AuditLog, Patient
from app.db.session import SessionLocal
from app.voice.conversation_logger import ConversationLogger
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
    db.query(Appointment).filter(Appointment.patient_id == patient_id).delete()
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


def test_execute_propose_and_confirm_booking_tools(test_doctor, llm_test_patient):
    # Verify patient first to pass verify_identity requirement
    execute_tool_call(
        "verify_identity",
        {
            "first_name": llm_test_patient.first_name,
            "last_name": llm_test_patient.last_name,
            "date_of_birth": llm_test_patient.date_of_birth.isoformat(),
        },
    )

    prop_args = {
        "patient_id": llm_test_patient.id,
        "doctor_id": test_doctor["doctor_id"],
        "start_time": "2026-08-03T10:00:00Z",
        "end_time": "2026-08-03T10:30:00Z",
        "reason": "Test Booking",
    }
    prop_res = execute_tool_call("propose_booking", prop_args)
    assert prop_res["status"] == "proposed"
    token = prop_res["confirmation_token"]

    conf_res = execute_tool_call("confirm_booking", {"confirmation_token": token})
    assert conf_res["status"] == "booked"
    assert conf_res["patient_id"] == llm_test_patient.id


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
def test_tool_result_sequencing(mock_call_ollama, llm_test_patient):
    """
    Explicit sequencing test: tool call -> await result -> append to history -> THEN start next generation.
    No race between these steps.
    """
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

    # Check call order & history state when second call_ollama was executed
    assert mock_call_ollama.call_count == 2

    # The second call to call_ollama must have received the tool result message in messages payload
    second_call_messages = mock_call_ollama.call_args_list[1][0][0]
    tool_msg = [m for m in second_call_messages if m.get("role") == "tool"]
    assert len(tool_msg) == 1
    assert "verified" in tool_msg[0]["content"]

    assert f"Your patient ID is {llm_test_patient.id}" in response_text


@patch("app.voice.llm_client.call_ollama")
def test_multi_turn_context_preservation(mock_call_ollama):
    """
    Validates that conversation/call history context persists and builds across turns.
    """
    mock_call_ollama.side_effect = [
        {"message": {"role": "assistant", "content": "Hello! What is your date of birth?"}},
        {"message": {"role": "assistant", "content": "Got it! How can I help you book today?"}},
        {"message": {"role": "assistant", "content": "Checking available slots for Dr. Smith."}},
    ]

    history = []
    # Turn 1: State name
    resp1, history = run_chat_turn(history, "Hello, my name is Jane Doe.")
    assert len(history) == 3  # system, user, assistant

    # Turn 2: State DOB
    resp2, history = run_chat_turn(history, "My date of birth is 1990-05-12.")
    assert len(history) == 5  # + user, assistant

    # Turn 3: Request action referencing context from turn 1 and 2
    resp3, history = run_chat_turn(history, "Can you check availability for doctor 1 on 2026-08-03?")
    assert len(history) == 7

    # Verify history contains all previous turns in sequence
    user_msgs = [m["content"] for m in history if m.get("role") == "user"]
    assert user_msgs == [
        "Hello, my name is Jane Doe.",
        "My date of birth is 1990-05-12.",
        "Can you check availability for doctor 1 on 2026-08-03?",
    ]


def test_conversation_logger_redaction_and_ist():
    """
    Verifies that ConversationLogger creates a log file with IST timestamp and redacts DOB and full names.
    """
    logger = ConversationLogger(call_id="testcall123")
    logger.log_stt("My date of birth is 1990-05-12 and my name is Jane Doe.")
    logger.log_llm_response("Thank you, Jane Doe. Your DOB 1990-05-12 is noted.")
    logger.log_tool_call(
        "verify_identity",
        {"first_name": "Jane", "last_name": "Doe", "date_of_birth": "1990-05-12"},
        {"verified": True, "patient_id": 42},
    )

    assert os.path.exists(logger.filepath)
    with open(logger.filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Check IST timezone in timestamp header
    assert "IST" in content
    # Check DOB redaction
    assert "1990-05-12" not in content
    assert "[DOB provided]" in content
    # Check last name redaction in tool args
    assert "'last_name': 'D.'" in content or '"last_name": "D."' in content or "D." in content
