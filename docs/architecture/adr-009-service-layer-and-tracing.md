# ADR-009: Service Layer Telemetry & LangSmith Tracing Integration

- **Status**: Accepted
- **Deciders**: Core Architecture Team & Agent Maintainers
- **Date**: 2026-07-28

---

## Context & Problem Statement

The clinic voice assistant requires high-throughput tool calling and fine-grained observability across conversational turns. Previously, voice tool dispatches were coupled to raw HTTP route wrappers. Furthermore, tracing LLM token generation, tool execution latency, and turn-taking behavior required a unified telemetry standard.

---

## Decision Drivers

1. **In-Process Performance**: Voice tool calls must complete in sub-50ms to keep total voice turn latency under 1.5 seconds.
2. **Observability & Tracing**: LLM inputs, tool calls, and completion telemetry must be exportable to tracing standard platforms like LangSmith without introducing latency overhead.
3. **Strict Decoupling**: Application business logic in `app.api.services` (booking, identity verification, working hours validation) must remain independent of transport frameworks (FastAPI routes, WebSockets, or test clients).

---

## Considered Options

1. **Option 1**: Keep HTTP mock dispatching and add ad-hoc logging.
2. **Option 2**: Expose direct service layer bindings in `app.api.services` and add standardized LangSmith tracing environment keys to `.env` and `Settings`.

---

## Decision Outcome

Chosen Option: **Option 2**.

### Key Architectural Requirements:

1. **Direct Service Abstraction**:
   - `app.api.services.identity`: Handles DOB normalization and identity verification.
   - `app.api.services.booking`: Handles appointment creation, exclusion constraint checks, cancellation, and rescheduling.
   - Tool calling in `app/voice/llm_client.py` delegates directly to service functions and in-memory ASGI router dispatch.

2. **LangSmith Telemetry Integration**:
   - Standardized `LANGCHAIN_TRACING_V2`, `LANGCHAIN_ENDPOINT`, `LANGCHAIN_API_KEY`, and `LANGCHAIN_PROJECT` variables added to `.env.example`, `.env`, and `app/api/config.py`.
   - Enables zero-code-change telemetry tracking when valid LangSmith API keys are provided.

---

## Positive Consequences

- **Tool Execution Speed**: Eliminates external network socket overhead for internal tool calls.
- **Enhanced Observability**: Provides production tracing for LLM prompts, reasoning steps, tool payloads, and latency breakdowns.
- **Maintainability**: Keeps database and scheduling rules centralized in `app.api.services`.

---

## Negative Consequences & Mitigation

- **Environment Configuration Overhead**: Additional environment keys must be maintained in `.env`.
  - *Mitigation*: Sensible defaults (`LANGCHAIN_TRACING_V2=false`) prevent unnecessary API calls when tracing is unconfigured.
