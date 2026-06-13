# Dispatcher Dedup — Architecture & Recovery

## How Dedup Works

The dispatcher (`agent_dispatcher.py`) uses `EventRouterDedup` backed by SQLite at:
`$PRISMATIC_HOME/.hermes/profiles/orchestrator/state/event-router/router.db`

Table: `processed_events` — columns: `dedup_key`, `event_type`, `created_at`, `expires_at`, `metadata`

Default TTL for `linear` event type: **3600 seconds (1 hour)**.

Two control points in the dispatch loop:
1. **`is_issue_dispatched_recently(issue_id, label_name)`** — checks if dedup_key exists and hasn't expired
2. **`mark_issue_dispatched(issue_id, label_name)`** — writes dedup_key with 1hr TTL

## Signal vs Launch Agents

| Agent type | Examples | Dedup? | Why |
|------------|----------|--------|-----|
| **Signal** | fred, ned, kai, autobot | NO | Self-manage via cron; re-signaling is harmless (nudge file overwrite) |
| **Launch** | agy, jules, codex | YES | Spawning duplicate processes wastes resources |

The dispatcher skips dedup check entirely for signal agents (line 1053 in agent_dispatcher.py).

## Root Cause Fix (Applied Jun 11, 2026)

**Before fix:** `mark_issue_dispatched()` was called BEFORE `launcher()`. Any launch failure (including no-op signals for non-existent launchers) permanently blocked the issue for 60 minutes.

**After fix:** `mark_issue_dispatched()` is called AFTER successful launch only. Failed launches don't create dedup entries, allowing retry on the next cycle.

## Recovery Procedure (when all new issues show "already dispatched")

```bash
python3 -c "
import sqlite3
db = '${PRISMATIC_HOME}/.hermes/profiles/orchestrator/state/event-router/router.db'
conn = sqlite3.connect(db)
conn.execute('DELETE FROM processed_events')
conn.commit()
print(f'Cleared. Remaining: {conn.execute(\"SELECT COUNT(*) FROM processed_events\").fetchone()[0]}')
conn.close()
"
```

Then re-fire the dispatcher: `cronjob(action='run', job_id='e2f1a3b4c5d6')`

## Dedup Key Format

`linear:{issue_identifier}:{agent_label}` — e.g., `linear:GRO-1173:agent:ned`

Query to inspect: `SELECT dedup_key, datetime(created_at, 'unixepoch', 'localtime'), (expires_at - created_at)/60 AS ttl_min FROM processed_events ORDER BY created_at DESC LIMIT 20;`
