# ADR-004: Text-to-speech engine

## Status
Accepted

## Context
- **Problem**: Need open-source, self-hostable TTS that streams fast enough to preserve the 1-2s latency budget and supports being cut off mid-utterance for barge-in.
- **Constraints**: Voice quality is bundled into a 20-point criterion alongside barge-in — barge-in responsiveness matters more than voice naturalness.

## Options Considered

| Option | Pros | Cons | Complexity | When Valid |
|--------|------|------|------------|-----------|
| Coqui XTTS | High-quality, natural-sounding voice, voice cloning | Noticeably slower inference, heavier to serve reliably in 2 days | Medium-High | Voice quality is the primary criterion and latency budget is relaxed |
| Piper | Fast, CPU-friendly, streams well, simple to self-host | Less natural/expressive than XTTS | Low | Real-time, latency-constrained voice agents — this case |
| espeak-ng | Extremely fast | Robotic, low-quality voice | Very Low | Latency is the only concern, quality irrelevant |

## Decision
**Chosen**: Piper

## Rationale
1. The graded criterion combines "real-time voice quality **and barge-in**" — a barge-in that lags because TTS synthesis is slow undermines the interruption experience more than a slightly less natural voice does.
2. Piper is CPU-viable, which de-risks deployment if GPU access is limited.
3. Piper is a native Pipecat integration, minimizing integration time.

## Trade-offs
- Voice sounds noticeably more synthetic than XTTS would produce.
- Accepting this because responsive barge-in is the harder, more heavily-weighted engineering problem to get right.

## Consequences
- **Positive**: Predictable, low-latency synthesis; reliable to deploy in the time available.
- **Negative**: Less pleasant listening experience than a premium TTS voice.
- **Mitigation**: Choose one of Piper's better-quality voice models (e.g. a medium-quality English voice) as a middle ground; document the trade-off explicitly in the README rather than hiding it.

## Revisit Trigger
- If latency testing shows significant headroom, evaluate Coqui XTTS as a quality upgrade before the deadline.
