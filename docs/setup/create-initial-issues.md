# Task: create the first batch of atomic issues

Use the GitHub MCP server to create one GitHub issue per section below, in the order listed (later issues depend on earlier ones — mention the dependency by title in the issue body since issue numbers aren't known yet).

For each issue:
- Title: as given
- Labels: as given, plus `status:ready` and `size:atomic` on every issue
- Body: assembled from the fields below, following the structure of `.github/ISSUE_TEMPLATE/atomic-task.md`

Do not start implementing any of these — only create the issues. After creating all of them, comment a summary list (issue number + title) so a human can review the order before assigning agents to work them.

---

## Issue 1: FastAPI project scaffold

**Labels**: `type:concurrency` not needed

**Scope**:
- `app/api/main.py`, `app/api/config.py`, `pyproject.toml` (add `fastapi`, `uvicorn`, `sqlalchemy`, `psycopg2-binary` as runtime deps via uv)

**Out of scope**:
- Any actual endpoint logic beyond a health check
- Docker changes

**Acceptance criteria**:
- [ ] `uv run uvicorn app.api.main:app --reload` starts without error
- [ ] `GET /health` returns `{"status": "ok"}`
- [ ] Config loads `DATABASE_URL`, `OLLAMA_HOST`, `OLLAMA_MODEL` from `.env` via pydantic-settings (add as a dep)
- [ ] No hardcoded credentials anywhere

**Relevant ADR/skill**: ADR-006 (deployment topology)

---

## Issue 2: SQLAlchemy models + DB session dependency

**Depends on**: FastAPI project scaffold

**Scope**:
- `app/db/models.py` (SQLAlchemy models mirroring `app/db/init/02-schema.sql` exactly — do not change the schema, only reflect it)
- `app/db/session.py` (engine + session dependency for FastAPI)

**Out of scope**:
- `app/db/init/*.sql` (schema is already finalized — models must match it, not the other way around)
- Any endpoint code

**Acceptance criteria**:
- [ ] Models exist for `Specialty`, `Doctor`, `Patient`, `Appointment`, `AuditLog`
- [ ] A FastAPI dependency `get_db()` yields a session and closes it properly
- [ ] Existing `tests/unit/` tests still pass unmodified (models must not require schema changes)

**Relevant ADR/skill**: ADR-005 (concurrency-safe booking)

---

## Issue 3: Seed script for sample data

**Depends on**: SQLAlchemy models

**Scope**:
- `app/db/seed.py`

**Out of scope**:
- Schema changes
- API endpoints

**Acceptance criteria**:
- [ ] Running `uv run python -m app.db.seed` inserts 2-3 specialties, 3-4 doctors with realistic working hours, and exactly 3 sample patients (needed for requirement #8 — "2-3 sample patient identities ready to try")
- [ ] Script is idempotent — running it twice does not create duplicates or error
- [ ] Sample patient identities (name + DOB) are printed to stdout so they can be copied into the README later

**Relevant ADR/skill**: none — this is data setup only

---

## Issue 4: Doctors and availability read endpoints

**Depends on**: Seed script

**Scope**:
- `app/api/routes/doctors.py`
- Registration of the router in `app/api/main.py`

**Out of scope**:
- Booking/write endpoints (separate issue)
- Identity verification (separate issue)

**Acceptance criteria**:
- [ ] `GET /doctors` lists doctors with specialty name
- [ ] `GET /doctors/{id}/availability?date=YYYY-MM-DD` returns open slots for that doctor on that date, correctly excluding already-booked ranges and respecting `working_start_time`/`working_end_time`/`working_days`
- [ ] No patient data is exposed by these endpoints (they're doctor/schedule info only, no identity check needed)

**Relevant ADR/skill**: booking-domain skill (once created)

---

## Issue 5: Identity verification service

**Depends on**: SQLAlchemy models

**Scope**:
- `app/api/services/identity.py`
- `app/api/routes/identity.py` (a `POST /verify-identity` endpoint used internally by the tool layer, not directly by callers)

**Out of scope**:
- Any endpoint that returns patient data (that's Issue 6 and beyond — those endpoints call this service, they don't duplicate its logic)

**Acceptance criteria**:
- [ ] Given `first_name`, `last_name`, `date_of_birth`, returns a verified `patient_id` only on an exact match against the `patients` table
- [ ] Returns a generic "not verified" response on any mismatch — never reveals which field was wrong (avoids leaking which patients exist)
- [ ] Every verification attempt (success or failure) writes a row to `audit_log`
- [ ] Unit test proves an unverified caller cannot obtain a `patient_id` through this endpoint

**Relevant ADR/skill**: ADR-007 (identity verification)

---

## Issue 6: Booking endpoint (create appointment)

**Depends on**: Identity verification service, Doctors/availability endpoints

**Scope**:
- `app/api/routes/appointments.py` (POST for create only in this issue)
- `app/api/services/booking.py`

**Out of scope**:
- Cancel/reschedule (separate issue)
- Voice/LLM integration

**Acceptance criteria**:
- [ ] Endpoint requires a verified `patient_id` (from Issue 5) — no `patient_id` may be passed directly by the caller without having gone through verification in this session
- [ ] On a Postgres exclusion-constraint violation, returns a clean 409 response (e.g. "that slot is no longer available") — does not crash or leak the raw DB error
- [ ] On a working-hours-trigger violation, returns a clean 400 response
- [ ] Every booking attempt (success or rejection) writes a row to `audit_log`
- [ ] Existing concurrency tests in `tests/unit/test_booking_concurrency.py` still pass — add an equivalent test that hits this endpoint directly (not just the DB) with concurrent requests

**Relevant ADR/skill**: ADR-005, ADR-007

---

## Issue 7: Cancel / reschedule endpoint

**Depends on**: Booking endpoint

**Scope**:
- `app/api/routes/appointments.py` (PATCH/DELETE for cancel and reschedule)

**Out of scope**:
- Create endpoint (already exists from Issue 6)

**Acceptance criteria**:
- [ ] Cancel requires verified identity matching the `patient_id` on the appointment being cancelled — cannot cancel another patient's appointment even with a valid `appointment_id`
- [ ] Reschedule is implemented as cancel + create, going through the same exclusion-constraint and working-hours checks as a fresh booking
- [ ] Every action writes to `audit_log`

**Relevant ADR/skill**: ADR-005, ADR-007

---

## Issue 8: Tool schema definitions for LLM function calling

**Depends on**: Booking endpoint, Cancel/reschedule endpoint, Doctors/availability endpoints

**Scope**:
- `app/voice/tools.py` (JSON-schema tool definitions matching the FastAPI endpoints exactly)

**Out of scope**:
- The Ollama client/tool-calling loop itself (separate issue)
- Any Pipecat/audio code

**Acceptance criteria**:
- [ ] One tool definition per endpoint: `verify_identity`, `check_availability`, `book_appointment`, `cancel_appointment`, `reschedule_appointment`
- [ ] Each tool's parameters exactly match its endpoint's request body — no extra fields the endpoint doesn't accept
- [ ] A short docstring/description per tool suitable for use in an LLM system prompt

**Relevant ADR/skill**: ADR-003 (LLM model), booking-domain skill (once created)

---

## Issue 9: Ollama tool-calling client wrapper

**Depends on**: Tool schema definitions

**Scope**:
- `app/voice/llm_client.py`

**Out of scope**:
- Pipecat pipeline wiring (separate issue)
- Audio/STT/TTS

**Acceptance criteria**:
- [ ] Given a conversation history and the tool schemas from Issue 8, calls the local Ollama model (`OLLAMA_HOST`/`OLLAMA_MODEL` from config) and returns either a text response or a structured tool call
- [ ] When the model emits a tool call, this module executes the corresponding FastAPI call and feeds the result back to the model for a final response (single round-trip loop, not open-ended)
- [ ] A basic system prompt is included here that states the identity-before-data rule explicitly (defense in depth alongside the code-level enforcement in Issue 5/6/7)
- [ ] A simple text-mode script (`app/voice/chat_test.py`) lets you manually type messages and see tool calls happen, without any audio — used for fast iteration before wiring up Pipecat

**Relevant ADR/skill**: ADR-003

---

After creating all 9 issues, comment a summary on this issue with the resulting issue numbers in order, so the human can decide which to assign to agents first.
