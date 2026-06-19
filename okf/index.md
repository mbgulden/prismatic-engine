---
type: Index
title: Prismatic Engine — OKF Spoke Index
description: Hub-and-spoke OKF index for the prismatic-engine repo. All docs in this directory mirror canonical versions in the hub (mbgulden/growthwebdev-knowledge). The hub is the source of truth; spokes are convenience mirrors.
resource: okf/index.md
tags: [index, project, prismatic-engine]
timestamp: 2026-06-19T20:00:00Z
linear_issue: GRO-2039
git_repo: mbgulden/prismatic-engine
git_path: okf/index.md
hub_repo: mbgulden/growthwebdev-knowledge
last_verified: 2026-06-19
verified_by: fred
status: current
---

# Prismatic Engine — OKF Spoke Index

This index points to OKF docs for the `prismatic-engine` repo. **The hub (`mbgulden/growthwebdev-knowledge/okf/`) is the source of truth.** Docs here are mirrors — if they diverge, the hub wins.

## Standards (mirrored from hub)

| Standard | Linear |
|---|---|
| [Review-loop codification](./review-loop-canonical.md) | GRO-2024 |
| [Linear rate-limit codification](./linear-rate-limit.md) | GRO-2008/2010/2020/2034 |
| [Event-driven dispatch architecture](./dispatch-architecture.md) | GRO-2047/2048/2050 |
| [Webhook security model](./webhook-security.md) | GRO-2057..2062 |
| [AGY peer-review standard](./agy-peer-review.md) | GRO-2024 |
| [Production-grade dispatch](./dispatch-production-grade.md) | GRO-2057 |

## Decisions (mirrored from hub)

| Decision | Linear |
|---|---|
| [Event-driven dispatch ADR](./event-driven-dispatch.md) | GRO-2042 |

## Integrations (mirrored from hub)

| Integration | Linear |
|---|---|
| [Webhook handler test pattern](./webhook-handler-test-pattern.md) | GRO-2047 |

## Concepts (engine-specific, no hub mirror)

- [architecture.md](./architecture.md) — Module map, public API surface, two-dispatcher model

## Tier 7 Journey (mirrored from hub)

| Doc | Description |
|---|---|
| [tier-7-journey.md](./tier-7-journey.md) | Chronological narrative of Tier 7 production-grade work |
| [tier-7-architecture.md](./tier-7-architecture.md) | Architecture diagram + data flow |

## Tier status

| Tier | Title | Status |
|---|---|---|
| Tier 1 | Unblock dispatcher | ✅ Done |
| Tier 2 | Modularize dispatcher | ✅ Done |
| Tier 3 | Update inventory | ✅ Done |
| Tier 4 | Architecture doc | ✅ Done |
| Tier 5a | OKF pilot | ✅ Done |
| Tier 6 | Standalone + event-driven dispatch | 🚧 Partially done |
| Tier 7 | Production-grade hardening | ✅ Done |

## Related Linear issues

GRO-2008, GRO-2010, GRO-2020, GRO-2024, GRO-2030, GRO-2031, GRO-2032, GRO-2034, GRO-2037, GRO-2039, GRO-2042, GRO-2047, GRO-2048, GRO-2050, GRO-2057..2062, GRO-2077, GRO-2078, GRO-2082

See [tier-7-journey.md](./tier-7-journey.md) for the chronological narrative.

## How to update these docs

1. Edit the canonical version in the **hub** (`mbgulden/growthwebdev-knowledge/okf/`).
2. Run `cp` from hub to this spoke's `okf/` directory.
3. Commit and push both repos.

Spoke edits are forbidden — they get overwritten on next sync.
