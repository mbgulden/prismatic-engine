---
type: Standard
title: AGY Peer-Review Standard
description: Canonical standard for AGY peer-review across the Prismatic Engine. Every agent's work passes through AGY → Fred → Done review loop. The webhook handler, dispatcher fix, and OKF docs all went through this loop.
resource: okf/standards/agy-peer-review.md
tags: [standard, agy, peer-review, review-loop, quality]
timestamp: 2026-06-19T12:30:00Z
linear_issue: GRO-2024
git_repo: mbgulden/prismatic-engine
git_path: prismatic/dispatcher.py
last_verified: 2026-06-19
verified_by: fred
status: current
---

# AGY Peer-Review Standard

**Status:** ENFORCED since Jun 18 2026.
**Codified by:** GRO-2024 + canonical codification doc.
**Refs:** GRO-2024 (review-loop enforcement), GRO-2034 (AGY caught dispatcher bugs), GRO-2040 (AGY caught OKF doc drift).

## The loop

```text
Worker (Kai/Ned/Codex/Jules/Fred/AGY)
  ↓ ships work via agent:* label
AGY (peer review)
  ├─ Verdict: APPROVE → next_label="agent:fred"
  └─ Verdict: NEEDS_CHANGES → next_label back to origin agent (auto-cycle)
Fred (verification)
  └─ Verdict: PASS → next_label="agent:done"
```

Only `agent:fred` may transition to `agent:done`. Bypass detection in `prismatic/dispatcher.py` re-routes any non-Fred attempt back to Fred. Code location: `dispatcher.py` line ~2134-2151 (next_label logic) and ~2206 (bypass comment).

## AGY review artifacts

| Artifact | Path | Format |
|---|---|---|
| AGY result file | `/tmp/agy-dispatch-GRO-{NUMBER}-result.md` | Markdown verdict |
| AGY chat session | tmux session `agy-GRO-{NUMBER}` | Live workspace |
| Linear comment | comment on `GRO-{NUMBER}` | Summary + verdict |

## AGY verdict structure

Every AGY result file follows this structure (codified in `launch_agy_with_artifact.py`):

```markdown
# Review Verdict: {APPROVE | NEEDS_CHANGES}

## Review Target
- **Repo:** {repo}
- **Branch / PR:** {branch or PR#}

## Verdict
**{APPROVE | NEEDS_CHANGES}**

## Evidence Reviewed
- ...

## Findings
### High Severity
1. ...
### Medium Severity
1. ...

## Required Fixes
1. ...

## Re-Review Checklist
- [ ] ...
```

## Real AGY findings (this session)

These are the actual findings AGY produced. Codified here so future work doesn't repeat the same mistakes.

### GRO-2034 dispatcher (Code review)

| # | Severity | Finding | Fix |
|---|---|---|---|
| 1 | High | `_linear_gql` was double-consuming budget tokens (1 + 1 = 2 per query) when called from legacy `_linear_call`. | Added `_skip_budget_check` flag; `_linear_call` consumes once, `_linear_gql` skips inner check. |
| 2 | Medium | Two consecutive `except Exception:` blocks in launcher error handling — first re-raised, second was dead code. | Restructured into single reachable except that logs + rolls back dedup. |

### GRO-2040 OKF docs (Documentation review)

| # | Severity | Finding | Fix |
|---|---|---|---|
| 1 | High | Broken link in hub `review-loop-canonical.md` — `agent_dispatcher.py` path resolved to `/home/ubuntu/orchestrator/` instead of `/home/ubuntu/.hermes/...`. | Fixed relative path. |
| 2 | High | `status: accepted` in `okf-adoption.md` frontmatter — outside OKF spec values (`current \| draft \| deprecated`). | Changed to `status: current`. |
| 3 | High | `prismatic-engine/okf/review-loop-canonical.md` linked to wrong spec URL (Google OKF SPEC instead of bypass-detection case study). | (Pending) |
| 4 | Medium | Documentation drift: `linear-rate-limit.md` claimed `prismatic/linear_budget.py` was canonical; reality is `prismatic/linear/budget.py` until GRO-2020 lands on `main`. | Updated both hub + spoke docs. |
| 5 | Medium | Code comment drift: `agent_dispatcher.py:2206` said `GRO-2014 review loop enforcement` — should be `GRO-2024`. | Fixed. |
| 6 | Medium | Module map in `architecture.md` incomplete: missing `agents/`, `api/`, `billing/`, `interface/`, `network/`, `plugins/`, `skills/`. | (Pending) |
| 7 | Advisory | Index files have frontmatter; AGY claimed spec forbids this. **Spec is permissive** — only `type` is required. Decision: keep frontmatter for now. | Not applied. |

### GRO-2051 GRO-2034 fix (Re-review)

| # | Severity | Finding | Fix |
|---|---|---|---|
| 1 | High | After the GRO-2034 fix was applied, AGY re-reviewed and found the **double budget consumption** was still present under specific conditions. | Required the `_skip_budget_check` flag pattern. |

## Why AGY catches real bugs

Two patterns emerge from the findings above:

1. **AGY is rigorous about source-level details.** Broken links, wrong references, code-comment drift — these are easy to miss when you're the author. AGY reads cold.
2. **AGY catches double-consumption / unreachable code.** Static analysis style bugs that escape human review because the *symptom* (e.g. budget running out faster than expected) shows up far from the *cause* (the duplicate `check_and_consume()`).

## Adoption

Every agent's work follows this loop. No exceptions. Bypass detection in dispatcher enforces it automatically — you cannot transition to `agent:done` without going through `agent:fred`.

## Related docs

- `okf/standards/review-loop-canonical.md` — the underlying review loop standard
- `okf/standards/linear-rate-limit.md` — what AGY found in GRO-2034
- `okf/decisions/agy-peer-review-decision.md` — why AGY is the peer reviewer (pending)