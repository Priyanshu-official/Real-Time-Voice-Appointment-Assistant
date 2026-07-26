# ADR-003: LLM reasoning model

## Status
Accepted

## Context
- **Problem**: Need an open-source, self-hostable, tool-calling-capable LLM that can (a) reason about booking/availability, (b) verify identity before disclosing data, and (c) resist prompt-injection/social-engineering attempts — inside a ~1-2s latency budget.
- **Constraints**: Privacy/adversarial resilience (20 pts) and architecture clarity (20 pts) are the two heaviest criteria; must run self-hosted.

## Options Considered

| Option | Pros | Cons | Complexity | When Valid |
|--------|------|------|------------|-----------|
| 1-3B instruct models | Fast, cheap to serve | Weak instruction-following under adversarial input; unreliable tool-call formatting | Low | Simple FAQ bots, not identity-gated PHI access |
| 7-8B instruct, 4-bit quantized (Qwen2.5-7B or Llama-3.1-8B) | Strong enough tool-calling and instruction-following to resist basic injection; fits latency budget quantized | Still weaker than frontier models on adversarial robustness | Medium | This case — real-time, identity-gated, tool-using agent |
| 30B+ open models | Much stronger reasoning/robustness | Breaks the 1-2s latency budget on available hardware without a serious GPU | High | Latency budget is relaxed or dedicated GPU inference available |

## Decision
**Chosen**: 7-8B instruct model (Qwen2.5-7B-Instruct or Llama-3.1-8B-Instruct), 4-bit quantized, served via Ollama or vLLM

## Rationale
1. Requirement #5 (adversarial resilience) needs a model that reliably follows a system-prompt policy under pressure — sub-3B models are too easily talked out of their instructions.
2. Requirement #1's latency budget rules out 30B+ models without dedicated GPU infrastructure we don't have time to provision and tune in 48 hours.
3. 7-8B quantized is the documented sweet spot for tool-calling reliability at real-time latency on commodity hardware.

## Trade-offs
- Not as robust against adversarial prompts as a frontier-scale model would be.
- We are accepting some residual injection risk rather than eliminating it.

## Consequences
- **Positive**: Meets latency budget; tool-calling is reliable enough for booking/availability logic.
- **Negative**: A sufficiently creative adversarial prompt may still partially succeed.
- **Mitigation**: Defense-in-depth — identity verification and data access are enforced in the FastAPI tool layer (code, not model judgment), so even if the LLM is manipulated, it cannot return another patient's data because the backend itself checks the verified patient_id on every call. The eval set explicitly includes adversarial transcripts to measure and document residual risk.

## Revisit Trigger
- If eval results show unacceptable injection success rate, add a lightweight input classifier/guardrail model in front of the LLM.

## Hardware Note (updated 26 Jul — superseded)
Initially resolved with the RTX 3050 Ti Laptop (4GB VRAM), which was too tight for the chosen 7-8B model plus concurrent STT/TTS, leading to a planned cloud GPU rental for deployment (see original note below, kept for history).

**Superseded by**: a Mac with 16GB unified memory (Apple Silicon) is available and is now the primary dev *and* deployment machine. 16GB comfortably fits the 7-8B 4-bit model (~4.5-5GB weights + KV cache) with headroom left for STT and TTS running concurrently — this was the actual blocker on the Windows/RTX box, not model choice. Served via Ollama (Metal-accelerated), which is the same self-hostable stack as it would be on any Linux box, so this is a hardware change, not an architecture change. This removes the need for a rented cloud GPU (see ADR-006) — deployment now runs on hardware already owned, self-hosted per the brief's own requirements.

Original note (RTX 3050 Ti, kept for history): Dev machine has an NVIDIA RTX 3050 Ti Laptop GPU with only 4GB VRAM — confirmed via Device Info, correcting an earlier reading that showed integrated graphics only. 4GB is enough to run a 3B-class model locally (Qwen2.5-3B-Instruct or Phi-3-mini, 4-bit, ~2-2.5GB) for local development, but not the chosen 7-8B model and not enough headroom to run STT+LLM+TTS concurrently for the 10-20 concurrent call load test. The RTX 3050 Ti machine remains available as a secondary/backup dev box only.

## Note on Groq API (26 Jul)
Considered using Groq's free-tier API for rapid prototyping of prompts/tool-calling logic. Rejected for the deployed system: Groq is a proprietary hosted API (their own LPU hardware), not self-hostable, so it would violate the brief's "100% open-source, self-hostable, no proprietary APIs" rule regardless of cost. Acceptable only as a throwaway prototyping aid before wiring up the real self-hosted model — must not appear in the submitted deployment.
