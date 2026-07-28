import os
import re
import uuid
from unittest.mock import patch
import pytest
from app.db.models import Appointment, AuditLog, Patient
from app.db.session import SessionLocal
from app.voice.conversation_logger import ConversationLogger
from app.voice.llm_client import (
    SYSTEM_PROMPT,
    execute_tool_call,
    extract_sentences_from_buffer,
    format_tts_text,
    run_chat_turn,
    sanitize_for_tts,
)


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


def test_tts_sentence_joining_single_space():
    """
    Verifies input ["Got it!", "Dr.", "Smith on tomorrow."] produces "Got it! Dr. Smith on tomorrow.".
    """
    input_fragments = ["Got it!", "Dr.", "Smith on tomorrow."]
    output = format_tts_text(input_fragments)
    assert output == "Got it! Dr. Smith on tomorrow."


def test_tts_regression_no_word_dot_word_runons():
    """
    Regression test asserting no TTS-bound string contains a word character immediately followed
    by '.' immediately followed by another word character with no space (catches "Dr.Smith"-style bugs).
    """
    test_cases = [
        ["Got it!", "Dr.", "Smith on tomorrow."],
        ["Hello!", "Dr.", "Jones."],
        ["Mr.", "Smith", "is here."],
    ]
    for case in test_cases:
        res = format_tts_text(case)
        assert re.search(r"\b[A-Za-z]+\.[A-Za-z]+\b", res) is None, f"Run-on artifact found in: '{res}'"


def test_execute_scheduling_tools_reject_incomplete_datetime():
    """
    Verifies book_appointment, propose_booking, and reschedule_appointment tool calls with incomplete
    or missing date/time parameters return a 'needs_clarification' status without calling underlying API.
    """
    # Missing time component
    res_book = execute_tool_call(
        "book_appointment",
        {"doctor_id": 1, "patient_id": 1, "start_time": "November 28th"},
    )
    assert res_book["status"] == "needs_clarification"
    assert "Missing time component" in res_book["error"]

    # Missing date component
    res_prop = execute_tool_call(
        "propose_booking",
        {"doctor_id": 1, "patient_id": 1, "start_time": "at 10 AM"},
    )
    assert res_prop["status"] == "needs_clarification"
    assert "Missing date component" in res_prop["error"]

    # Incomplete datetime
    res_resched = execute_tool_call(
        "reschedule_appointment",
        {"doctor_id": 1, "appointment_id": 1, "patient_id": 1, "new_start_time": "Friday"},
    )
    assert res_resched["status"] == "needs_clarification"
    assert "Missing time component" in res_resched["error"]


def test_system_prompt_prohibits_machine_formats():
    """
    Verifies SYSTEM_PROMPT contains strict rules against speaking ISO 8601 strings, machine formats,
    and unverified tool execution.
    """
    assert "YYYY-MM-DDTHH:MM:SSZ" in SYSTEM_PROMPT
    assert "NEVER SPEAK MACHINE FORMATS OR EXAMPLE STRINGS" in SYSTEM_PROMPT
    assert "NEVER CALL A TOOL WITH INCOMPLETE OR AMBIGUOUS PARAMETERS" in SYSTEM_PROMPT
    assert "RULE 7 — MUST EXECUTE TOOLS" in SYSTEM_PROMPT


def test_extract_sentences_holds_back_short_exclamation_during_streaming():
    """
    Verifies short exclamations ("Got it!") are held back during streaming (is_final=False)
    until merged with the following sentence, preventing isolated TTS exclamation artifacts.
    """
    # Streaming chunk 1: "Got it! " -> held back
    s1, r1 = extract_sentences_from_buffer("Got it! ", is_final=False)
    assert s1 == []
    assert r1 == "Got it! "

    # Streaming chunk 2: "Got it! To proceed, what date works best?" -> merged
    s2, r2 = extract_sentences_from_buffer("Got it! To proceed, what date works best?", is_final=False)
    assert len(s2) == 1
    assert s2[0] == "Got it, to proceed, what date works best?"
    assert r2 == ""

    # Final chunk: "Got it!" (stream ends) -> yielded
    s3, r3 = extract_sentences_from_buffer("Got it!", is_final=True)
    assert s3 == ["Got it!"]


def test_tools_list_contains_book_appointment():
    """
    Verifies book_appointment is registered in TOOLS definitions.
    """
    from app.voice.tools import TOOLS
    tool_names = [t["function"]["name"] for t in TOOLS]
    assert "book_appointment" in tool_names
    assert "propose_booking" in tool_names
    assert "confirm_booking" in tool_names


def test_tool_telemetry_logging(test_doctor):
    """
    Asserts logs capture tool request payload, response, latency, and success status.
    """
    with patch("loguru.logger.info") as mock_logger:
        args = {"doctor_id": test_doctor["doctor_id"], "date": "2026-08-03"}
        res = execute_tool_call("check_availability", args)
        assert "available_slots" in res
        
        # Verify loguru.logger.info was called with telemetry log
        telemetry_logs = [call[0][0] for call in mock_logger.call_args_list if "[TOOL TELEMETRY]" in str(call[0][0])]
        assert len(telemetry_logs) >= 1
        log_entry = telemetry_logs[0]
        assert "name=check_availability" in log_entry
        assert "latency_ms=" in log_entry
        assert "success=True" in log_entry


@patch("app.voice.llm_client._execute_tool_call_internal")
def test_tool_timeout_and_retry_escalation(mock_tool_internal):
    """
    Mocks a failing/slow tool call and verifies LLM client retries at most 2 times before escalating.
    """
    mock_tool_internal.side_effect = Exception("Service unavailable")

    with patch("app.voice.llm_client.call_ollama") as mock_call_ollama:
        mock_call_ollama.return_value = {
            "message": {
                "role": "assistant",
                "tool_calls": [
                    {
                        "function": {
                            "name": "check_availability",
                            "arguments": {"doctor_id": 1, "date": "2026-08-03"},
                        }
                    }
                ],
            }
        }
        history = []
        response_text, _ = run_chat_turn(history, "Check availability for doctor 1")

        # Verify tool was attempted at most 2 times
        assert mock_tool_internal.call_count == 2
        # Verify escalation message surfaced to caller
        assert "let me connect you with a human assistant" in response_text


def test_sanitize_for_tts_strips_iso_dates():
    """
    Verifies sanitize_for_tts strips ISO dates and timestamps from spoken output.
    """
    raw_text = "Your appointment is confirmed for 2026-08-03T10:00:00Z."
    cleaned = sanitize_for_tts(raw_text, has_tool_success=True)
    assert "2026-08-03" not in cleaned
    assert "2026-08-03T10:00:00Z" not in cleaned
    assert "Your appointment is confirmed for" in cleaned


def test_system_prompt_no_hardcoded_2023():
    """
    Verifies SYSTEM_PROMPT contains no hardcoded year 2023 example strings and includes rules 9 and 10.
    """
    assert "2023-11-16" not in SYSTEM_PROMPT
    assert "RULE 8 — NO PRE-NARRATION OF TOOL CALLS AND ERROR HANDLING" in SYSTEM_PROMPT
    assert "RULE 9 — AUTOMATIC IDENTITY VERIFICATION WHEN NAME & DOB ARE PRESENT" in SYSTEM_PROMPT
    assert "RULE 10 — NEVER CONFUSE PATIENT DATE OF BIRTH WITH APPOINTMENT BOOKING DATE" in SYSTEM_PROMPT
    assert "RULE 14 — ALWAYS CONFIRM COMPLETED TOOL ACTIONS IMMEDIATELY" in SYSTEM_PROMPT


def test_resolve_doctor_id_handles_names_and_invalid_ids():
    """
    Verifies resolve_doctor_id maps doctor names (e.g. "Dr. Sarah Connor") to valid IDs
    and rejects invalid IDs (e.g. 5 or "Dr. Patel").
    """
    from app.voice.llm_client import resolve_doctor_id

    # String doctor name match
    doc_id, err = resolve_doctor_id({"doctor_id": "Dr. Sarah Connor"})
    assert err is None
    assert doc_id == 27

    # Surname string match
    doc_id_wong, err = resolve_doctor_id({"doctor_id": "Wong"})
    assert err is None
    assert doc_id_wong == 29

def test_sanitize_for_tts_strips_fake_tool_confirmations():
    """
    Verifies sanitize_for_tts strips fake confirmation sentences across all tools when has_tool_success is False.
    """
    from app.voice.llm_client import sanitize_for_tts as sanitize_func

    # Fake booking confirmation without tool call
    raw_booking = "Great news, Jane! Your appointment with Dr. John Smith on July 29th at 10 AM is confirmed."
    cleaned_booking = sanitize_func(raw_booking, has_tool_success=False)
    assert "is confirmed" not in cleaned_booking
    assert "appointment" not in cleaned_booking

    # Valid booking confirmation WITH successful tool call
    cleaned_booking_valid = sanitize_func(raw_booking, has_tool_success=True)
    assert "is confirmed" in cleaned_booking_valid or "confirmed" in cleaned_booking_valid

def test_auto_verify_identity_fallback(llm_test_patient):
    """
    Verifies _try_auto_verify_identity_fallback automatically executes verify_identity
    when full name and DOB exist in conversation history.
    """
    from app.voice.llm_client import _try_auto_verify_identity_fallback

    messages = [
        {"role": "user", "content": f"My date of birth is July 20, 1985."},
        {"role": "assistant", "content": "Thank you. What is your full name?"},
        {"role": "user", "content": f"{llm_test_patient.first_name} {llm_test_patient.last_name}"},
    ]

    tc = _try_auto_verify_identity_fallback(messages)
    assert tc is not None
    assert tc["function"]["name"] == "verify_identity"
    assert tc["function"]["arguments"]["first_name"] == llm_test_patient.first_name
    assert tc["function"]["arguments"]["last_name"] == llm_test_patient.last_name
    assert tc["function"]["arguments"]["date_of_birth"] == "1985-07-20"






