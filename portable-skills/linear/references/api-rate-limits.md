# Linear API Rate Limits & LinearBudget

> **Source.** Codified in `prismatic/linear/budget.py` after the GRO-2034 audit. Every script that calls the Linear API must go through `LinearBudget.check_and_consume()` or risk burning the 2,500 req/hour budget silently.

## The budget

- **Total**: 2,500 requests per hour per Linear workspace
- **Enforcement**: `LinearBudget` in `prismatic/linear/budget.py`
- **Safety threshold**: 80% = 2,000 req/hr (the lint fails CI above this)
- **Token bucket**: refills at `tokens_per_hour / 3600` per second

## What happens when budget is exhausted

`check_and_consume(agent_name)` returns `False`. The caller is expected to **raise an exception** or **wait + retry**. The dispatcher does the former:

```python
def _linear_gql(query, variables=None):
    budget = _linear_budget()
    if budget is not None:
        if not budget.check_and_consume("cron.agent_dispatcher"):
            raise Exception("Linear API rate limit exceeded ...")
    # ... actual request ...
```

This is the canonical pattern from GRO-2034. Use it everywhere.

## Detection: the lint script

`scripts/check_linear_cron_rate.sh` (shipped GRO-2037) scans `prismatic/` for files that make Linear API calls without importing `LinearBudget`. **Fails CI on any ungated file.**

Run locally:
```bash
bash /home/ubuntu/work/prismatic-engine/scripts/check_linear_cron_rate.sh
```

### What it detects

- Direct HTTP imports: `requests`, `httpx`, `urllib.request`, `http.client`, `aiohttp`
- Subprocess-based: `curl` or `wget` calls
- Linear references: `api.linear.app`, `LinearBudget`, `linear_call`, `_linear_gql`, `post_linear_comment`

### What it flags

```
❌ LinearBudget coverage lint failed:
  • prismatic/credit_tracker.py: makes Linear API calls without LinearBudget gate
  • prismatic/journal.py: makes Linear API calls without LinearBudget gate
  ...
```

Each flagged file = a separate Linear issue to fix.

## Known heavy consumers (from `docs/linear-rate-limit-audit.md`)

| File | Approx req/hr (pre-fix) |
|------|-------------------------|
| `agent_dispatcher.py` (orchestrator profile) | 900-1000 |
| `kai_callback_monitor.py` | 90 |
| `comment_trigger_monitor.py` | 60-120 |
| Other orchestrator scripts | <100 total |

Post-GRO-2034: all gated. The lint catches new bypass regressions.

## Adding the gate to a new file

```python
# At top of file
from prismatic.linear.budget import linear_budget

# At the Linear call site
def post_comment(issue_id: str, body: str) -> bool:
    if not linear_budget.check_and_consume("my_module"):
        raise Exception("Linear API rate limit exceeded")
    # ... actual Linear API call ...
```

The `agent_name` string should be unique per file (e.g. `"credit_tracker"`, `"agent_dispatcher"`, `"telemetry"`). This lets the budget track per-file consumption in its logs.

## Reference

- `prismatic-engine/docs/linear-rate-limit-audit.md` — full audit history
- `prismatic-engine/scripts/check_linear_cron_rate.sh` — lint source
- GRO-2034 — original codification fix
- GRO-2037 — lint script
- GRO-2053..GRO-2057 — follow-ups for ungated engine files

---

*Author: Ned. Update this doc when the budget semantics change.*