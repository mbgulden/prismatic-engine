---
type: Standard
title: LinearBudget Lint + CI Integration
description: Regression-prevention layer for Linear API codification. Catches ungated Linear-calling files at PR time via scripts/check_linear_cron_rate.sh and .github/workflows/prismatic-lint.yml.
resource: okf/linear-budget-lint-ci.md
tags: [linear, ci, lint, linearbudget, regression-prevention, agent:fred, agent:ned, prismatic-engine]
timestamp: 2026-06-19T16:30:00Z
linear_issue: GRO-2037,GRO-2053,GRO-2054,GRO-2055,GRO-2056,GRO-2057,GRO-2062
git_repo: mbgulden/prismatic-engine
git_path: okf/linear-budget-lint-ci.md
last_verified: 2026-06-19
verified_by: ned
status: current
---

# LinearBudget Lint + CI Integration

**Status:** ENFORCED in CI as of Jun 19 2026.
**Related standard:** [`linear-rate-limit.md`](./linear-rate-limit.md) (the LinearBudget codification itself).

## What this standard guarantees

Every Linear API call in the prismatic-engine codebase is gated through `LinearBudget.check_and_consume()`. New code that adds an ungated Linear call is **rejected at PR time** by the `prismatic-lint.yml` workflow.

This is the **regression-prevention layer** on top of the codification itself. Codification tells you how to gate; this standard tells you the gate is enforced.

## Where enforcement lives

| Path | Role | GRO |
|------|------|-----|
| `scripts/check_linear_cron_rate.sh` | The lint script. Scans `prismatic/` for files with HTTP imports + Linear references that don't import `LinearBudget`. Fails on any ungated file. Estimates hourly usage; fails above 2000/hr safety threshold (80% of 2500 budget). | GRO-2037 |
| `.github/workflows/prismatic-lint.yml` | GitHub Actions workflow. Runs the lint on every push and PR to `main` / `deploy-fresh`. Required check. | GRO-2062 |
| `prismatic/linear/budget.py` | The `LinearBudget` class itself (referenced by the lint's gate detection) | GRO-2008/2020 |
| `~/.hermes/profiles/orchestrator/scripts/agent_dispatcher.py::_linear_gql()` | Wraps direct GraphQL with `LinearBudget.check_and_consume()` | GRO-2034 |

## Files gated as of Jun 19 2026

8/8 files with Linear API usage are gated:

| File | Call mechanism | Gated via | GRO |
|------|----------------|-----------|-----|
| `prismatic/dispatcher.py` | urllib.request | `_linear_gql()` wrapper | GRO-2034 |
| `prismatic/linear/__init__.py` | imports `LinearBudget` | direct | GRO-2008/2020 |
| `prismatic/linear/budget.py` | defines `LinearBudget` | n/a (provider) | GRO-2008/2020 |
| `prismatic/credit_tracker.py` | subprocess curl | `post_linear_comment()` guard | GRO-2053 |
| `prismatic/journal.py` | urllib.request | `gql()` chokepoint guard | GRO-2054 |
| `prismatic/providers/tasks/linear.py` | urllib.request | `execute_query()` guard | GRO-2055 |
| `prismatic/security/credential_rotator.py` | urllib.request | `rotate_linear()` guard | GRO-2056 |
| `prismatic/telemetry.py` | subprocess curl | `_post_alert_comments()` guard | GRO-2057 |

## The canonical gate pattern

```python
try:
    from prismatic.linear.budget import linear_budget
    if not linear_budget.check_and_consume("prismatic.<module>"):
        return None  # or raise — depends on caller semantics
except ImportError:
    # LinearBudget not importable (e.g., partial install) — log warning, proceed
    print(
        "[<module>] WARNING: LinearBudget not importable — proceeding without gate",
        file=__import__("sys").stderr,
    )
# ... existing Linear API call ...
```

Use a unique `agent_name` per file (e.g. `"prismatic.credit_tracker"`, `"prismatic.telemetry"`) so the budget tracks per-file consumption in its logs.

## What the lint detects

The lint script's detection is intentionally **inclusive** — it catches:

- Direct HTTP imports: `requests`, `httpx`, `urllib.request`, `http.client`, `aiohttp`
- Subprocess-based: `curl` or `wget` calls
- Linear references: `api.linear.app`, `LinearBudget`, `linear_call`, `_linear_gql`, `post_linear_comment`

It does **not** consider a file "gated" by:
- A comment saying "this should be gated" (lint doesn't read English)
- A function name like `gated_call` (lint doesn't infer intent)
- A test fixture or mock (these are excluded from the scan but production code is not)

The only thing that counts as gated is: **actual `LinearBudget` import + `check_and_consume()` call**.

## CI integration

`.github/workflows/prismatic-lint.yml` runs on:

| Event | Branches |
|-------|----------|
| `push` | `main`, `deploy-fresh` |
| `pull_request` | `main`, `deploy-fresh` |

Jobs:

1. Checkout repo (`actions/checkout@v4`)
2. Set up Python 3.10 (`actions/setup-python@v5`)
3. **Run `scripts/check_linear_cron_rate.sh`** (REQUIRED — fails on any ungated file)
4. Run `scripts/pre-commit-hook.sh` (best-effort — warnings only)

If step 3 fails, the PR cannot merge.

## Running the lint locally

```bash
cd /home/ubuntu/work/prismatic-engine
bash scripts/check_linear_cron_rate.sh
echo "Exit: $?"
# 0 = clean (8/8 gated)
# 1 = lint failure (X/8 gated; ungated files listed)
```

## Adding the gate to a new file

1. Identify the call site: `grep -n "api.linear.app" path/to/file.py`
2. Add the canonical gate pattern (see above) before the call
3. Use a unique `agent_name` (the existing names are listed in the table above)
4. Run the lint locally to verify
5. Commit — CI will verify again on PR

## What "ungated" looks like in the lint output

```
❌ LinearBudget coverage lint failed:
  • prismatic/credit_tracker.py: makes Linear API calls without LinearBudget gate
  • prismatic/journal.py: makes Linear API calls without LinearBudget gate
  ...

Fix: import LinearBudget from prismatic.linear.budget and wrap your
Linear API calls with budget.check_and_consume('<script_name>').
See GRO-2034 for the canonical pattern.
```

Exit code: 1.

## Failure modes

| Failure | Behavior | Resolution |
|---------|----------|------------|
| New ungated file added | Lint fails CI on PR | Add the gate per the canonical pattern |
| `api.linear.app` URL found but not actually called | False positive — lint sees URL but not the gate | Add the gate anyway (defensive) |
| `LinearBudget` not importable in the env | `ImportError` caught, warning printed, gate skipped | Investigate env setup; production should never hit this |
| Lint script itself fails to run | CI job errors, not lint-specific | Check bash version, grep, python3 availability |

## Hand-off

This standard is **maintained by `agent:ned`**. If you add a new Linear-calling file, the lint will catch it. If you need to exempt a file from the lint (rare, e.g., a test fixture), modify the `CANDIDATE_FILES` glob in `scripts/check_linear_cron_rate.sh` and document why in the commit message.

## Related

- [`linear-rate-limit.md`](./linear-rate-limit.md) — the LinearBudget codification itself
- [`architecture.md`](./architecture.md) — engine module layout
- [`webhook-handler-test-pattern.md`](./webhook-handler-test-pattern.md) — for testing gated webhook paths
- `docs/linear-rate-limit-audit.md` (engine repo, full audit history)
- GRO-2037, GRO-2053..GRO-2057, GRO-2062 (Linear issues)

---

*Verified by `agent:ned` on Jun 19 2026 after the 5-file gating sweep (GRO-2053..GRO-2057) shipped and CI went green for the first time.*
