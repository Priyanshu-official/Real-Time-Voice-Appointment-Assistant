# ADR-006: Deployment topology

## Status
Accepted

## Context
- **Problem**: Need a live, public, demonstrable deployment by 28 July that can handle a 10-20 concurrent call load test.
- **Constraints**: 48-hour build; "depth over breadth" and "do not overengineer" are explicit instructions in the brief.

## Options Considered

| Option | Pros | Cons | Complexity | When Valid |
|--------|------|------|------------|-----------|
| Microservices (separate STT/LLM/TTS/API services, message queue) | Independently scalable components | Massively more to build, deploy, and debug in the time available; no requirement demands this | High | Production system at real scale with a team to run it |
| Single service, single VM, docker-compose | Fast to build, fast to debug, fast to deploy, easy to reason about for the architecture-clarity criterion | Won't horizontally scale past what one machine can handle | Low | Time-boxed build, small demo-scale load (10-20 concurrent calls) — this case |
| Serverless functions per component | No infra to manage | Cold starts fight the 1-2s latency budget; awkward fit for stateful streaming audio pipelines | Medium | Bursty, stateless workloads — not a good fit for streaming voice |

## Decision
**Chosen**: Single service (FastAPI + Pipecat pipeline + Postgres), deployed as one docker-compose stack on one VM/GPU instance.

## Rationale
1. Nothing in the graded requirements needs independent scaling of components — the load test target (10-20 concurrent calls) is well within what a single well-sized instance handles.
2. Directly follows the brief's own instruction: "depth over breadth — a rock-solid narrow build beats a broad, flaky one."
3. A single, clearly-organized service is easier to explain clearly in the README/video, which is itself part of the architecture-clarity criterion.

## Trade-offs
- Doesn't horizontally scale past one machine's capacity without further work.
- A crash in one component (e.g. the LLM server) affects the whole stack rather than being isolated.

## Consequences
- **Positive**: Minimal infrastructure to build, debug, and explain within the timeline; lowest risk of not shipping.
- **Negative**: Not production-scale architecture.
- **Mitigation**: Explicitly document this as a deliberate, scoped trade-off in the README (this is exactly what an ADR is for) rather than presenting it as an oversight.

## Revisit Trigger
- If load testing reveals the single instance can't sustain 10-20 concurrent calls within the latency budget, split the LLM/TTS inference into a separate process before splitting further.

## Update (26 Jul): host machine and public exposure

### Context
Original plan assumed a rented cloud GPU (RunPod/Vast.ai) for deployment, driven by the RTX 3050 Ti's 4GB VRAM being too tight for the chosen 7-8B model. A Mac with 16GB unified memory is now confirmed available and comfortably fits the model (see ADR-003). This removes the reason for renting cloud infrastructure at all.

### Options Considered

| Option | Pros | Cons | Complexity | When Valid |
|--------|------|------|------------|-----------|
| Rented cloud GPU (original plan) | Dedicated, always-on, no reliance on local network/power | Costs money and setup time neither of which is needed now; extra infra to configure in a tight timeline | Medium | Local hardware genuinely insufficient — no longer the case |
| Mac, exposed via Cloudflare Tunnel / ngrok | Zero additional cost, zero new infra to provision, still satisfies "self-hosted, open-source" cleanly since it's actually self-hosted (not just self-hosted in name) | Public URL depends on the Mac staying on and connected for the demo window; not built for sustained production traffic | Low | Time-boxed demo/evaluation window — this case |
| Deploy to a free-tier PaaS (Render/Fly.io free tier) | Simple URL, no tunnel to manage | Free tiers don't provide GPU/enough RAM for a 7-8B model; would force a smaller model or CPU inference, undoing the point of using the Mac | Low-Medium | Stateless, lightweight services only |

### Decision
**Chosen**: Docker-compose stack runs on the Mac (16GB unified memory), LLM served via Ollama with Metal acceleration. Public access via Cloudflare Tunnel (or ngrok as fallback) pointed at the local FastAPI/Pipecat service.

### Rationale
1. Removes cost and setup risk of provisioning cloud GPU infrastructure with limited time left before the deadline.
2. The Mac's 16GB comfortably fits the 7-8B model plus concurrent STT/TTS (see ADR-003), so there's no longer a technical reason to move off local hardware.
3. A tunnel keeps the architecture genuinely self-hosted rather than "self-hosted" in name only on rented infrastructure — a cleaner, more honest match to the brief's open-source/self-hostable requirement.

### Trade-offs Accepted
- The public URL is only live while the Mac is on, awake, and connected — need to keep it running through the grading/demo window.
- No redundancy if the machine sleeps, restarts, or loses network mid-demo.

### Consequences
- **Positive**: No cloud cost, no cloud setup time, one less moving part to debug under deadline pressure.
- **Negative**: Availability is tied to one physical machine's uptime during the evaluation window.
- **Mitigation**: Disable sleep/screen-lock on the Mac for the demo window; test the tunnel URL well before the deadline, not at the last minute; keep the video walkthrough as a fallback proof of functionality if the live URL has any hiccup during grading.

### Revisit Trigger
- If the load test (10-20 concurrent calls) shows the Mac can't sustain the latency budget under that load, reconsider a rented GPU instance at that point — but only if evidence shows it's needed.
