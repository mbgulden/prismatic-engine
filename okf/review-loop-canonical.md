---
type: Standard
title: Self-Review + Peer Review Loop (Canonical Codification)
description: The canonical review loop enforced in agent_dispatcher.py. Worker → AGY peer review → Fred verification → agent:done.
resource: okf/review-loop-canonical.md
tags: [review-loop, agent:fred, agent:agy, peer-review, codification, prismatic-engine]
timestamp: 2026-06-19T10:30:00Z
linear_issue: GRO-2024
git_repo: mbgulden/prismatic-engine
git_path: okf/review-loop-canonical.md
last_verified: 2026-06-19
verified_by: fred
status: current
---

# Self-Review + Peer Review Loop (Canonical Codification)

**Canonical version:** [`growthwebdev-knowledge/okf/standards/review-loop-canonical.md`](https://github.com/mbgulden/growthwebdev-knowledge/blob/main/okf/standards/review-loop-canonical.md).
**This spoke version** mirrors the canonical standard with prismatic-engine-specific
context.

**Status:** ENFORCED in `agent_dispatcher.py` as of Jun 18 2026.
**Enforcement point:** `agent_dispatcher.py` lines around `next_label = config.get("next_label")`.

## The loop (canonical)

```text
Worker (Ned, Kai, Jules, Codex, Autobot, AGY sub-lanes)
   ↓
AGY peer review (different agent, read-only)
   ↓
Fred verification (orchestrator confirms review artifact + walkthrough)
   ↓
agent:done → Done state
```

Every agent lane routes through AGY for peer review, except:

- **AGY itself** (the peer-reviewer) routes directly to Fred for verification.
- **Fred** is the only lane that may move to `agent:done`.

## Label chain (single source of truth)

| Source lane | `next_label` | Why |
|---|---|---|
| `agent:kai`, `agent:kai-css`, `agent:kai-content`, `agent:kai-js` | `agent:agy` | Kai ships to AGY for peer review |
| `agent:codex` | `agent:agy` | Codex ships to AGY |
| `agent:autobot` | `agent:agy` | Autobot ships to AGY |
| `agent:ned` | `agent:agy` | Ned ships to AGY |
| `agent:ned-code`, `agent:ned-infra`, `agent:ned-audit`, `agent:ned-review` | `agent:ned` | Ned sub-agents report to Ned |
| `agent:jules` | `agent:fred` | Jules → AGY (via redirect) → Fred |
| `agent:agy`, `agent:agy-*` (8 lanes) | `agent:fred` | AGY peer review complete → Fred verify |
| `agent:antigravity-cli` | `agent:fred` | Antigravity CLI is AGY-equivalent |
| `agent:fred` | `agent:done` | **Only Fred may move to Done.** |
| `agent:done` | terminal | No further routing. |

## Why the loop exists

In June 2026, 5 Darius Star issues went `agent:ned → Done` with no AGY peer
review. The old label chain allowed it because `agent:fred.next_label` was
`agent:done` with no verification step between AGY review and Done. See
[`pipeline-bypass-detection-case-study.md`](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
in the orchestrator-delegation-discipline skill.

## Where this is enforced

- `agent_dispatcher.py` (orchestrator profile) — `AGENT_CONFIG` table +
  bypass-detection block right before each `transition_label()` call.
- `agent_output_validator.py` — validates AGY transcripts before allowing
  the next transition.

## Where the bypass check lives

```python
# Pipeline bypass detection (GRO-2024 review loop enforcement):
# Only Fred (next_label="agent:done") may move an issue to Done.
if next_label == "agent:done" and label_name != "agent:fred":
    bypass_comment = "..."
    transition_label_with_comment(
        issue["id"], label_name, "agent:fred", bypass_comment
    )
    continue
```

## What Fred actually verifies

When `agent:fred` picks up an issue:

1. The validator transcript is clean (no `agent_output_validator` escalation).
2. A Linear comment from the worker exists with file paths or artifact references.
3. The issue has been through `agent:agy` (peer review) at some point — checked
   via label history or `agent:fred` cron pickup.

If any of these checks fail, Fred re-routes to the appropriate agent rather
than transitioning to `agent:done`.

## Failure modes this prevents

- Worker self-review-only (no AGY peer review).
- AGY review without Fred verification (no human-in-the-loop stop).
- Done state with empty transcript (silent validator crash).
- Done state with `requires:human-approval` (send outreach, publish profile).

## Failure mode this does NOT prevent

The engine dispatcher (`prismatic/dispatcher.py`) does NOT have bypass detection.
Until that ships, the engine dispatcher should not run unattended on the same
Linear queue as the orchestrator dispatcher.

## Related docs

- [`linear-rate-limit.md`](./linear-rate-limit.md) — companion standard
- [`architecture.md`](./architecture.md) — Tier 4 architecture
- Canonical version: [`growthwebdev-knowledge/okf/standards/review-loop-canonical.md`](https://github.com/mbgulden/growthwebdev-knowledge/blob/main/okf/standards/review-loop-canonical.md)
