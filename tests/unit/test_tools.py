from app.voice.tools import TOOLS, get_tool_definitions


def test_tool_count_and_names():
    definitions = get_tool_definitions()
    assert len(definitions) == 5
    tool_names = [t["function"]["name"] for t in definitions]
    expected_names = [
        "verify_identity",
        "check_availability",
        "book_appointment",
        "cancel_appointment",
        "reschedule_appointment",
    ]
    assert tool_names == expected_names


def test_tool_parameters_match_endpoints():
    tool_map = {t["function"]["name"]: t["function"] for t in TOOLS}

    # 1. verify_identity
    vi = tool_map["verify_identity"]
    assert set(vi["parameters"]["required"]) == {"first_name", "last_name", "date_of_birth"}

    # 2. check_availability
    ca = tool_map["check_availability"]
    assert set(ca["parameters"]["required"]) == {"doctor_id", "date"}

    # 3. book_appointment
    ba = tool_map["book_appointment"]
    assert set(ba["parameters"]["required"]) == {"patient_id", "doctor_id", "start_time", "end_time"}

    # 4. cancel_appointment
    canc = tool_map["cancel_appointment"]
    assert set(canc["parameters"]["required"]) == {"appointment_id", "patient_id"}

    # 5. reschedule_appointment
    resch = tool_map["reschedule_appointment"]
    assert set(resch["parameters"]["required"]) == {"appointment_id", "patient_id", "doctor_id", "start_time", "end_time"}


def test_tool_descriptions_non_empty():
    for tool in TOOLS:
        fn = tool["function"]
        assert "description" in fn
        assert isinstance(fn["description"], str)
        assert len(fn["description"].strip()) > 10
