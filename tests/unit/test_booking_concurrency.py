"""
Proves ADR-005: Postgres's EXCLUDE constraint prevents double-booking under
real concurrent load, not just in a single-threaded happy path.

Each attempt opens its OWN connection so the requests genuinely race at the
database level, the way concurrent phone calls would.
"""
import concurrent.futures

from tests.conftest import get_connection


def attempt_booking(doctor_id, patient_id, start, end):
    """Returns True if the booking succeeded, False if Postgres rejected it."""
    conn = get_connection()
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO appointments (doctor_id, patient_id, appointment_range)
               VALUES (%s, %s, tstzrange(%s, %s))""",
            (doctor_id, patient_id, start, end),
        )
        return True
    except Exception:
        return False
    finally:
        cur.close()
        conn.close()


def test_concurrent_bookings_same_slot_only_one_succeeds(test_doctor):
    doctor_id = test_doctor["doctor_id"]
    patient_id = test_doctor["patient_ids"][0]
    start = "2026-08-01 10:00:00+00"
    end = "2026-08-01 10:30:00+00"

    n_attempts = 10
    with concurrent.futures.ThreadPoolExecutor(max_workers=n_attempts) as executor:
        futures = [
            executor.submit(attempt_booking, doctor_id, patient_id, start, end)
            for _ in range(n_attempts)
        ]
        results = [f.result() for f in futures]

    successes = results.count(True)
    failures = results.count(False)
    assert successes == 1, f"Expected exactly 1 successful booking under concurrency, got {successes}"
    assert failures == n_attempts - 1


def test_concurrent_bookings_different_slots_all_succeed(test_doctor):
    """Sanity check the constraint isn't over-broad — non-overlapping slots
    for the same doctor should all book successfully, even concurrently."""
    doctor_id = test_doctor["doctor_id"]
    patient_id = test_doctor["patient_ids"][0]

    slots = [
        ("2026-08-01 11:00:00+00", "2026-08-01 11:30:00+00"),
        ("2026-08-01 11:30:00+00", "2026-08-01 12:00:00+00"),
        ("2026-08-01 12:00:00+00", "2026-08-01 12:30:00+00"),
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(slots)) as executor:
        futures = [
            executor.submit(attempt_booking, doctor_id, patient_id, start, end)
            for start, end in slots
        ]
        results = [f.result() for f in futures]

    assert all(results), f"Expected all non-overlapping bookings to succeed, got {results}"
