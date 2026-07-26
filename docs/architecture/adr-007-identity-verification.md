# ADR-007: Identity verification approach

## Status
Accepted

## Context
- **Problem**: Must verify caller identity before revealing any patient data, and never leak one patient's data to another. Graded at 20 points alongside adversarial resilience.
- **Constraints**: 48-hour build; no real telephony/carrier-level caller ID available since telephony is out of scope.

## Options Considered

| Option | Pros | Cons | Complexity | When Valid |
|--------|------|------|------------|-----------|
| Full auth system (OTP via SMS, session tokens, MFA) | Production-grade security | Massive scope increase; requires real telephony/SMS integration that's explicitly out of scope | High | Real production healthcare system |
| No verification, trust caller-provided name | Trivial to build | Directly fails requirement #4 (never leak data) | None | Never acceptable here |
| Deterministic challenge (patient ID/name + DOB, or phone last-4) checked server-side, enforced in the tool layer | Satisfies "verify before revealing," simple to build and test, easy to attack-test for the adversarial-resilience criterion | Not real-world production security | Low | This case — the requirement is "verify before revealing," not "build production auth" |

## Decision
**Chosen**: Deterministic challenge (two-factor: e.g. name + date of birth) verified in the FastAPI tool layer against the mock records DB, with every data-access call logged to an audit table with patient_id, timestamp, and action.

## Rationale
1. The graded requirement is specifically "verify caller identity before revealing anything" and "maintain an audit trail" — not "build hospital-grade authentication." Building OTP/SMS infrastructure would be scope creep the brief doesn't ask for and telephony is explicitly excluded.
2. Because it's enforced in the FastAPI tool layer (not just in the LLM's behavior), it holds even if the LLM is manipulated by a prompt injection — the backend simply won't return data for an unverified patient_id, regardless of what the model says.
3. This is easy to red-team within the 48-hour window, which matters directly for requirement #6 (adversarial eval set).

## Trade-offs
- Not resistant to someone who already knows a patient's name and DOB (a real system would need stronger factors).
- Accepting this because the brief scopes telephony/real caller verification out, and the grading criterion is about the presence of a verification gate and audit trail, not its cryptographic strength.

## Consequences
- **Positive**: Simple, testable, and structurally prevents cross-patient data leakage regardless of LLM behavior.
- **Negative**: Would not be sufficient for a real production deployment.
- **Mitigation**: State this trade-off explicitly in the README's "privacy and regulatory reasoning" section, framed as a scoped decision rather than an omission.

## Revisit Trigger
- If telephony/real caller ID becomes in-scope, revisit with actual carrier-level or OTP-based verification.
