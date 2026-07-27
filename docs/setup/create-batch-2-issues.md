# Task: create batch 2 of atomic issues (voice, eval, load, deploy)

Same instructions as the first batch: create one GitHub issue per section, in order, using the atomic-task template. Do not implement anything — only create issues. Comment a summary with resulting issue numbers when done.

---

## Issue: Pipecat pipeline skeleton (no barge-in yet)

**Depends on**: Ollama tool-calling client wrapper (issue #9 from batch 1)

**Scope**:
- `app/voice/pipeline.py`
- `app/voice/audio_test_fixtures/` (a couple of short sample WAV files for manual testing)

**Out of scope**:
- Barge-in/VAD interruption logic (separate issue)
- Browser UI / websocket transport (separate issue)

**Acceptance criteria**:
- [ ] Pipecat pipeline wired: faster-whisper STT → `llm_client.py` (from issue #9) → Piper TTS
- [ ] Given a pre-recorded WAV file as input, produces a spoken response as output (file-in, file-out, no live mic/websocket yet)
- [ ] Measured end-to-end latency (STT start to first TTS audio byte) is logged/printed for one sample run
- [ ] No barge-in expected to work yet — that's the next issue

**Relevant ADR/skill**: ADR-001, ADR-002, ADR-004

---

## Issue: Barge-in / VAD interruption

**Depends on**: Pipecat pipeline skeleton

**Scope**:
- `app/voice/pipeline.py` (extend, don't rewrite)

**Out of scope**:
- Browser UI (separate issue)

**Acceptance criteria**:
- [ ] Silero VAD (or Pipecat's built-in VAD) detects speech while TTS is playing and cancels the in-progress TTS output
- [ ] After interruption, the pipeline correctly starts listening to the new input rather than continuing the old response
- [ ] A manual test procedure is documented in the PR description (since this is hard to unit-test meaningfully) — e.g. "play sample A, interrupt partway with sample B, confirm output reflects B only"

**Relevant ADR/skill**: ADR-001 (this is the requirement that ADR exists to satisfy)

---

## Issue: WebSocket transport + browser call UI

**Depends on**: Barge-in / VAD interruption

**Scope**:
- `app/voice/server.py` (websocket endpoint wrapping the Pipecat pipeline)
- `app/frontend/index.html` (single-file HTML/JS: call button, mic capture via getUserMedia, audio playback)

**Out of scope**:
- Any backend pipeline logic (already done)
- Styling beyond basic usability

**Acceptance criteria**:
- [ ] Pressing "Call" in the browser opens a websocket, streams mic audio to the server, and plays back streamed TTS audio
- [ ] Barge-in works through this real transport, not just the file-based test from the prior issue
- [ ] Works end-to-end locally (`localhost`) before any tunnel/deployment step

**Relevant ADR/skill**: ADR-001

---

## Issue: Adversarial and ambiguous eval set

**Depends on**: Ollama tool-calling client wrapper (issue #9), Identity verification (issue #5)

**Scope**:
- `tests/eval/transcripts/` (transcript files, one scenario per file)
- `tests/eval/test_eval_set.py` (runs each transcript through `llm_client.py` in text mode, asserts expected tool-call behavior or refusal)

**Out of scope**:
- Audio/voice — this runs entirely in text mode against the LLM+tools, per the original architecture plan (fast iteration, no STT/TTS needed to test this)

**Acceptance criteria**:
- [ ] At least 3 adversarial transcripts: attempts to extract another patient's data, attempts to bypass identity verification via prompt injection, attempts to get the model to ignore its system prompt rules
- [ ] At least 3 ambiguous/messy transcripts: unclear dates ("next Tuesday sometime"), partial identity info, contradictory requests
- [ ] Each transcript has an documented expected outcome (refuse / ask clarifying question / correct tool call) and the test asserts against it
- [ ] Results summary (pass/fail per transcript) is printed in a format that can be pasted into the README

**Relevant ADR/skill**: ADR-003, ADR-007

---

## Issue: Load test script

**Depends on**: Ollama tool-calling client wrapper (issue #9)

**Scope**:
- `tests/load/load_test.py`

**Out of scope**:
- Audio — this simulates concurrent text-mode conversations hitting the LLM+tools+DB, which is sufficient to demonstrate load behavior per the original architecture plan

**Acceptance criteria**:
- [ ] Simulates 10-20 concurrent "calls" (asyncio tasks), each running a short scripted conversation (check availability → book) against the running FastAPI + Ollama stack
- [ ] Reports: latency per request (min/median/p95/max), success rate, and any errors encountered (e.g. exclusion-constraint rejections from genuinely colliding simulated slots, which is expected/correct behavior, not a bug)
- [ ] Output is a simple printed report or a small JSON/CSV file, not a dashboard — keep this proportional to what's needed

**Relevant ADR/skill**: ADR-005, ADR-006

---

## Issue: docker-compose api service + Cloudflare Tunnel

**Depends on**: WebSocket transport + browser call UI

**Scope**:
- `docker-compose.yml` (add an `api` service for FastAPI + the voice server; Postgres service stays as-is)
- `docs/deployment.md` (Cloudflare Tunnel setup steps)

**Out of scope**:
- Any application code changes
- Ollama (stays native on the Mac host per ADR-003/006 — do not containerize it)

**Acceptance criteria**:
- [ ] `docker compose up -d` brings up Postgres + the api/voice service together, api service reaches Ollama via `host.docker.internal`
- [ ] `docs/deployment.md` documents the exact `cloudflared` commands to expose the local stack via a public URL
- [ ] One full manual test: fresh `docker compose up`, tunnel started, call the public URL from a phone or another network, confirm the voice flow works end-to-end

**Relevant ADR/skill**: ADR-006

---

After creating all issues, comment the summary with issue numbers so a human can sequence agent assignments.
