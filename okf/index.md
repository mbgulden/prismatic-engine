---
type: Index
title: prismatic-engine — OKF Bundle
description: Master index of OKF concepts in the prismatic-engine repository.
resource: okf/index.md
tags: [index, prismatic-engine, okf]
timestamp: 2026-06-19T17:00:00Z
linear_issue: GRO-2039
git_repo: mbgulden/prismatic-engine
git_path: okf/index.md
last_verified: 2026-06-19
verified_by: ned
status: current
---

# prismatic-engine — OKF Bundle

This bundle is the spoke for the prismatic-engine. The hub lives at
[`mbgulden/growthwebdev-knowledge/okf/projects/prismatic-engine.md`](https://github.com/mbgulden/growthwebdev-knowledge/blob/main/okf/projects/prismatic-engine.md).

## Concepts

- [`architecture.md`](./architecture.md) — Module map, public API surface, two-dispatcher model
- [`review-loop-canonical.md`](./review-loop-canonical.md) — Self-review + peer review loop codification (GRO-2024)
- [`linear-rate-limit.md`](./linear-rate-limit.md) — Linear API rate-limit codification (GRO-2008/2010/2020/2034)
- [`linear-budget-lint-ci.md`](./linear-budget-lint-ci.md) — Lint script + CI workflow that enforces LinearBudget gating at PR time (GRO-2037/2053..2057/2062)
- [`production-readiness-infrastructure.md`](./production-readiness-infrastructure.md) — 4-cron sweep: log rotation, SQLite VACUUM, retention, state-DB health alerts (GRO-2058/2059/2060/2061/2063)
- [`dispatch-architecture.md`](./dispatch-architecture.md) — Event-driven dispatch architecture (GRO-2047/2048/2050)
- [`webhook-handler-test-pattern.md`](./webhook-handler-test-pattern.md) — Webhook test pattern (9 tests, all pass)

## Related (canonical standards at the hub)

- [`growthwebdev-knowledge/okf/standards/review-loop-canonical.md`](https://github.com/mbgulden/growthwebdev-knowledge/blob/main/okf/standards/review-loop-canonical.md) — canonical version of the review loop standard
- [`growthwebdev-knowledge/okf/standards/linear-rate-limit.md`](https://github.com/mbgulden/growthwebdev-knowledge/blob/main/okf/standards/linear-rate-limit.md) — canonical version of the Linear rate-limit standard
- [`growthwebdev-knowledge/okf/standards/dispatch-architecture.md`](https://github.com/mbgulden/growthwebdev-knowledge/blob/main/okf/standards/dispatch-architecture.md) — canonical event-driven dispatch architecture
- [`growthwebdev-knowledge/okf/standards/agy-peer-review.md`](https://github.com/mbgulden/growthwebdev-knowledge/blob/main/okf/standards/agy-peer-review.md) — AGY peer-review standard

When the hub and spoke diverge, the hub version is canonical. Spoke versions
are convenience copies with project-specific context.

## Tier status

| Tier | Title | Status |
|---|---|---|
| Tier 1 | Unblock dispatcher | ✅ Done |
| Tier 2 | Modularize dispatcher | ✅ Done |
| Tier 3 | Update inventory | ✅ Done |
| Tier 4 | Architecture doc | ✅ Done |
| Tier 5a | OKF pilot | 🚧 In Progress |
| Tier 6 | Standalone + event-driven dispatch | 🚧 In Progress (webhook handler shipped) |

## Related Linear issues

GRO-2008, GRO-2010, GRO-2020, GRO-2024, GRO-2030, GRO-2031, GRO-2032, GRO-2034, GRO-2037, GRO-2039, GRO-2042..2050, GRO-2053..2063, GRO-2069..2076 (BHAG filed, waiting on GRO-2042)
