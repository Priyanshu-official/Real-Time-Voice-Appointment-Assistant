"""
JSON schema tool definitions for LLM function calling (Ollama / OpenAI format).
Each tool matches a FastAPI endpoint parameter signature.
"""

from typing import Any, Dict, List

VERIFY_IDENTITY_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "verify_identity",
        "description": "Verifies patient identity using first name, last name, and date of birth before returning patient data or allowing booking operations.",
        "parameters": {
            "type": "object",
            "properties": {
                "first_name": {
                    "type": "string",
                    "description": "Patient's first name",
                },
                "last_name": {
                    "type": "string",
                    "description": "Patient's last name",
                },
                "date_of_birth": {
                    "type": "string",
                    "description": "Patient's date of birth in YYYY-MM-DD format",
                },
            },
            "required": ["first_name", "last_name", "date_of_birth"],
        },
    },
}

CHECK_AVAILABILITY_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "check_availability",
        "description": "Retrieves open 30-minute appointment slots for a specific doctor on a given date.",
        "parameters": {
            "type": "object",
            "properties": {
                "doctor_id": {
                    "type": "integer",
                    "description": "Unique ID of the doctor",
                },
                "date": {
                    "type": "string",
                    "description": "Date to check in YYYY-MM-DD format",
                },
            },
            "required": ["doctor_id", "date"],
        },
    },
}

PROPOSE_BOOKING_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "propose_booking",
        "description": "Validates appointment slot availability and doctor working hours without writing to database. Returns a human-readable summary and confirmation token.",
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {
                    "type": "integer",
                    "description": "Verified patient ID",
                },
                "doctor_id": {
                    "type": "integer",
                    "description": "Doctor ID",
                },
                "start_time": {
                    "type": "string",
                    "description": "Appointment start timestamp in ISO 8601 format (e.g. 2026-08-03T10:00:00Z)",
                },
                "end_time": {
                    "type": "string",
                    "description": "Appointment end timestamp in ISO 8601 format (e.g. 2026-08-03T10:30:00Z)",
                },
                "reason": {
                    "type": "string",
                    "description": "Optional reason for the appointment",
                },
            },
            "required": ["patient_id", "doctor_id", "start_time", "end_time"],
        },
    },
}

CONFIRM_BOOKING_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "confirm_booking",
        "description": "Finalizes and writes an appointment to the database using a valid confirmation token received from propose_booking.",
        "parameters": {
            "type": "object",
            "properties": {
                "confirmation_token": {
                    "type": "string",
                    "description": "Short-lived confirmation token returned by propose_booking",
                },
            },
            "required": ["confirmation_token"],
        },
    },
}

CANCEL_APPOINTMENT_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "cancel_appointment",
        "description": "Cancels an existing appointment. Requires verified patient_id matching the appointment owner.",
        "parameters": {
            "type": "object",
            "properties": {
                "appointment_id": {
                    "type": "integer",
                    "description": "ID of the appointment to cancel",
                },
                "patient_id": {
                    "type": "integer",
                    "description": "Verified patient ID of the caller requesting cancellation",
                },
            },
            "required": ["appointment_id", "patient_id"],
        },
    },
}

RESCHEDULE_APPOINTMENT_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "reschedule_appointment",
        "description": "Reschedules an existing appointment to a new time slot. Requires verified patient_id matching the appointment owner.",
        "parameters": {
            "type": "object",
            "properties": {
                "appointment_id": {
                    "type": "integer",
                    "description": "ID of the existing appointment to reschedule",
                },
                "patient_id": {
                    "type": "integer",
                    "description": "Verified patient ID of the caller",
                },
                "doctor_id": {
                    "type": "integer",
                    "description": "Doctor ID for the new appointment slot",
                },
                "start_time": {
                    "type": "string",
                    "description": "New appointment start timestamp in ISO 8601 format (e.g. 2026-08-03T11:00:00Z)",
                },
                "end_time": {
                    "type": "string",
                    "description": "New appointment end timestamp in ISO 8601 format (e.g. 2026-08-03T11:30:00Z)",
                },
                "reason": {
                    "type": "string",
                    "description": "Optional reason for rescheduling",
                },
            },
            "required": ["appointment_id", "patient_id", "doctor_id", "start_time", "end_time"],
        },
    },
}

TOOLS: List[Dict[str, Any]] = [
    VERIFY_IDENTITY_TOOL,
    CHECK_AVAILABILITY_TOOL,
    PROPOSE_BOOKING_TOOL,
    CONFIRM_BOOKING_TOOL,
    CANCEL_APPOINTMENT_TOOL,
    RESCHEDULE_APPOINTMENT_TOOL,
]


def get_tool_definitions() -> List[Dict[str, Any]]:
    """Returns all LLM function calling tool definitions."""
    return TOOLS

