# Prismatic Engine — Operational Runbook

This runbook outlines diagnosis and remediation steps for operators, SREs, and developers debugging issues in the **Prismatic Engine 7-Step Loop** and **Mode Switch** systems.

---

## 1. Fast Diagnostics Flowchart

When an orchestration job fails, follow this triage flow:

```
                  ┌──────────────────────────────┐
                  │    Task / Issue Halted       │
                  └──────────────┬───────────────┘
                                 │
                     [Is FSM in PENDING State?]
                                 ├───► YES: Human Input Needed (Check Approval Queue)
                                 │
                     [Is Lock Stalled / Held?]
                                 ├───► YES: Release locks manually (swarm.js release)
                                 │
                     [Is Iteration Limit Tripped?]
                                 └───► YES: Code loopback exhausted (Investigate logs)
```

---

## 2. Common Failure Modes & Remediation

### Failure Mode 1: Stuck in PENDING State (HITL Gate Wait)
* **Description**: Pipeline execution stops. The state machine shows `PENDING_PLAN_APPROVAL` or `PENDING_INTEGRATION`, but no notifications are processed.
* **Diagnosis**:
  1. Inspect the on-disk state file `prismatic_state/pipelines/<issue_id>.json`.
  2. Verify the `current_step` attribute matches the pending state.
  3. Check gateway logs (`gateway_debug.log` or Port 9000 daemon status) for notification delivery failures.
* **Remediation**:
  - Manually advance the pipeline via the CLI:
    ```bash
    prismatic-engine queue approve <issue_id>
    ```
  - Or reject and route back to refinement:
    ```bash
    prismatic-engine queue reject <issue_id> --reason "Developer manual override"
    ```

### Failure Mode 2: Mode Configuration Conflicts (Drift)
* **Description**: The dispatcher executes a task autonomously when it should require approvals, or halts unexpectedly.
* **Diagnosis**:
  - The mode loaded from the FSM disk snapshot `prismatic_state/pipelines/<issue_id>.json` overrides the global `PRISMATIC_ENGINE.yaml` configuration.
  - Check active mode inside the snapshot: `"mode": "autonomous"`.
  - Check `PRISMATIC_ENGINE.yaml`: `mode: collaborative`.
* **Remediation**:
  - Run the CLI command to force update the pipeline orchestration mode:
    ```bash
    prismatic-engine update-mode <issue_id> --mode collaborative
    ```
  - Alternatively, delete the cache file to force rebuild state matching yaml configurations (Warning: clears history):
    ```bash
    rm prismatic_state/pipelines/<issue_id>.json
    ```

### Failure Mode 3: Refinement Loop Exhaustion (Infinite Loops)
* **Description**: The agent worker and automated reviewer are stuck in a `REVIEW` $\rightarrow$ `FEEDBACK` $\rightarrow$ `REFINE` loop, exhausting credit policies or model token budgets.
* **Diagnosis**:
  1. Query database telemetry or inspect FSM snapshot for `review_cycles` count.
  2. Read the error log in `.antigravity/contracts/<threadId>_feedback.json` to identify why the test/compile fails.
* **Remediation**:
  - Under normal conditions, the refinement breaker trips at iteration 3, escalating to `interactive` mode.
  - If the breaker fails to trip, stop the daemon process:
    ```bash
    pm2 stop prismatic-dispatcher
    ```
  - Manually fix the source code in the active git branch (`feature/issue-id`), commit, and push.
  - Override the FSM status directly to `REVIEW_PASSED` to proceed:
    ```bash
    prismatic-engine override-state <issue_id> --to review
    ```

### Failure Mode 4: Deadlocks due to Lock Stalling
* **Description**: Worker agent crashes or terminates abruptly before releasing mutex locks, preventing other agents from editing target files.
* **Diagnosis**:
  1. Attempting to run a task yields: `Error: File <filepath> is locked by thread <threadId>`.
  2. Verify lock existence in `.antigravity/` / sqlite DB registry.
  3. Verify heartbeat file timestamp: `ls -la .antigravity/locks/heartbeat-<threadId>`.
* **Remediation**:
  - If the heartbeat is older than 5 minutes, SRE watchdog should auto-remediate.
  - To force release a lock manually:
    ```bash
    node .antigravity/swarm.js unlock <filepath> <threadId>
    ```
  - Or clear all active locks:
    ```bash
    node .antigravity/swarm.js clear-locks
    ```

---

## 3. Useful Debugging Commands

Use these commands to diagnose dispatcher and state machine issues:

### 1. Check Dispatcher Service Logs
```bash
tail -n 100 -f /var/log/prismatic-dispatcher.log
```

### 2. Inspect State Machine Snapshot
```bash
cat prismatic_state/pipelines/GRO-1234.json
```
Example Output:
```json
{
  "issue_id": "GRO-1234",
  "mode": "collaborative",
  "current_step": "review",
  "review_cycles": 1,
  "is_terminal": false,
  "history": [
    {
      "from_step": "created",
      "to_step": "decompose",
      "timestamp": "2026-06-17T17:00:00.123456Z"
    }
  ]
}
```

### 3. Query Database Telemetry (SQLite)
To query the count of loop refinement occurrences:
```sql
SELECT parent_id, count(*) 
FROM telemetry_loop_events 
WHERE loop_type = 'refine' 
GROUP BY parent_id;
```
