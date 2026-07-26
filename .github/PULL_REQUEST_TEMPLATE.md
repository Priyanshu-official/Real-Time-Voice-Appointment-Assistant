## Linked issue
Closes #

## What changed

## Tests run
<!-- Which suite(s), and confirm they passed -->
- [ ] tests/unit/
- [ ] tests/eval/ (if this touches LLM prompt/tool-calling)
- [ ] Concurrency test specifically (if this touches booking logic)

## Golden rules checklist (AGENTS.md)
- [ ] Only touched files listed in the issue's Scope
- [ ] No secrets committed
- [ ] Audit log write included, if this touches data access
- [ ] Identity check enforced in code (not just prompt), if this touches patient data
