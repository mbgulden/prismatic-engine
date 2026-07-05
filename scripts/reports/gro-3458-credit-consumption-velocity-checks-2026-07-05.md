# GRO-3458 — Credit Consumption & Velocity Checks Audit

**Auditor:** Ned  
**Timestamp (UTC):** 2026-07-05T20:48:20Z  
**Repository/branch:** `prismatic-engine` / `ned/GRO-3458`  
**Scope:** token/credit spending telemetry, daily credit headroom evaluation, and automated throttle/stop behavior.

## Executive summary

🔴 **Credit headroom is already exhausted for the Google AI Ultra ledger.** The live `telemetry_credit_ledger` shows **237,930 google-antigravity credits** recorded since 2026-07-01 against the hard-coded **25,000/month** allocation in `prismatic/credit_tracker.py`. That is **951.7% of the configured monthly allowance**.

🟡 **Velocity protections exist but are partial.** The dispatcher evaluates `AIUltraCreditTracker.evaluate_exhaustion_warning()` and sets a `throttle_dispatch` flag, but the observed enforcement is a **5-second delay** before dispatch, not a hard stop. Separate quota-state tracking does emit `critical` / `exhausted` events and has a lane stop record, but the most recent `agy` lane stop was already expired at audit time.

🟡 **Linear API rate limiting is active and noisy.** `linear_budget.db` recorded **10,041 `prismatic.dispatcher` rejects** in the last 7 days, last seen 2026-07-04T10:11:29Z. Current bucket state had recovered to 2,386.83 / 2,500 tokens by 2026-07-04T14:10:34Z, but the rejection volume proves the dispatcher can still hit the Linear budget gate.

## Evidence sources inspected

| Surface | Path / command | Notes |
|---|---|---|
| Credit ledger | `prismatic_state/event_router.db:telemetry_credit_ledger` | 22,018 rows; 15,862 google-antigravity rows this month. |
| Media artifact ledger | `prismatic_state/event_router.db:telemetry_media_artifacts` | 21,674 rows; path-based media deduplication. |
| Dispatcher enforcement | `prismatic/dispatcher.py:1580-1707` | Calls tracker, then delays dispatch by 5s if warning fires. |
| Credit tracker | `prismatic/credit_tracker.py` | 25,000/month allocation, 1h burn velocity, exhaustion warning. |
| Policy engine | `prismatic/credit_policy_engine.py` | Per-session/per-month in-memory policy decisions plus JSON persistence. |
| Linear budget | `prismatic_state/linear_budget.db` | Token bucket tables `budget_state`, `budget_logs`. |
| AGY quota state | `~/.prismatic/quota_state.db` via `prismatic/agy_quota_state.py --json` | Current model quota snapshots and quota events. |
| Lane stops | `prismatic_state/event_router.db:lane_stops` | `agy` stop existed but expired before audit time. |

## Live ledger findings

### Monthly and recent burn

| Window | Credits | Rows | Dominant provider/agent |
|---:|---:|---:|---|
| Last 1h | 0 | 0 | none |
| Last 6h | 0 | 3 | local/no-cost rows only |
| Last 24h | 58,275 | 3,896 | `google-antigravity` / `agent:agy` |
| Last 7d | 237,935 | 16,108 | `google-antigravity` / `agent:agy` |
| Current month (`google-antigravity`) | 237,930 | 15,862 | `agent:agy` |

Configured monthly limit from `AIUltraCreditTracker.MONTHLY_ALLOCATION_LIMIT`: **25,000**.  
Calculated monthly remaining from the current ledger: **0**.  
Overage vs configured limit: **212,930 credits**.

### Daily spend, last 7 days

| Day (UTC) | Provider | Credits | Rows |
|---|---|---:|---:|
| 2026-07-05 | google-antigravity | 58,275 | 3,885 |
| 2026-07-05 | local-llm | 0 | 11 |
| 2026-07-04 | google-antigravity | 58,530 | 3,902 |
| 2026-07-04 | local-llm | 0 | 14 |
| 2026-07-03 | google-antigravity | 121,065 | 8,071 |
| 2026-07-03 | local-llm | 0 | 38 |
| 2026-07-02 | local-llm | 0 | 43 |
| 2026-07-01 | google-antigravity | 60 | 4 |
| 2026-07-01 | local-llm | 0 | 29 |
| 2026-06-30 | google-antigravity | 5 | 1 |

This is not a slow leak. It is a burst pattern: most recorded Google burn occurred on July 3–5.

## Headroom / quota evaluation

### AI Ultra credit tracker

`AIUltraCreditTracker` evaluates:

- monthly spend from `telemetry_credit_ledger` where `provider = 'google-antigravity'` and `recorded_at >= month start`;
- remaining credits as `max(0, 25000 - monthly_spent)`;
- 1h velocity via `calculate_burn_velocity()`;
- exhaustion warning only when `velocity > 0` and `remaining / velocity < 24h`.

**Current edge case:** monthly remaining is already 0, but last-hour velocity is 0. That means `evaluate_exhaustion_warning()` returns no warning right now even though monthly headroom is exhausted. It only warns on active burn velocity, not on depleted monthly balance at idle.

### AGY quota state

Latest quota snapshot contained:

| Model | Remaining | Reset |
|---|---:|---|
| `claude-opus-4.6-thinking` | 0.2% | 2026-07-09T15:40:21Z |
| `claude-sonnet-4.6-thinking` | 0.2% | 2026-07-09T15:40:21Z |
| `gpt-oss-120b-medium` | 0.2% | 2026-07-09T15:40:21Z |
| Gemini flash/pro family | 78.7% | 2026-07-06T01:12:23Z |

Quota events in the last 24h:

- `critical`: `gpt-oss-120b-medium` at 8.7% remaining, 2026-07-05T18:26:33Z.
- `exhausted`: `claude-opus-4.6-thinking`, `claude-sonnet-4.6-thinking`, and `gpt-oss-120b-medium` at 0.2% remaining, 2026-07-05T18:45:03Z.

## Automated throttle / stop actions

### Implemented controls

1. **Dispatcher credit warning delay** — `prismatic/dispatcher.py:1580-1707`
   - Scans `assets`, `designs`, `research`, and `/tmp` for media artifacts.
   - Calls `evaluate_exhaustion_warning(lookback_hours=1.0)`.
   - If warning fires, sets `throttle_dispatch = True` and delays each dispatch by 5 seconds.

2. **Credit policy decisions** — `prismatic/dispatcher.py:1643-1672`
   - Calls `evaluate_agent_launch(label, issue_id, operation='code_generation')`.
   - `DENY` decisions block dispatch, add a Linear comment, log metrics, and mark the issue processed for the cycle.
   - `WARN` decisions log but continue.
   - `ASK_USER` decisions are skipped in headless dispatcher mode.

3. **Quota-state events** — `prismatic/agy_quota_state.py`
   - Stores per-model quota snapshots in `~/.prismatic/quota_state.db`.
   - Emits `warning`, `critical`, `exhausted`, and `recovered` events.
   - Supports throttle checks with configurable warn/critical/pause thresholds.

4. **Lane stop ledger** — `event_router.db:lane_stops`
   - `agy` lane had a stop until 2026-07-05T19:39:32Z.
   - At audit time, that stop was expired.
   - `unknown` lane stop had also expired.

### Gaps / risks

| Severity | Finding | Impact | Recommended fix |
|---|---|---|---|
| 🔴 High | Monthly exhausted state does not independently hard-stop dispatch when 1h velocity is 0. | If the system idles after burning through the allowance, the tracker can appear quiet despite zero remaining monthly headroom. | Add a hard `remaining <= 0` check before velocity math; return `CRITICAL` even when current burn is idle. |
| 🔴 High | `throttle_dispatch` only sleeps 5 seconds. | Delay is not meaningful protection when monthly headroom is exhausted or Claude/GPT quota is at 0.2%. | Convert critical/exhausted states to deny/no-dispatch for expensive AGY models until reset or manual override. |
| 🟡 Medium | Quota-state pause is model-specific, but dispatcher policy path is agent/operation-centric. | A model can be exhausted while a generic `agent:agy` launch still proceeds unless launcher selection checks the quota DB. | Wire `agy_quota_state.can_dispatch(model)` into AGY launcher/model routing. |
| 🟡 Medium | `CreditPolicyEngine` monthly state is process-local plus a JSON aggregate, while the durable ledger is SQLite. | Multiple dispatcher processes can disagree on monthly total and overwrite JSON. | Make policy monthly checks read from `telemetry_credit_ledger` or a single locked budget table. |
| 🟡 Medium | Linear budget rejects are visible after the fact only. | 10,041 recent rejects indicate dispatch/API loops can slam the rate gate before backoff stabilizes. | Treat repeated `reject` logs as a circuit-breaker input, not just a log. |
| 🟡 Medium | `post_linear_comment()` in `credit_tracker.py` builds GraphQL by string interpolation. | Multi-line markdown/body escaping can fail or corrupt comments. | Use GraphQL variables via Python `urllib` or the existing Linear helper, not inline mutation strings. |

## Current status

🔴 **Not healthy for high-cost AGY/Claude/GPT workloads.** Claude Opus/Sonnet and GPT-OSS quota snapshots are effectively exhausted at 0.2% remaining, and monthly google-antigravity ledger spend is far beyond the configured 25K/month allocation.

🟡 **Gemini flash/pro quota still has room** at 78.7% until 2026-07-06T01:12:23Z, but the historical ledger indicates the system can burn tens of thousands of credits/day when AGY dispatch is active.

🟡 **Automated controls are present but not strict enough.** They warn, delay, and sometimes deny policy requests, but do not consistently hard-stop the expensive path when monthly or model quota is exhausted.

## Recommended next actions

1. **Immediate:** stop dispatching expensive AGY model routes (`claude-*`, `gpt-oss-120b-medium`) until the 2026-07-09 reset or an explicit override.
2. **Immediate:** update `AIUltraCreditTracker.evaluate_exhaustion_warning()` to emit a critical alert when `remaining_credits <= 0`, independent of current velocity.
3. **Short-term:** replace the dispatcher 5-second throttle with a hard deny when quota state is `critical`/`exhausted` for the selected model.
4. **Short-term:** add a daily headroom check that reports: monthly spent, remaining, previous-24h burn, projected days-to-zero, and active lane stops.
5. **Short-term:** make repeated LinearBudget rejects trip a temporary dispatcher circuit breaker.
6. **Cleanup:** fix `credit_tracker.post_linear_comment()` to use GraphQL variables and import `sys` if it continues printing errors to `sys.stderr`.

## Verification commands run

```bash
python3 /home/ubuntu/work/prismatic-engine/prismatic/agy_quota_state.py --json
sqlite3-style Python reads against /home/ubuntu/work/prismatic-engine/prismatic_state/event_router.db
sqlite3-style Python reads against /home/ubuntu/work/prismatic-engine/prismatic_state/linear_budget.db
read_file prismatic/credit_tracker.py
read_file prismatic/credit_policy_engine.py
read_file prismatic/dispatcher.py:1580-1707
```

No write mutations were made to runtime DBs; database access was read-only for this audit.
