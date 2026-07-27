# Task: set up GitHub labels and issue/PR templates

Use the GitHub MCP server to perform the following on this repository. Do not touch any other files or settings. If a label or file already exists, update it to match this spec rather than duplicating it.

## 1. Create these labels

| Name | Color (hex) | Description |
|---|---|---|
| `status:ready` | `0E8A16` | Issue is scoped and ready for an agent to pick up |
| `status:in-progress` | `FBCA04` | An agent is actively working this issue |
| `status:review` | `1D76DB` | PR opened, waiting on Judge review |
| `status:blocked` | `D93F0B` | Blocked on a dependency or a human decision |
| `status:done` | `5319E7` | Merged and closed |
| `agent:assigned` | `C5DEF5` | An agent has claimed this issue |
| `size:atomic` | `BFD4F2` | Scoped to a single 15-30 min task |
| `type:concurrency` | `E4E669` | Touches booking/availability/concurrency logic — extra care per ADR-005 |
| `type:privacy` | `F9D0C4` | Touches identity verification or data access — extra care per ADR-007 |

## 2. Create `.github/ISSUE_TEMPLATE/atomic-task.md`

```markdown
---
name: Atomic task
about: A single 15-30 minute unit of work for an agent
title: "[TASK] "
labels: status:ready, size:atomic
---

## Scope
<!-- Exact files/directories this task may touch. Nothing outside this list. -->
-

## Out of scope
<!-- Explicitly list anything adjacent that must NOT be touched -->
-

## Acceptance criteria
<!-- Testable bullet points. The Judge checks against these directly. -->
- [ ]
- [ ]

## Relevant ADR / skill
<!-- e.g. ADR-005 (concurrency), ADR-007 (identity), booking-domain skill -->

## Notes
<!-- Anything else an agent needs to know before starting -->
```

## 3. Create `.github/ISSUE_TEMPLATE/config.yml`

```yaml
blank_issues_enabled: false
contact_links: []
```

## 4. Create `.github/PULL_REQUEST_TEMPLATE.md`

```markdown
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
```

## 5. Branch protection on `main`

- Require the CI status check to pass before merge (CI workflow doesn't exist yet — skip this step for now if the check doesn't appear as an option, and leave a comment on this issue saying so).
- Disallow direct pushes to `main`.

## 6. Confirm

After creating everything, comment on this issue summarizing what was created (labels, files, branch protection status) so a human can verify before the first real atomic-task issue is opened.
