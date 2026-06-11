# High-Volume PR Review, Analysis & Merge Workflow

> Delivered via GRO-29 nudge execution (Jun 2026). See the canonical source at:
> `agentic-swarm-ops/docs/pr-review-workflow-high-volume.md`

This reference covers the full PR lifecycle for agent-generated branches:

- **Risk classification** (🟢 Safe / 🟡 Low / 🟠 Medium / 🔴 High) based on changed paths
- **PR creation standards** — Jules PR body template with required sections
- **Review pipeline** — Codex auto-review for 🟠/🔴 PRs, Michael gate for 🔴
- **Merge decision matrix** — CI + tests + review + risk class = deterministic merge rule
- **Conflict resolution flow** — route back to agent, 24h deadline, escalate
- **Failed CI handling** — 3 retries, then Michael escalation
- **PR queue prioritization** — FIFO within risk class
- **Safety gates checklist** — 10-pass checklist before any merge

See `agentic-swarm-ops/docs/pr-review-workflow-high-volume.md` for the full doc.
