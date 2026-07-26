import os
import psycopg2
import pytest
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://clinic_app:localdevpassword@localhost:5432/clinic",
)


def get_connection():
    """A fresh connection per call — required for real concurrency testing.
    Sharing one psycopg2 connection across threads would serialize the
    requests and defeat the point of this test.
    """
    return psycopg2.connect(DATABASE_URL)


@pytest.fixture
def test_doctor():
    """Creates an isolated specialty + doctor + two patients for a test,
    and cleans up afterward regardless of test outcome."""
    conn = get_connection()
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO specialties (name) VALUES (%s) RETURNING id",
        ("Test Specialty - pytest",),
    )
    specialty_id = cur.fetchone()[0]

    cur.execute(
        """INSERT INTO doctors (name, specialty_id, working_days, working_start_time, working_end_time)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        ("Dr. Test", specialty_id, [0, 1, 2, 3, 4, 5, 6], "00:00", "23:59"),
    )
    doctor_id = cur.fetchone()[0]

    patient_ids = []
    for first, last in [("Alice", "Test"), ("Bob", "Test")]:
        cur.execute(
            """INSERT INTO patients (first_name, last_name, date_of_birth, phone)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (first, last, "1990-01-01", "5550000"),
        )
        patient_ids.append(cur.fetchone()[0])

    yield {"doctor_id": doctor_id, "patient_ids": patient_ids}

    # Teardown — delete in FK-safe order
    cur.execute("DELETE FROM audit_log WHERE patient_id = ANY(%s)", (patient_ids,))
    cur.execute("DELETE FROM appointments WHERE doctor_id = %s", (doctor_id,))
    cur.execute("DELETE FROM patients WHERE id = ANY(%s)", (patient_ids,))
    cur.execute("DELETE FROM doctors WHERE id = %s", (doctor_id,))
    cur.execute("DELETE FROM specialties WHERE id = %s", (specialty_id,))
    cur.close()
    conn.close()


@pytest.fixture
def test_doctor_normal_hours():
    """Same as test_doctor, but with realistic Mon-Fri 9am-5pm hours —
    used specifically for testing the working-hours trigger, which the
    wide-open test_doctor fixture deliberately bypasses."""
    conn = get_connection()
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO specialties (name) VALUES (%s) RETURNING id",
        ("Test Specialty Normal Hours - pytest",),
    )
    specialty_id = cur.fetchone()[0]

    cur.execute(
        """INSERT INTO doctors (name, specialty_id, working_days, working_start_time, working_end_time)
           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
        ("Dr. Normal Hours", specialty_id, [1, 2, 3, 4, 5], "09:00", "17:00"),
    )
    doctor_id = cur.fetchone()[0]

    cur.execute(
        """INSERT INTO patients (first_name, last_name, date_of_birth, phone)
           VALUES (%s, %s, %s, %s) RETURNING id""",
        ("Carol", "Test", "1990-01-01", "5550003"),
    )
    patient_id = cur.fetchone()[0]

    yield {"doctor_id": doctor_id, "patient_id": patient_id}

    cur.execute("DELETE FROM audit_log WHERE patient_id = %s", (patient_id,))
    cur.execute("DELETE FROM appointments WHERE doctor_id = %s", (doctor_id,))
    cur.execute("DELETE FROM patients WHERE id = %s", (patient_id,))
    cur.execute("DELETE FROM doctors WHERE id = %s", (doctor_id,))
    cur.execute("DELETE FROM specialties WHERE id = %s", (specialty_id,))
    cur.close()
    conn.close()
