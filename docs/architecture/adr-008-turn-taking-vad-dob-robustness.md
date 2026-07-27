# ADR-008: Real-Time Turn-Taking, VAD & DOB Verification Robustness

## Status
Accepted

## Context
During live voice testing, several interaction bugs were identified:
1. **Premature Turn-taking**: Pipecat's turn processor emitted LLM responses on partial/incomplete user speech (`EndOfTurnState.INCOMPLETE`), interrupting callers mid-sentence.
2. **Audio Feedback**: Microphone stream in browser lacked WebRTC echo cancellation and noise suppression, causing TTS output played through speakers to re-trigger VAD and disrupt conversations.
3. **STT Accuracy & Hallucinations**: `WhisperSTTService` configured with `tiny.en` misrecognized numbers, months, and clinical terms (*"date of birth"* -> *"rate of worth"*), causing identity verification failures.
4. **DOB Verification Loops**: When callers provided partial date strings (e.g., *"1978, December"*), the LLM called `verify_identity` with incomplete dates (`"1978"`), triggering database lookup failures and infinite re-confirmation prompt loops.
5. **Missing Session Telemetry**: Session history logs were not connected in the live WebSocket server, and failed verification turns were not recorded for model evaluation.

## Issue & Fix Log Registry

| Issue ID | Severity | Description | Fix Log ID | Architectural Resolution |
|---|---|---|---|---|
| `ISSUE-001` | **Major** | VAD/Turn-taking triggering LLM responses on incomplete user speech (`INCOMPLETE` state) | `FIX-LOG-001` | Enforce `EndOfTurnState.COMPLETE` in `UserTurnProcessor` and tune Silero VAD thresholds (`confidence=0.7`, `start_secs=0.3`, `stop_secs=0.4`). |
| `ISSUE-002` | **Major** | Browser mic audio feedback / TTS loop without WebRTC echo cancellation | `FIX-LOG-002` | Enable `echoCancellation: true`, `noiseSuppression: true`, and `autoGainControl: true` in `navigator.mediaDevices.getUserMedia`. |
| `ISSUE-003` | **Major** | `tiny.en` Whisper STT model mishearing numbers and clinical phrases | `FIX-LOG-003` | Upgrade default WebSocket STT model to `distil-small.en` (falling back to `small.en`) via faster-whisper. |
| `ISSUE-004` | **Major** | Infinite `verify_identity` retry loop on partial or unparsed date strings | `FIX-LOG-004` | Add text-to-number date normalizer in `app/api/services/identity.py` and update LLM prompt to ask for missing date components before calling `verify_identity`. |
| `ISSUE-005` | **Minor** | Unwired `ConversationLogger` & missing evaluation dataset for offline tuning | `FIX-LOG-005` | Connect `ConversationLogger` per WebSocket session in `app/voice/server.py` and append failed verification turns to `logs/eval_dataset.jsonl`. |
| `ISSUE-006` | **Minor** | System prompt speaking example date formats (*"August 22, 1992"*) to callers | `FIX-LOG-006` | Explicitly prohibit speaking example date format strings to callers in `llm_client.py` system prompt. |
| `ISSUE-007` | **Major** | Verified session state loss causing LLM to re-ask for patient identity | `FIX-LOG-007` | Add strict persistence rule in `SYSTEM_PROMPT`: once `verify_identity` returns `verified=true`, identity is locked as verified for the rest of the session. |
| `ISSUE-008` | **Minor** | TTS speaking lone punctuation fragments (`"."`, `"!"`, `"'"`)| `FIX-LOG-008` | Require `any(c.isalnum() for c in text)` check in `LLMToolCallingProcessor` before pushing `TextFrame` objects down to `PiperTTSService`. |

## Decision

1. **Turn Strategy**: Configure `UserTurnProcessor` to require complete speech evaluation (`EndOfTurnState.COMPLETE`) before pushing frames down the pipeline. Tune Silero VAD analyzer parameters (`confidence=0.7`, `start_secs=0.3`, `stop_secs=0.4`, `min_volume=0.6`).
2. **Audio Preprocessing**: Require standard WebRTC constraints (`echoCancellation`, `noiseSuppression`, `autoGainControl`) on browser microphone audio streams in `app/frontend/index.html`.
3. **STT Engine**: Standardize default voice server model to `distil-small.en` via CTranslate2 faster-whisper.
4. **Identity Verification Guardrails**:
   - Add text-to-number normalizer (`"nineteen seventy eight"` -> `"1978"`) in `verify_patient_identity`.
   - Constrain LLM System Prompt: do NOT execute `verify_identity` until month, day, and year are present. Prompt the caller specifically for missing date components.
5. **Session Telemetry**: Instantiate `ConversationLogger` per WebSocket session in `app/voice/server.py` and write session logs to `logs/conversations/` and failed verification turns to `logs/eval_dataset.jsonl`.

## Rationale
- Preventing premature turn completion stops the voice assistant from interrupting callers while they think or pause mid-sentence.
- Echo cancellation eliminates feedback loops caused by speaker output re-entering the microphone.
- `distil-small.en` provides a ~5x WER reduction over `tiny.en` while remaining well within the Mac Metal real-time latency budget (~200ms STT latency).
- Text-to-number date normalization and partial date guardrails prevent invalid tool execution and break verification loops.

## Consequences
- **Positive**: Fluid natural conversation, high DOB accuracy, zero echo feedback, clean session telemetry.
- **Negative**: Marginally higher CPU/VRAM usage for `distil-small.en` vs `tiny.en` (~100MB VRAM difference).
- **Mitigation**: Mac Metal acceleration (ADR-003) handles `distil-small.en` with ease.

## Revisit Trigger
- If end-to-end latency exceeds 1500ms on slower host hardware, evaluate `base.en` as a fallback.
