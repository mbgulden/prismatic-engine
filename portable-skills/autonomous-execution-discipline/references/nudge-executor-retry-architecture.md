# Nudge Executor Retry Architecture (Phase 1.5)

## The Problem (GRO-758)

Before GRO-758, the `nudge_executor.py` script deleted the prismatic nudge file immediately after writing `/tmp/trigger-fred-work`. If the LLM cron (Fred's 5-minute cron) failed mid-processing — timeout, rate limit, crash — both the nudge file and the trigger file were gone. The signal was silently lost, and Michael's issue would sit unprocessed indefinitely with no retry mechanism.

## The Fix: Pending Marker Pattern

The nudge file is now a **pending marker**. The script cron never deletes it. Only the LLM cron deletes it, and only after **successful processing**.

```
┌──────────────────────────────────────────────┐
│              Signal Flow                      │
├──────────────────────────────────────────────┤
│                                               │
│  nudge file written by dispatcher             │
│        │                                      │
│        ▼                                      │
│  nudge_executor.py (1min)                     │
│    → writes /tmp/trigger-fred-work (retry=0)  │
│    → KEEPS nudge file                         │
│        │                                      │
│        ▼                                      │
│  LLM cron (5min) reads trigger                │
│    → SUCCESS: deletes BOTH files ✓            │
│    → FAILURE: nudge file remains              │
│        │                                      │
│        ▼  (if nudge persists > 5 min)         │
│  Phase 1.5 retry loop triggers again          │
│    → increments retry counter                 │
│    → re-writes trigger                        │
│    → waits 5 min cooldown                     │
│        │                                      │
│        ▼  (after 3 failed attempts)           │
│  ESCALATION: dead-letter + output             │
│    → writes .escalated-<signal_id>            │
│    → preserves nudge for manual inspection    │
│    → prints escalation to cron output         │
└──────────────────────────────────────────────┘
```

## Phase 1.5 in Detail

The retry loop runs every cycle of `nudge_executor.py` (every 1 minute) but respects a **5-minute cooldown** between re-triggers. This matches the LLM cron interval so we don't write-storm trigger files.

### Trigger Conditions

A signal enters Phase 1.5 when ALL of these are true:
1. It's in the tracker (`/tmp/prismatic/.seen_signals.json`) with `nudged: true`
2. Its nudge file still exists on disk at `/tmp/prismatic/nudge-<agent>`
3. It has NOT been escalated yet
4. It's been ≥ 300 seconds since `last_triggered_at` (cooldown expired)

### Retry Counter Logic

```
entry["retries"] = max(entry["retries"], current_trigger_retry) + 1
```

This reads both the entry's own retry count AND the trigger file's current retry count, takes the max, and adds 1. This prevents counter drift if the trigger file was modified outside the tracker.

### MAX_RETRIES Behavior

When `entry["retries"] >= 3`:
1. Writes dead-letter file: `/tmp/prismatic/.escalated-<signal_id>`
2. Sets `entry["escalated"] = true` (prevents further retries)
3. Prints escalation output to stdout (delivered by the cron system)
4. **Preserves the nudge file** for manual inspection — never delete pending data

### Dead-Letter Format

```json
{
  "signal_id": "uuid-here",
  "issue_id": "GRO-XXX",
  "target": "fred",
  "retries": 3,
  "escalated_at": 1234567890.0,
  "escalated_at_human": "2026-06-08 12:38:00"
}
```

## LLM Cron Cleanup Protocol

The LLM cron (this agent) **MUST** delete both files on successful completion:

```
rm -f /tmp/trigger-fred-work /tmp/prismatic/nudge-<agent>
```

Deleting only the trigger file creates a zombie: the nudge file persists, and Phase 1.5 will treat it as a stuck signal and re-trigger. Always verify:

```bash
ls /tmp/trigger-fred-work /tmp/prismatic/nudge-*  # verify both gone
```

## Testing the Retry Loop

To simulate a transient LLM failure:

```bash
# Write a trigger pointing to a non-existent Linear issue
echo -e "0\n3\nGRO-999\nTest retry\nfake-signal-id\n" > /tmp/trigger-fred-work

# The next 3 LLM cron cycles will fail (issue not found)
# Each failure leaves the nudge file
# Phase 1.5 will re-trigger with incremented retries each cycle
# After cycle 3, check for dead-letter:
ls /tmp/prismatic/.escalated-*
```

To confirm the fix works end-to-end, run `nudge_executor.py` with a real signal and verify:
1. Trigger file is written
2. Nudge file is NOT deleted
3. After `rm -f /tmp/trigger-fred-work` (simulating LLM success), then deleting the nudge file:
4. Next run should see nudge file gone → mark `resolved: true` in tracker

## Pitfalls

- ❌ **LLM cron deleting only the trigger file:** The `nudge_executor.py` re-triggers every 5 min. Always delete BOTH files.
- ❌ **Modifying `MAX_RETRIES` without updating the trigger format:** The trigger file format uses a separate `MAX_RETRIES` constant (currently 3) at write time. If you change the constant in `nudge_executor.py`, old trigger files have the old max baked into line 2.
- ❌ **Overwriting the nudge file externally:** If you manually delete or write to `/tmp/prismatic/nudge-*`, you break the pending-marker contract. The tracker won't match the new file. Only the SignalProvider should write to this directory; only the LLM cron should delete from it on success.
- ❌ **Running `nudge_executor.py` as a test without a tracker file:** Without `/tmp/prismatic/.seen_signals.json`, every run treats the nudge file as NEW and re-triggers with retry=0. To test Phase 1.5, the signal must already be in the tracker with `nudged: true`.
