-- Core schema for the voice appointment assistant.
-- See docs/architecture/adr-005-concurrency-safe-booking.md for the
-- reasoning behind the EXCLUDE constraint below.

CREATE TABLE specialties (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE
);

CREATE TABLE doctors (
    id                  SERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    specialty_id        INTEGER NOT NULL REFERENCES specialties(id),
    -- simple fixed working hours per doctor; days: 0=Sunday .. 6=Saturday
    working_days        INTEGER[] NOT NULL DEFAULT '{1,2,3,4,5}',
    working_start_time  TIME NOT NULL DEFAULT '09:00',
    working_end_time    TIME NOT NULL DEFAULT '17:00'
);

CREATE TABLE patients (
    id              SERIAL PRIMARY KEY,
    first_name      TEXT NOT NULL,
    last_name       TEXT NOT NULL,
    date_of_birth   DATE NOT NULL,
    phone           TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE appointments (
    id                  SERIAL PRIMARY KEY,
    doctor_id           INTEGER NOT NULL REFERENCES doctors(id),
    patient_id          INTEGER NOT NULL REFERENCES patients(id),
    appointment_range   TSTZRANGE NOT NULL,
    status              TEXT NOT NULL DEFAULT 'booked'
                         CHECK (status IN ('booked', 'cancelled')),
    reason              TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Structural double-booking prevention (ADR-005): two 'booked'
    -- appointments for the same doctor can never have overlapping ranges.
    -- This is enforced by Postgres itself, independent of application code,
    -- and holds under concurrent inserts.
    EXCLUDE USING gist (
        doctor_id WITH =,
        appointment_range WITH &&
    ) WHERE (status = 'booked')
);

CREATE INDEX idx_appointments_doctor_range ON appointments USING gist (doctor_id, appointment_range);

-- Working-hours enforcement (part of "availability logic" requirement).
-- A CHECK constraint can't reference another table, so this is a trigger.
CREATE OR REPLACE FUNCTION enforce_doctor_working_hours()
RETURNS TRIGGER AS $$
DECLARE
    doc doctors%ROWTYPE;
    start_ts TIMESTAMPTZ := lower(NEW.appointment_range);
    end_ts   TIMESTAMPTZ := upper(NEW.appointment_range);
BEGIN
    SELECT * INTO doc FROM doctors WHERE id = NEW.doctor_id;

    IF NOT (EXTRACT(DOW FROM start_ts)::INTEGER = ANY(doc.working_days)) THEN
        RAISE EXCEPTION 'Doctor % does not work on this day', NEW.doctor_id;
    END IF;

    IF start_ts::TIME < doc.working_start_time OR end_ts::TIME > doc.working_end_time THEN
        RAISE EXCEPTION 'Appointment is outside doctor % working hours (% - %)',
            NEW.doctor_id, doc.working_start_time, doc.working_end_time;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_enforce_working_hours
    BEFORE INSERT OR UPDATE ON appointments
    FOR EACH ROW
    WHEN (NEW.status = 'booked')
    EXECUTE FUNCTION enforce_doctor_working_hours();

-- Audit trail (ADR-007): every data-access or booking action gets a row here,
-- regardless of whether the LLM's tool call was ultimately allowed or denied.
CREATE TABLE audit_log (
    id          SERIAL PRIMARY KEY,
    patient_id  INTEGER REFERENCES patients(id),
    action      TEXT NOT NULL,
    allowed     BOOLEAN NOT NULL,
    details     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_log_patient ON audit_log (patient_id, created_at);
