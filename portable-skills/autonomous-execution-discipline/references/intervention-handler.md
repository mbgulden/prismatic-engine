# Intervention Handler — HALT/PAUSE/RESUME System

**Built:** GRO-798 (Jun 8, 2026)
**Location:** `scripts/intervention_handler.py` (orchestrator profile) + `ops/intervention_handler.py` (repo)

## Architecture

```
User posts /agent:halt on GRO-123 Linear issue
    ↓
Agent Dispatcher reads comments (15-min poll)
    ↓
check_intervention() in intervention_handler.py:
  1. Parses comments for /agent:halt, /agent:pause, /agent:resume, /agent:kill
  2. Writes SignalProvider nudge file at /tmp/prismatic/nudge-<agent>
  3. Finds running agent PIDs (pgrep -f <issue_id>)
  4. Sends OS signals (SIGTERM/SIGSTOP/SIGCONT/SIGKILL)
  5. Returns intervention dict → dispatcher skips dispatch
  6. Dispatcher posts confirmation comment on the Linear issue
```

## Integration Points

### In the Agent Dispatcher (`agent_dispatcher.py`)
- Import: `from intervention_handler import check_intervention`
- Runs inside the dispatch loop, AFTER dedup check, BEFORE launcher call
- Checks every issue for ALL agents (fred, kai, agy, jules, codex, autobot)
- On detection: writes signal, kills processes, posts confirmation, skips dispatch

### In the Nudge Executor (Fred's cron)
- **Current state (Jun 8, 2026):** The nudge executor does NOT check for intervention signals before starting work. This is a GAP.
- **Expected behavior:** The nudge executor should call `handle_intervention_signal(target="fred", issue_id=...)` BEFORE executing any work. If it returns an intervention, abort the task.
- **Implementation needed:** Add this check to the nudge executor's Step 1 (after reading trigger file, before Step 0).

## Files

| File | Purpose |
|------|---------|
| `scripts/intervention_handler.py` (orchestrator) | Deployed module — comment parsing, signal writing, process management |
| `scripts/agent_dispatcher.py` | Integration — intervention check in dispatch loop |
| `ops/intervention_handler.py` (repo copy) | Version-controlled copy |
| `schemas/agent-run-record-schema.json` | `halted`/`paused` states |
| `ops/agent-run-records/run_records.py` | Validator with new states |
| `docs/intervention-buttons.md` | User-facing documentation |

## Commands

| Command | Action | OS Signal | Effect |
|---------|--------|-----------|--------|
| `/agent:halt` | Graceful stop | SIGTERM → SIGKILL (3s) | Stops current agent, prevents re-dispatch |
| `/agent:kill` | Force stop | SIGKILL (immediate) | Hard kill |
| `/agent:pause` | Suspend | SIGSTOP | Freezes agent (resumable) |
| `/agent:resume` | Resume | SIGCONT | Un-freezes paused agent |

## Key Functions

- `parse_comments(comments)` → `list[dict]`: Scan comments for intervention commands. Returns newest-first.
- `has_active_intervention(comments)` → `Optional[dict]`: Returns newest intervention if not superseded by resume.
- `check_intervention(issue_id, comments, current_agent)` → `Optional[dict]`: One-call check for dispatcher. Writes signal, kills processes, returns intervention details.
- `handle_intervention_signal(target, issue_id, pids)` → `Optional[dict]`: Called by running agents to check for and handle signals. Acknowledges the signal after processing.
- `halt_processes(pids, force=False)` → `dict`: SIGTERM then SIGKILL after 3s grace period.
- `pause_processes(pids)` → `int`: SIGSTOP.
- `resume_processes(pids)` → `int`: SIGCONT.
