# GRO-683 Dispatcher Spam Loop — Case Study

**Date:** June 7, 2026  
**Issue:** GRO-683 — Fred + AGY: Design simplest AGY invocation + monitoring pattern for swarm  
**Trigger file:** `/tmp/trigger-fred-work` (legacy format, 0 retries, max 3)  
**Dispatcher comments:** **51** — the highest count recorded across all cleaned signals  
**Agent output comments:** **0** — the dispatcher routed the issue every 15 min for ~24 hours with no successful executor processing  

## Pattern

This is the **cleanest Case 2 example** in the tracker:

1. Nudge executor reads trigger file
2. Counts 51 dispatcher `"routed to Fred"` comments — classic spam loop detection
3. Runs Step 0.5 pre-verification against all 4 work items in the issue description
4. Finds **all 4 items already complete** on disk:
   - `--print` mode tested and documented (GRO-681 research)
   - `agy_monitor.py` exists at `~/.gemini/antigravity-cli/scratch/agy_monitor.py`
   - Invocation pattern fully documented in `antigravity-cli-orchestration` skill
   - `agent_dispatcher.py` `launch_agy()` already uses `--print` + `--dangerously-skip-permissions` + `--print-timeout 10m`
5. Posts "Nudge Executor — Breaking the Loop" comment documenting the pre-verification
6. Transitions `agent:fred` → `agent:done`
7. Moves state Backlog → Done
8. Deletes trigger file
9. Updates `cleaned-signals-tracker.json`

## What Made This Different From GRO-675/GRO-679

| Aspect | GRO-675, GRO-679 | GRO-683 |
|--------|-----------------|---------|
| Agent output comments | 1 (prior executor partially completed) | 0 |
| Prior executor posted comment? | Yes (but crashed before cleanup) | No executor ever reached the issue |
| Work status | Pre-completed by a prior executor session | Pre-completed by diff sessions (GRO-681 research + subsequent implementation) |
| Dispatcher comments | ~42-50 | **51 (record)** |
| Dispatcher comment span | ~16 hours | ~24 hours |

## Why This Happened

The dispatcher (`agent_dispatcher.py`) routes every 15 minutes. Once a trigger file exists at `/tmp/trigger-fred-work`, the nudge executor (running every 5 minutes) is supposed to process it. In this case, the nudge executor **never successfully processed the issue** — every 5-minute cycle either timed out during context loading or crashed, leaving the trigger file intact. The dispatcher kept routing because the issue still had `agent:fred` label, and the nudge file survived.

Root cause: the dedup database (`event_router_dedup.py`, GRO-669) was not yet deployed for these issues. The dispatcher had no protection against re-routing the same issue every cycle.

## Verified Resolution Steps

```python
# Pattern used to break the loop:
# 1. Pre-verify → found Case 2 (all work complete)
# 2. Post breaking-the-loop comment
# 3. Transition label: agent:fred → agent:done
# 4. Move state: Backlog → Done
# 5. Double-verify trigger file deletion
# 6. Update cleaned-signals-tracker
```

## Cluster Context

GRO-683 is the **4th entry** in the cleaned-signals-tracker, all from the same 48-hour window (Jun 6-7, 2026):

| Issue | Comments | Agent Output | Phase |
|-------|----------|-------------|-------|
| GRO-675 | 50+ | 1 | Prismatic Hub Phase 3 |
| GRO-678 | 14 | 0 | Prismatic Hub Phase 4 |
| GRO-679 | 42 | 1 | Prismatic Hub Phase 5 |
| **GRO-683** | **51** | **0** | **AGY invocation pattern** |

All 4 were pre-dedup-DB dispatcher spam loops. The dedup DB (GRO-669) should prevent NEW issues from accumulating this pattern. These 4 are legacy artifacts from before that fix was deployed.

## Key Lesson

The dispatcher-comment-spam-loop-detection pitfall originally said "move to In Progress unconditionally." Step 0.5 Case 2 says "move to Done for completed work." GRO-683 tested this edge case and confirmed: **when Step 0.5 finds pre-completed work, the correct state is Done, not In Progress.** The pitfall was patched Jun 2026 to reconcile this contradiction.
