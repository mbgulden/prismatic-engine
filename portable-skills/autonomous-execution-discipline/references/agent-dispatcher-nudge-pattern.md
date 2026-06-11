# Agent Dispatcher — Signal → Action → Execution Pattern

## Architecture (as of June 2026)

The dispatcher (`scripts/agent_dispatcher.py`) routes Linear issues to agents via labels.
The nudge system has THREE layers:

```
Dispatcher (15min)         → writes /tmp/prismatic/nudge-* (SignalPayload JSON)
Nudge Detection (1min)     → reads nudge files, writes trigger, dedup alerts
Nudge Executor (5min)      → reads trigger, processes issue, retries on failure
```

### Layer 1: Dispatcher → SignalProvider

```python
from signal_provider import create_signal_provider, SignalPayload, SignalAction

signal_provider = create_signal_provider()  # FileSignalProvider at /tmp/prismatic/

# In signal_fred(), signal_kai(), signal_autobot():
signal_provider.send_work(
    target="fred",
    issue_id="GRO-755",
    title="Implement SignalProvider",
    priority=3,
)
```

SignalProvider writes atomic JSON to `/tmp/prismatic/nudge-{target}` with:
- `signal_id` (UUID), `target`, `action` (work/review/notify/stop), `issue_id`, `title`, `priority`, `metadata`, `created_at`

### Layer 2: Nudge Detection (script-only, every 1min)

**Cron: `400a5a41cc45`** — `nudge_executor.py` (no_agent=true, deliver=telegram:8190664947)

For each nudge file found:
- **Fred**: writes `/tmp/trigger-fred-work` with retry counter, cleans nudge file
- **Kai/Autobot/others**: writes trigger (same file), cleans nudge file, prints notification
- **Dedup**: tracks signal_ids in `/tmp/prismatic/.seen_signals.json` — one notification per signal
- **Stale warn**: 30+ min unacknowledged → ⚠️ warning printed
- **Stale escalate**: 60+ min unacknowledged → writes Fred trigger (escalated)

**Trigger file format:**
```
0          ← retries_done
3          ← max_retries
GRO-750    ← issue_id
Nav Re-Vamp ← title
uuid...    ← signal_id
```

### Layer 3: Nudge Executor (LLM, every 5min)

**Cron: `c2cce4fec4ed`** — LLM-driven, loads `autonomous-execution-discipline` + `golden-thread`, model=`deepseek-v4-flash`

1. Reads `/tmp/trigger-fred-work` (line 1=retries, line 2=max, line 3=issue_id, line 4=title, line 5=signal_id)
2. Processes the issue via Linear API
3. **Success**: deletes trigger, transitions label to `agent:done`
4. **Failure**: increments retries_done, re-writes trigger. After max_retries (3): comments on issue, deletes trigger, notifies

### Label Transition Rule

Signal agents (fred, kai, autobot) do NOT auto-transition in the dispatcher main loop. The Nudge Executor MUST transition `agent:fred` → `agent:done` after processing. Without this, the dispatcher re-nudges every 15min for the same issue.

### Crons Summary

| Cron ID | Name | Type | Schedule |
|---------|------|------|----------|
| `400a5a41cc45` | Nudge Detection | script-only | * * * * * |
| `c2cce4fec4ed` | Nudge Executor | LLM (deepseek-v4-flash) | every 5m |
| `2479e52d4bbe` | GPU Health Monitor | script-only | every 5m |

Removed crons: `23f8447b629a` (LLM every-min token burner), `ea68d7a92068` (notification-only watch).

## SignalProvider

**File**: `scripts/signal_provider.py` — drop-in alongside dispatcher. Contains `FileSignalProvider` (atomic writes via tempfile+rename, flock locking), `FallbackChain`, `SignalPayload` dataclass, `create_signal_provider()` factory.

**Full package** staged at `~/work/prismatic-engine-staging/prismatic/providers/signals/` with HTTP and Redis backends.

**Key properties:**
- Atomic writes (tempfile → os.rename) — no partial reads
- flock() locking — no race conditions
- signal_id dedup — agents skip duplicate signals
- Provider-agnostic — same interface for file/HTTP/Redis backends

## Agent Launcher Map

```python
AGENT_LAUNCHERS = {
    "agent:agy": launch_agy,          # Spawns AGY subprocess
    "agent:jules": launch_jules,      # Spawns Jules subprocess
    "agent:codex": launch_codex,      # Spawns Codex subprocess
    "agent:fred": signal_fred,        # → signal_provider.send_work("fred", ...)
    "agent:kai": signal_kai,          # → signal_provider.send_work("kai", ...) → trigger-fred-work
    "agent:autobot": signal_autobot,  # → signal_provider.send_work("autobot", ...) → trigger-fred-work
}
```

All signal agents route through the same Nudge Executor LLM cron. Kai and Autobot don't need separate poll loops — the shared executor handles everything.

## Key Files

- `scripts/signal_provider.py` — FileSignalProvider + FallbackChain + SignalPayload
- `scripts/agent_dispatcher.py` — imports signal_provider, routes labels
- `scripts/nudge_executor.py` — detection + dedup + retry + stale escalation (script-only)
- `~/work/prismatic-engine-staging/prismatic/providers/signals/` — full multi-provider package

## Pitfalls

- **LLM cron prompt must be explicit.** A vague prompt like "read the file and process it" fails silently with flash-tier models. The prompt MUST specify exact line-by-line format: "Line 1 = retries_done, Line 2 = max_retries, Line 3 = issue_id..."
- **Signal label never transitions without the executor.** The dispatcher skips auto-transition for fred/kai/autobot labels (line 662: `if label_name not in ("agent:fred", "agent:kai")`). The Nudge Executor MUST transition to `agent:done` on success, or the dispatcher re-nudges every 15min.
- **Trigger file must be deleted by the executor on success OR final failure.** If the executor crashes mid-work, the retry counter ensures re-delivery. If the trigger file persists with retries < max, the next cycle re-processes it.
- **Missing `agent:autobot` from AGENT_CONFIG** was a bug fixed June 2026. When adding new signal agents, add to BOTH AGENT_CONFIG and AGENT_LAUNCHERS.
- **Pipeline tasks** (with `## Robot Agent Pipeline` in description) are NOT auto-transitioned — the agent reads the pipeline context and re-labels itself.
- **GPU node down means no local models.** Nudge Executor uses `deepseek-v4-flash` as fallback. When GPU node (100.78.237.7) is online, switch to `qwen3:32b` for free unlimited inference on small tasks.
