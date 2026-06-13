# Dispatcher Dedup Clearing

When ALL AGY issues show "Already dispatched ... skipping (dedup)" — the dedup DB is poisoned.

## Detection
Dispatcher report shows: `Newly Launched: 0` + `Status: All dedup'd (within TTL)` for AGY lane, despite 10+ issues with `agent:agy` label sitting in Todo/Backlog.

## Clear It

```bash
python3 << 'PYEOF'
import sqlite3
db = "/home/ubuntu/.hermes/profiles/orchestrator/state/event-router/router.db"
conn = sqlite3.connect(db)
conn.execute("DELETE FROM processed_events")
conn.commit()
count = conn.execute("SELECT COUNT(*) FROM processed_events").fetchone()[0]
print(f"Cleared. Remaining: {count}")
conn.close()
PYEOF
```

## Verify

Re-run the dispatcher or wait for the next cron tick. AGY issues should now launch.

## Root Cause

The dispatcher calls `mark_issue_dispatched()` BEFORE `launcher()`. If the launch fails (timeout, connection error, no-op), the dedup entry persists for the TTL window (60 min) and permanently blocks retries. The fix was applied June 11, 2026 (moved dedup mark to AFTER successful launch), but stale entries from before the fix persist until manually cleared.

## Pitfall

- `sqlite3` binary may not be installed on the host. Use the Python approach above (stdlib `sqlite3` module always available).
- After clearing, check that new dispatcher runs don't re-poison the DB — if they do, the fix hasn't been applied to the running dispatcher script.
