# Event Router Dedup Database — Architecture Reference

## Overview

The SQLite dedup database (GRO-669) prevents duplicate dispatch by the agent dispatcher. Built per the NAS blueprint spec (`event-router-architecture.md §Dedup`).

**File:** `~/.hermes/profiles/orchestrator/scripts/event_router_dedup.py`
**Database:** `~/.hermes/profiles/orchestrator/state/event-router/router.db` (SQLite, WAL mode)

## Dedup Key Format

| Type | Key Pattern | TTL | Purpose |
|------|-------------|-----|---------|
| Linear | `linear:<issue_id>:<agent_label>` | 1 hour | Prevent re-dispatching same issue to same agent |
| Command | `command:<issue_id>:<comment_id>` | 24 hours | Prevent re-processing the same `/agent` comment |
| GitHub | `github:<repo>:<pr_number>:<event_id>` | 24 hours | PR event dedup (future use) |
| Cron | `cron:<queue_name>:<bucket>:<issue_id>` | 30 min | Cron reconciliation (future use) |
| Manual | `manual:<button_event_id>` | 72 hours | Manual button events (future use) |

## Public API

```python
from event_router_dedup import get_dedup

dd = get_dedup()

# Dispatch dedup
dd.mark_issue_dispatched("GRO-669", "agent:fred")
dd.is_issue_dispatched_recently("GRO-669", "agent:fred")  # True
dd.is_issue_dispatched_recently("GRO-669", "agent:agy")    # False

# Command dedup
dd.mark_command_processed("GRO-669", "comment-uuid-123")
dd.is_command_processed("GRO-669", "comment-uuid-123")  # True
dd.is_command_processed("GRO-669", "comment-uuid-456")  # False

# Maintenance
dd.force_cleanup()    # Delete expired entries
dd.get_stats()        # {total_entries, active_entries, by_type, db_size_bytes}
```

## CLI Usage

```bash
python3 event_router_dedup.py stats     # Show database statistics
python3 event_router_dedup.py cleanup   # Force cleanup expired entries
python3 event_router_dedup.py check <key>  # Check if a dedup key exists
```

## Integration Points in agent_dispatcher.py

### 1. Pre-Dispatch Check (line ~950)
Before launching any agent, the dispatcher checks `dedup_db.is_issue_dispatched_recently(issue_id, label_name)`. If True → skip with "Already dispatched — skipping (dedup)".

### 2. Post-Dispatch Marking (line ~958)
The dispatch is recorded **before** the agent launch (not after). Even if the launch itself fails, the dedup entry prevents infinite retry loops on consecutive dispatcher cycles.

### 3. Command Dedup (line ~795)
Before processing a `/agent` comment, checks `dedup_db.is_command_processed(identifier, comment_id)`. The command is marked as processed immediately upon parsing.

### 4. Periodic Cleanup (line ~925)
`dedup_db.force_cleanup()` runs on every dispatcher invocation. Only cleans if >1 hour since last cleanup.

### 5. Output Stats (line ~1010)
Every dispatcher cycle prints:
```
🗄️  Dedup DB: 12 active entries (15 total, 24576 bytes)
   Types: command=8, linear=4
```

## Architecture Decision: Mark Before Launch

The dispatch is recorded BEFORE the agent launch, not after. Rationale:

- A launch failure (AGY crash, Jules auth error) on one cycle should NOT trigger re-dispatch on the next cycle 15 min later
- The 1-hour TTL gives time for human intervention or log review
- If the issue needs re-dispatch after a genuine fix, wait 1 hour or delete the dedup entry manually:
  ```bash
  sqlite3 ~/.hermes/profiles/orchestrator/state/event-router/router.db \
    "DELETE FROM processed_events WHERE dedup_key LIKE 'linear:GRO-669:%';"
  ```

## Breaking the Dispatcher Spam Loop

Before the dedup database, the dispatcher processed the same issue every 15 min cycle indefinitely. Issues like GRO-151 and GRO-669 accumulated 47-50 identical "Dispatcher: routed to Fred" comments with zero agent output.

The dedup database is the **proactive** fix — it prevents the spam loop from starting. The existing **reactive** fix (5+ dispatcher comments → break the loop) in the nudge executor pipeline handoff still applies to issues that accumulated comments before the dedup DB was deployed.

## Thread Safety

- SQLite WAL mode allows concurrent reads
- Single `threading.Lock` wraps all writes
- No multi-process safety — the dispatcher is single-process
