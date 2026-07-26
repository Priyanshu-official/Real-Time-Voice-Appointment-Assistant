# AGENTS.md — golden rules for this repo

Read this before starting any issue. If a rule here conflicts with an issue description, this file wins — ask before proceeding rather than guessing.

## Scope discipline (read first)
- Touch only the files/directories listed under **Scope** in your assigned issue. Nothing else.
- If the task genuinely requires touching a file outside that scope, stop and comment on the issue explaining why — don't silently expand scope.
- Do not refactor, rename, or "clean up" code outside your issue's scope, even if you notice something wrong. Open a new issue for it instead.

## Tech stack (do not substitute — see linked ADRs for why)
- Orchestration: Pipecat — ADR-001
- STT: faster-whisper — ADR-002
- LLM: 7-8B instruct, 4-bit, via Ollama (Mac, Metal-accelerated) — ADR-003
- TTS: Piper — ADR-004
- DB: Postgres with `EXCLUDE` constraint (btree_gist) for booking — ADR-005
- Deployment: single docker-compose stack on the Mac, exposed via Cloudflare Tunnel — ADR-006
- Identity: name + DOB (or patient ID + phone last-4) challenge, checked server-side — ADR-007

Don't introduce a different framework, model, or library for something already decided in an ADR. If you think an ADR is wrong, comment on the issue — don't route around it silently.

## Non-negotiables
1. **Concurrency**: booking writes must go through the DB's `EXCLUDE` constraint (ADR-005). Never add app-level locking as a substitute or workaround.
2. **Identity before data**: no tool call may return patient data unless the FastAPI layer has verified the caller's identity for that specific `patient_id` in this session. This check lives in code, not in the LLM prompt (ADR-007).
3. **Audit trail**: every data-access tool call writes a row to the audit log (patient_id, action, timestamp). No exceptions, including for "just testing."
4. **No secrets in code or commit messages.** Connection strings, keys, tokens go in `.env`, which is gitignored.

## Before opening a PR
- Run the relevant test suite for your change (`tests/unit/` at minimum). Do not open a PR with failing tests.
- If your change touches booking logic, run the concurrency test specifically.
- If your change touches the LLM prompt or tool-calling logic, run the relevant subset of `tests/eval/`.
- Self-check your diff against your issue's acceptance criteria before requesting review — don't rely on the Judge to catch things you already know are missing.

## Conventions
- Branch name: `issue-<number>-<short-slug>`
- Commit format: `<type>: <short description> (#<issue-number>)` — types: `feat`, `fix`, `test`, `docs`, `refactor`
- PR description must link the issue (`Closes #<number>`) and list which tests were run
- Keep PRs small — one issue, one PR. If your change grew beyond the issue's scope, split it.

## Identity and attribution
- At the start of each work session, before your first commit, run (repo-local, not --global):
  ```bash
  git config user.name "Antigravity Agent"
  git config user.email "antigravity-agent@users.noreply.github.com"
  ```
- Apply the `source:agent` label to every PR you open.
- Fill in the `**Source**` line at the top of the PR description with "Antigravity agent (issue #<number>)".

## When to stop and ask instead of proceeding
- The issue's acceptance criteria are ambiguous or contradictory
- The task requires a schema change not already described in an ADR
- The task requires touching authentication/identity logic beyond what's in ADR-007
- Any test in `tests/unit/` fails for a reason unrelated to your change (don't silently skip or delete it)

## Reference
- Architecture decisions: `docs/architecture/adr-*.md`
- Grading rubric this project is built against: see the Judge's `judge-rubric` skill
