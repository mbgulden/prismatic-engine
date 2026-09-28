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


## 4. Backup & Restore

### 4.1 What is backed up

Daily snapshots land in `/archive/prismatic-bus-snapshots/` (14-day retention per store,
oldest pruned automatically). An off-box copy syncs to the synology NFS mount at
`/mnt/synology-agentic-context/prismatic-snapshots/` (append-only — never pruned off-box).

| Store | Snapshot prefix | Method | RPO |
|---|---|---|---|
| Event bus (`~/.prismatic/bus/event_log.sqlite`) | `event_log.sqlite.` | sqlite backup API (WAL-safe) | ~24h |
| Review factory (`~/.prismatic/state/agy_completed_work.db`) | `agy_completed_work.sqlite.` | sqlite backup API (WAL-safe) | ~24h |
| Trust ledger (`~/.prismatic/audit/trust-ledger.jsonl`) | `trust-ledger.jsonl.` | copy + full JSON validation | ~24h |
| Merge receipts (`~/.prismatic/merge-receipts.jsonl`) | `merge-receipts.jsonl.` | copy + full JSON validation | ~24h |
| Webhook receipts (`~/.prismatic/db/merge-receipts.jsonl`) | `merge-receipts-db.jsonl.` | copy + full JSON validation | ~24h |

Snapshot files are named `<prefix>.YYYYMMDD_HHMMSS` (UTC).

### 4.2 How it runs

- **Writer:** the Hermes orchestrator cron job `event_log_backup` (daily 02:00 MDT =
  08:00 UTC) executes `/home/ubuntu/.hermes/profiles/orchestrator/scripts/prismatic_bus_backup.py`.
  The script integrity-checks each live sqlite DB *before* snapshotting, verifies every
  snapshot after writing, prunes to 14 days, and exits non-zero with a stderr message on
  any failure — failures are loud, never silent. A per-run report lands in the
  orchestrator's cron output dir (`.../cron/output/event_log_backup/`).
- **Off-box sync:** crontab `30 8 * * *` rsyncs the snapshot dir to the synology mount
  (log: `/home/ubuntu/.hermes/logs/prismatic_snapshot_sync.log`).
- **Gotchas the automation already handles:**
  - The live DBs run in WAL mode — a naive `cp` of the `.db` file misses the WAL and
    yields a stale view. Always use the sqlite backup API (or checkpoint first).
  - The bus DB's cron-authority tables carry `CHECK` constraints calling custom SQL
    functions (`is_utc_timestamp`, `sha256_hex`). `PRAGMA integrity_check` fails with
    "unknown function" unless those are registered on the checking connection — this is
    a checker artifact, not corruption.

### 4.3 What is NOT backed up

- `~/.prismatic/keys/` (receipt-signing key) and `~/.prismatic/env.d/` (deploy HMAC
  secret) — **no backup exists.** Regenerating the signing key invalidates all existing
  receipt signatures; key rotation needs its own plan.
- systemd unit files (only ad-hoc `.bak` copies exist under `~/.prismatic/backups/`).

### 4.4 Restoring a single store (verified 2026-09-28 via /tmp dry-run)

Stop the services that write to the target store first, then:

**Event bus / review-factory DB (sqlite):**
```bash
# 1. Pick the snapshot
ls -lt /archive/prismatic-bus-snapshots/event_log.sqlite.* | head -3
# 2. Remove live DB *and* its WAL/SHM sidecars (WAL mode: all three must go)
rm ~/.prismatic/bus/event_log.sqlite ~/.prismatic/bus/event_log.sqlite-wal ~/.prismatic/bus/event_log.sqlite-shm
# 3. Copy the snapshot into place (plain cp is fine here: the snapshot itself is a clean DB)
cp /archive/prismatic-bus-snapshots/event_log.sqlite.<STAMP> ~/.prismatic/bus/event_log.sqlite
# 4. Verify before restarting services (register custom functions first, see 4.2)
python3 -c "
import sqlite3
c = sqlite3.connect('$HOME/.prismatic/bus/event_log.sqlite')
c.create_function('is_utc_timestamp', 1, lambda s: 1)
c.create_function('sha256_hex', 1, lambda b: 'x')
print(c.execute('PRAGMA integrity_check').fetchone()[0])
"
# 5. Restart services, confirm the consumer cursor still matches the bus max rowid
```

**Trust ledger / merge receipts (jsonl):** copy the newest snapshot over the live file,
then validate every line parses as JSON.

### 4.5 Full box loss (bare-metal rebuild)

1. Provision replacement host, install Tailscale, rejoin tailnet. **[UNVERIFIED]**
2. Mount the synology NFS share; copy the newest snapshot set from
   `/mnt/synology-agentic-context/prismatic-snapshots/` to `/archive/prismatic-bus-snapshots/`.
3. Restore each store per 4.4 into a fresh `~/.prismatic/` tree.
4. Recreate `keys/` and `env.d/` — **no backups exist; secrets must be regenerated**,
   which invalidates all prior receipt signatures. **[UNVERIFIED — no key rotation procedure]**
5. Reinstall systemd units (units are not in the repo; reconstruct from `.bak` copies
   under `~/.prismatic/backups/` or the old host's notes). **[UNVERIFIED]**
6. Restart services in dependency order; reset/repair the consumer cursor if
   `dispatch_consumer.rowid` was lost (consumer refuses to start when cursor > bus max —
   use its `--repair-apply` flow). **[UNVERIFIED]**

### 4.6 Verifying backups (do this after any change to the snapshot job)

```bash
python3 /home/ubuntu/.hermes/profiles/orchestrator/scripts/prismatic_bus_backup.py
# expect: one line per store with integrity/row/line counts, then "Success: ..."
ls /mnt/synology-agentic-context/prismatic-snapshots/ | tail -5
# expect: today's snapshot set present off-box
```
