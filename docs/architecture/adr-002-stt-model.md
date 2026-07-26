# ADR-002: Speech-to-text model

## Status
Accepted

## Context
- **Problem**: Need streaming, open-source, self-hosted STT that fits inside a 1-2s end-to-end latency budget.
- **Constraints**: CPU or modest GPU (TBC), 48-hour build, latency and voice quality are a 20-point criterion.

## Options Considered

| Option | Pros | Cons | Complexity | When Valid |
|--------|------|------|------------|-----------|
| whisper-large-v3 | Best accuracy | Too slow for 1-2s streaming budget on modest hardware | Low (to run) | Offline transcription, not real-time |
| faster-whisper distil-small/medium | Near-large accuracy, CTranslate2 speed, streams well | Slightly lower accuracy on noisy audio | Low | Real-time voice agents on CPU/small GPU — this case |
| Vosk / wav2vec2 small models | Very fast | Materially worse accuracy, weaker punctuation/formatting | Low | Extremely latency-constrained, low-stakes transcription |

## Decision
**Chosen**: faster-whisper, distil-small or distil-medium (pick based on measured latency once hardware is confirmed)

## Rationale
1. The 1-2s end-to-end latency budget (STT+LLM+TTS combined) doesn't leave room for whisper-large's inference time.
2. Accuracy loss vs. large is small and largely recoverable with domain-constrained prompting (clinic-specific vocabulary); latency overruns are not recoverable under a hard budget.
3. faster-whisper is a native Pipecat integration, so it doesn't add integration risk on top of the model trade-off.

## Trade-offs
- Slightly higher word-error-rate than large, especially on noisy audio or strong accents.
- Accepting this because the grading criterion rewards responsive, interruptible conversation over perfect transcription.

## Consequences
- **Positive**: Keeps us inside the latency budget; frees compute for the LLM step.
- **Negative**: Occasional misrecognition of names/dates — a common source of booking errors.
- **Mitigation**: LLM prompt explicitly confirms critical fields (date, doctor name, patient identity) back to the caller before committing a booking.

## Revisit Trigger
- If measured latency has headroom after deployment, step up to distil-large-v3 for accuracy.

## Hardware Note (resolved 26 Jul)
Dev machine has an NVIDIA RTX 3050 Ti Laptop GPU (4GB VRAM) — confirmed via Device Info, correcting an earlier reading that showed integrated graphics only. faster-whisper runs GPU-accelerated locally for dev at low latency, even at distil-medium. This does not change the model choice, only confirms local dev can be GPU-accelerated rather than CPU-only.
