# Trigger File Resurrection Pattern

## The Problem

A trigger file (`/tmp/trigger-fred-work` or `/tmp/prismatic/nudge-*`) is found, processed, and deleted by the nudge executor — but re-appears on a subsequent poll cycle. This means the executor's cleanup was correct, but the **dispatcher re-created** the signal.

This is different from a "silent failure loop" (where the same trigger file never gets deleted because the executor crashes). Resurrection means the executor succeeded but the dispatcher has persistent state for that signal.

## Detection

1. **Track signal_id across sessions.** Each trigger file has a `signal_id` (UUID on line 5 of legacy format, or in the JSON payload for prismatic format). When you process a trigger file:
   - Check the skill's `references/` directory for a file tracking cleaned signals (e.g. `references/cleaned-signals-tracker.json`)
   - If one exists, check if this `signal_id` or `issue_id` appears in it
   - If no tracker exists, create one (or just check the registry `_session_summary` for prior mentions of the same issue ID)

2. **Signal-level dedup:** If the `signal_id` UUID matches one already cleaned, this is a resurrection — the dispatcher is re-creating the exact same signal.

3. **Issue-level pattern:** If the `issue_id` matches one cleaned within the past 24 hours, even with a different signal_id, this is likely a resurrection (the dispatcher may generate a new UUID each time).

## Workflow — When You Detect a Resurrection

### First resurrection (2nd appearance of same issue within 24h)
1. Delete the trigger file as normal
2. Note in registry `_session_summary`: `"⚠️ Trigger file for ISSUE-ID re-appeared. Previously cleaned at HH:MMZ. gDrive MCP verified healthy. Trigger file cleaned again."`
3. Check if the system is still healthy (for system-health issues) — if anything has changed, escalate

### Second resurrection (3rd appearance within 24h)
1. Delete the trigger file
2. Update registry with an ESCALATION note: `"🔴 Trigger file for ISSUE-ID resurrected 2+ times. Dispatcher may have persistent stale signal. Requires dispatcher config audit."`
3. Include this in the report delivery — Michael needs to know a cron job is re-creating signals

### Third+ resurrection (4+ appearances)
1. Delete the trigger file — but this time, block it more aggressively:
   ```bash
   # Create a blocker file so the dispatcher has something to check
   # (if the dispatcher respects marker files)
   touch /tmp/prismatic/blocked-<issue_id>
   ```
2. Report that the dispatcher config itself needs attention. This is no longer a nudge-executor concern — it's infrastructure.

**Note:** Currently (Jun 2026), the only resurrection tracking is manual via the registry `_session_summary`. A structured tracker (`references/cleaned-signals-tracker.json`) is the next evolution but hasn't been built yet. Until then, rely on registry comments and signal-level pattern matching.

## Structured Signal Tracker

A JSON tracker at `references/cleaned-signals-tracker.json` records every cleanup with full history. On each nudge execution:

1. Check the tracker for previous cleanups of the same `issue_id`
2. If found, increment the `resurrection_count` and note the timestamp
3. Apply escalation rules based on resurrection count
4. If NOT found, create a new entry with the first-cleanup timestamp

This replaces the ad-hoc registry `_session_summary` tracking. The tracker survives across all cron sessions because it lives in the skill's `references/` directory. Always check it before processing — a signal that looks new might already have 2+ previous cleanups from a different session.

## Concrete Example — GRO-269 (Jun 2026)

| Event | Timestamp | What Happened |
|-------|-----------|---------------|
| Initial cleanup | 2026-06-07T02:14Z | GRO-269 trigger file found → gDrive MCP healthy → cleaned, registry updated |
| 1st resurrection | 2026-06-07T07:02Z | Same trigger file found again → gDrive MCP still healthy → cleaned again, registry tagged as re-appearance |
| 2nd resurrection | 2026-06-07T07:45Z | **3rd total appearance** in 6 hours. Escalation threshold reached. Created `_trigger_file_resurrections` array in registry. Created `references/cleaned-signals-tracker.json`. |
| Count | 3 appearances | 2nd resurrection — **escalated**. Dispatcher needs infrastructure-level fix. |

**What happened:**
- The dispatcher has persistent state for the "gdrive MCP OAuth" signal
- The MCP self-healed (OAuth tokens refreshed naturally), but the dispatcher never received a "signal resolved" acknowledgment
- Each new poll cycle re-creates the trigger file because the dispatcher's state machine still thinks the signal is pending
- The fix is a dispatcher-level change (acknowledge the "already resolved" response from the nudge executor — or, for issue IDs in a Done state, skip re-emitting the trigger)

**What the nudge executor did:**
- ✅ Correctly identified this as a system-health issue (no Step 0.5 artifact search needed)
- ✅ Called `mcp_gdrive_drive_about()` to verify health
- ✅ Cleaned trigger file on success
- ✅ Updated registry with re-appearance tracking
- ✅ Created structured tracker (`references/cleaned-signals-tracker.json`) on 3rd appearance

## Relationship to Other Patterns

| Pattern | Key Difference |
|---------|---------------|
| **Silent failure loop** (`dispatcher-comment-spam-loop-detection.md`) | Trigger file NEVER gets deleted — executor crashes or times out before cleanup. 47+ dispatcher comments on the issue. |
| **Trigger file resurrection** (this doc) | Trigger file IS deleted, but dispatcher RE-CREATES it. 0-2 dispatcher comments because each cycle completes normally. |
| **Stale archived issue** (`stale-archived-issue-handling.md`) | Trigger file points to an issue that no longer exists in Linear. Work still needed. |

The resurrection pattern is the hardest to detect because it looks like normal operation — the executor runs, cleans up, reports success — but the signal keeps coming back.

## Prevention

**Short-term:** Nudge executor should always report resurrection counts in its delivery output so Michael sees the pattern.

**Medium-term:** The dispatcher needs to support a signal-acknowledgment protocol:
1. Nudge executor processes a signal → writes `<signal_id>.resolved` to a known location
2. Dispatcher checks for `.resolved` files before re-emitting the same signal
3. Dispatcher deletes the `.resolved` file after acknowledging

**Long-term:** Move all signals to the prismatic JSON format, which supports `metadata.status` and `metadata.resolved_at` fields that the dispatcher can check before re-emitting.
