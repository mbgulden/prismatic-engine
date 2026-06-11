# AGY Watchdog Inactivity Recovery — Reference

**Source Issue**: GRO-797
**Implemented**: June 8, 2026
**Port Source**: Hub `Watchdog.ts` (69 lines, VS Code extension v1.1.45)

## Architecture

### Hub Watchdog.ts (Original)

```typescript
class Watchdog {
    lastActivity: number = Date.now();
    timeout: number = 60;
    recovery: () => Promise<void>;

    ping() { this.lastActivity = Date.now(); }
    start() {
        setInterval(() => {
            if ((Date.now() - this.lastActivity) / 1000 > this.timeout) {
                this.recovery();
                this.ping();
            }
        }, 5000);
    }
}
```

Key design: Timer-based, reset by `ping()`, runs **inside** the agent loop.

### Hermes Translation (Python)

The core insight: Hub's Watchdog runs inside the agent process. Hermes needs it **outside** — cron-based, checking timestamps in agent run records.

| Hub Concept | Hermes Equivalent |
|---|---|
| `ping()` — resets timer on activity | Agent run record `started_at` timestamp |
| `setInterval(5000)` — periodic check | Cron job running `agy_watchdog.py` |
| `lastActivity` — last ping timestamp | `started_at` in JSON run record |
| `recoveryTimeoutSeconds` (60s) | `INACTIVITY_WARN_SECONDS` (180s) / `INACTIVITY_KILL_SECONDS` (300s) |
| `recoveryProtocol()` — callback | `attempt_recovery()` — SIGTERM → SIGKILL |
| Timer-based (inside agent) | Database-timestamp-based (outside agent) |

## Recovery Flow

```
Cron fires → find_running_records() → check_inactivity()
  → age > INACTIVITY_KILL_SECONDS →
    → failure_count < MAX_RECOVERY_RETRIES (3) →
      → attempt_recovery(): SIGTERM + wait + SIGKILL
      → update run record: failure_count++, state="failed"
    → failure_count >= MAX_RECOVERY_RETRIES →
      → create_escalation_nudge(): write /tmp/prismatic/nudge-fred
      → dispatcher picks it up → routes to Fred
```

## Constants

| Constant | Value | Purpose |
|---|---|---|
| `INACTIVITY_WARN_SECONDS` | 180s (3 min) | Log a warning if running this long |
| `INACTIVITY_KILL_SECONDS` | 300s (5 min) | Kill if running this long with no completion |
| `MAX_RECOVERY_RETRIES` | 3 | Max SIGTERM attempts before escalation |
| `RECOVERY_RECORD_TYPES` | `{"agy", "jules", "codex"}` | Agent types eligible for auto-recovery |

## Run Record Updates

When recovery is triggered, the agent's run record is updated:
- `state` → `"failed"`
- `failure_count` → incremented
- `metadata.recovery_attempts` → same as `failure_count`
- `metadata.last_recovery_attempt` → ISO 8601 timestamp
- `status_detail` → `"Recovery attempted (N/3)"`
- `completed_at` → set to now

## Escalation Nudge Format

When all 3 retries are exhausted, a SignalPayload JSON is written to `/tmp/prismatic/nudge-fred`:

```json
{
  "target": "fred",
  "action": "work",
  "issue_id": "GRO-XXX",
  "title": "[WATCHDOG ESCALATION] Task title — stalled 300s, 3 retries exhausted",
  "priority": 1,
  "signal_id": "watchdog-escalation-GRO-XXX-<timestamp>"
}
```

## File Locations

| File | Purpose |
|---|---|
| `ops/agy_watchdog.py` | Main watchdog script with v2 recovery |
| `agent-runs/*.json` | Run records checked for inactivity |
| `/tmp/prismatic/nudge-fred` | Escalation nudge (for dispatcher) |

## Porting Pattern (for future Hub→Hermes ports)

When porting Hub TS files to Hermes Python, the general pattern is:
1. Hub runs **inside** the agent loop (timer-based, sync, event-driven)
2. Hermes runs **outside** (cron-based, async, timestamp-comparison)
3. Database/file timestamps replace in-memory counters
4. Callbacks become nudge-files for the dispatcher
5. Retry limits and cooldowns are configurable constants, not hardcoded
