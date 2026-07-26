# ADR-001: Voice orchestration framework

## Status
Accepted

## Context
- **Problem**: Need streaming STT → LLM → TTS with barge-in (patient can interrupt mid-response) at ~1-2s latency, self-hostable and open-source.
- **Constraints**: 48-hour build, deployed by 28 July, solo/small team, "real-time voice + barge-in" is a 20-point graded criterion.

## Options Considered

| Option | Pros | Cons | Complexity | When Valid |
|--------|------|------|------------|-----------|
| Custom WebSocket + manual VAD/turn-taking | Full control, no framework lock-in | Barge-in/turn-taking is the hardest part of this whole build; high bug risk under time pressure | High | Only if requirements need behavior no framework supports |
| Pipecat (open-source voice agent orchestration) | Built-in VAD-based interruption, streaming turn management, ready connectors for whisper/Ollama/Piper | Framework opinions to learn; less control over edge cases | Low-Medium | Standard voice-agent shape, which this is |
| LiveKit Agents (open-source) | Also handles interruption, more infra-heavy (SFU) | Extra infra (LiveKit server) not needed since telephony/WebRTC-at-scale is explicitly out of scope | Medium | Multi-party or production telephony-scale calls |

## Decision
**Chosen**: Pipecat

## Rationale
1. Barge-in is graded and is also the single hardest engineering problem in this brief — reusing a maintained implementation converts a multi-day R&D risk into a config problem.
2. Requirement #1 explicitly needs streaming STT→LLM→TTS with interruption; Pipecat's pipeline model maps directly onto this without custom glue code.
3. Telephony is explicitly out of scope, so LiveKit's SFU/room infrastructure is unneeded weight — Pipecat's lighter footprint fits the "simulate the call in-app" requirement better.

## Trade-offs
- We give up full control over turn-taking internals and accept the framework's opinions on pipeline structure.
- If a genuinely novel interruption behavior is needed later, we're constrained by what Pipecat exposes.

## Consequences
- **Positive**: Barge-in and streaming latency work out of the box; time saved goes into the higher-weight privacy/concurrency requirements.
- **Negative**: Debugging is partly "framework debugging" rather than full visibility into audio buffering.
- **Mitigation**: Keep the STT/LLM/TTS components swappable (faster-whisper, Ollama, Piper are all standard Pipecat integrations) so we're not locked into specific model choices, only the orchestration layer.

## Revisit Trigger
- If telephony (real PSTN calls) becomes in-scope later, re-evaluate LiveKit or a SIP-capable alternative.
