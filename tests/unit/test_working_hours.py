"""
Proves the "working hours respected" part of the availability-logic
requirement — the trg_enforce_working_hours trigger in 02-schema.sql.
"""
import psycopg2
import pytest

from tests.conftest import get_connection


def _insert_appointment(doctor_id, patient_id, start, end):
    conn = get_connection()
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO appointments (doctor_id, patient_id, appointment_range)
               VALUES (%s, %s, tstzrange(%s, %s))""",
            (doctor_id, patient_id, start, end),
        )
    finally:
        cur.close()
        conn.close()


def test_booking_within_working_hours_succeeds(test_doctor_normal_hours):
    doctor_id = test_doctor_normal_hours["doctor_id"]
    patient_id = test_doctor_normal_hours["patient_id"]
    # 2026-08-03 is a Monday, 10am — within Mon-Fri 9-5
    _insert_appointment(doctor_id, patient_id, "2026-08-03 10:00:00+00", "2026-08-03 10:30:00+00")


def test_booking_after_hours_is_rejected(test_doctor_normal_hours):
    doctor_id = test_doctor_normal_hours["doctor_id"]
    patient_id = test_doctor_normal_hours["patient_id"]
    # Same Monday, 8pm — outside 9-5
    with pytest.raises(psycopg2.Error, match="working hours"):
        _insert_appointment(doctor_id, patient_id, "2026-08-03 20:00:00+00", "2026-08-03 20:30:00+00")


def test_booking_on_non_working_day_is_rejected(test_doctor_normal_hours):
    doctor_id = test_doctor_normal_hours["doctor_id"]
    patient_id = test_doctor_normal_hours["patient_id"]
    # 2026-08-08 is a Saturday — doctor only works Mon-Fri
    with pytest.raises(psycopg2.Error, match="does not work"):
        _insert_appointment(doctor_id, patient_id, "2026-08-08 10:00:00+00", "2026-08-08 10:30:00+00")
