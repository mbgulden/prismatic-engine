# Nudge Execution Architecture (June 2026)

## Signal Flow

```
Linear issue labeled agent:fred (or agent:kai)
    │
    ▼
Unified Agent Dispatcher (agent_dispatcher.py, every 15min)
    │  Reads Linear labels, calls signal_provider.send_work()
    ▼
SignalProvider → FileSignalProvider.send()
    │  Writes /tmp/prismatic/nudge-{target} (atomic JSON, flock-locked)
    ▼
Nudge Detection (cron 400a5a41cc45, script-only, every 1min)
    │  nudge_executor.py reads nudge files
    │  ┌─ Fred nudge: writes /tmp/trigger-fred-work (retry counter)
    │  │   cleans nudge files immediately
    │  └─ Kai/other nudge: ALSO writes trigger, cleans nudge
    │      (Kai issues are processed by same LLM executor)
    ▼
Nudge Executor (cron c2cce4fec4ed, LLM deepseek-v4-flash, every 5min)
    │  Reads trigger file → loads autonomous-execution-discipline + golden-thread
    │  Processes the issue from Linear
    │  ┌─ SUCCESS: deletes trigger file
    │  └─ FAILURE: increments retries_done, re-writes trigger
    │      After 3 failures: comments on Linear, deletes trigger, notifies Michael
```

## Trigger File Format

```
{retries_done}
{max_retries}
{issue_id}
{title}
{signal_id}
```

Example:
```
0
3
GRO-755
Implement SignalProvider — swappable agent signaling interface
abc12345-1234-1234-1234-123456789abc
```

## State Tracking

`/tmp/prismatic/.seen_signals.json` — tracks every signal_id seen:
```json
{
  "abc12345": {
    "target": "fred",
    "issue_id": "GRO-755",
    "seen_at": 1749225000,
    "nudged": true
  }
}
```

- `nudged: true` → trigger file written, LLM cron handles retries
- `nudged: false` → notification sent, waiting for agent to process
- Entries older than 24h are cleaned

## Stale Escalation

Implemented in `prismatic/nudge_executor.py` (standalone `python -m prismatic.nudge_executor`, runs every 5min via crontab).

- **30 min** (`NUDGE_STALE_MINUTES`): ⚠️ Stale warning printed to stdout (visible in cron logs at `/tmp/nudge_executor.log`). For Fred-targeted nudges, also flags "Fred's cron executor may be stuck."
- **60 min** (`NUDGE_ESCALATE_MINUTES`): If target != fred, writes `/tmp/trigger-fred-work` to cross-assign orphaned work. If target == fred, prints 🔴 critically stuck indicator.
- **Exit codes**: 0 = clean, 1 = stale detected, 2 = escalation triggered

Config via env vars: `PRISMATIC_NUDGE_DIR`, `NUDGE_STALE_MINUTES`, `NUDGE_ESCALATE_MINUTES`, `NUDGE_TRIGGER_FILE`.
See `~/work/prismatic-engine-staging/prismatic/nudge_executor.py`.

## Cron Inventory

| Job ID | Name | Type | Every | Model |
|--------|------|------|-------|-------|
| `400a5a41cc45` | Nudge Detection | Script-only | 1 min | N/A |
| `c2cce4fec4ed` | Nudge Executor | LLM | 5 min | deepseek-v4-flash |
| `2479e52d4bbe` | GPU Health Monitor | Script-only | 5 min | N/A |
| (system crontab) | Stale Nudge Escalation | Script-only | 5 min | N/A (python3 -m prismatic.nudge_executor) |

## Migration History

1. **Pre-June 2026**: LLM cron `23f8447b629a` checked `/tmp/nudge-fred` every minute (1,440 tokens/day)
2. **June 6 (attempt 1)**: Replaced with notification-only cron `ea68d7a92068` — detection worked, execution didn't. Michael got pinged 8 times for the same stale signal.
3. **June 6 (fix)**: Two-cron architecture: detection is script-only, execution is LLM-gated behind trigger file. Both legs must exist.
4. **June 6 (refinement)**: Added retry counter, stale escalation, Kai auto-processing, backward-compat shim removed.

## Key Pitfalls

- **Never replace execution with notification**: Detection ≠ execution. Both crons required.
- **Nudge poller must NEVER auto-complete tasks (CRITICAL — Jun 2026):** A signal-polling agent that transitions `agent:<target>` → `agent:done` on nudge receipt is auto-completing work without execution. The poller's job is to ROUTE, not to complete. Only the execution agent (after explicit verification) marks Done. See `references/signal-agent-never-auto-complete.md` for the full pattern with the Kai nudge_poller.py case study. Signs of this bug: poller posts "✅ completed processing" without doing work, or issues arrive Done that were never touched.
- **Nudge file path migration**: SignalProvider writes to `/tmp/prismatic/nudge-*`. Agents that poll `/tmp/nudge-*` won't see them. Old backward-compat shim has been removed — all consumers must read the new path.
- **Model selection**: Nudge Executor uses `deepseek-v4-flash` (cheapest cloud). When GPU node `100.78.237.7` is online, switch to `qwen3:32b` (free, unlimited). Never use `deepseek-v4-pro` for cron tasks.
