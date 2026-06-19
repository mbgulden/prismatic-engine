# Linear API Rate Limit Recovery Runbook

This runbook outlines the steps to recognize a Linear API rate limit breach, pause the offending cron jobs, and perform a staged recovery.

---

## 1. Recognizing a Rate Limit Breach

A Linear API rate limit breach typically manifests as an HTTP 400 response from the Linear GraphQL API. 

### Diagnostics & Log Indicators
- **HTTP Status Code**: `400 Bad Request` (instead of HTTP 429)
- **Response Message / Body**: The response body contains the message: `"Only 2500 requests are allowed per 1 hour"`.
- **Log Signatures**:
  Look for the following logs in your service outputs:
  ```
  [LinearTaskProvider] HTTP 400: {"errors":[{"message":"Only 2500 requests are allowed per 1 hour", ...}]}
  ```
  Or in dispatcher/provider warnings:
  ```
  [LinearTaskProvider] HTTP 400: Only 2500 requests are allowed per 1 hour
  ```

---

## 2. Immediate Mitigation: Pause Offending Cron Jobs

When a rate limit breach occurs, you must immediately pause the top rate-limiting consumers (specifically the top 3 high-frequency crons).

### Top 3 Crons to Pause (in order of priority):
1. **Unified Agent Dispatcher**
   - **ID**: `e2f1a3b4c5d6`
   - **Script**: `agent_dispatcher.py`
   - **Original Interval**: Every 2 minutes (slowed down to every 5 minutes during incident response)
   - **Baseline Consumption**: ~900 - 1000 requests/hour
2. **Kai Callback Monitor**
   - **ID**: `ecc080d17c00`
   - **Script**: `kai_callback_monitor.py`
   - **Interval**: Every 2 minutes
   - **Baseline Consumption**: ~90 requests/hour
3. **Comment Trigger Monitor**
   - **ID**: `e59739502d22`
   - **Script**: `comment_trigger_monitor.py`
   - **Interval**: Every 1 minute
   - **Baseline Consumption**: ~60 - 120 requests/hour

### How to Pause Cron Jobs
Modify the local cron configuration in `/home/ubuntu/.hermes/profiles/orchestrator/cron/jobs.json`:
1. Find the job entry by its `id`.
2. Set `"enabled"` to `false`.
3. Set `"state"` to `"paused"`.
4. Alternatively, use the CLI daemon commands if available.

---

## 3. Recovery and Resume Order (Post-GRO-1995)

Once `GRO-1995` lands (which introduces native budget enforcement and batched API request pooling), you can safely resume operations in the following order:

### Phase 1: Resume Low/Medium-Impact Monitors
Resume the background monitoring scripts. They have relatively low overhead but provide essential hooks.
- **Kai Callback Monitor** (`ecc080d17c00`)
- **Comment Trigger Monitor** (`e59739502d22`)
- *Action*: In `/home/ubuntu/.hermes/profiles/orchestrator/cron/jobs.json`, restore `"enabled": true` and `"state": "scheduled"`.

### Phase 2: Resume Dispatcher under Restricted Schedule
Resume the agent dispatcher at a reduced interval.
- **Unified Agent Dispatcher** (`e2f1a3b4c5d6`)
- *Action*: Enable the dispatcher with its **slowed interval** of **5 minutes** (set `"schedule": { "minutes": 5 }` and `"schedule_display": "every 5m"`).
- *Reason*: Keep the dispatcher at 5-minute intervals to allow verification of rate limiting.

### Phase 3: Verify and Restore Dispatcher Interval
- **Condition**: Do **NOT** restore the dispatcher to its original 2-minute interval until budget tracking metrics confirm that the cumulative rate remains safely under **2,000 requests per hour** (allowing a 500-request buffer below the 2,500/hr hard cap).
- Use `prismatic-engine doctor linear-budget` or check `linear_metrics.db` to verify usage.
- Once verified, set dispatcher to 2-minute interval.
