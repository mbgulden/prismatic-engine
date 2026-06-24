# Prismatic Engine — Native Fleet Watchdog

**Date:** 2026-06-24
**Status:** Active (deployed via systemd)
**Author:** Fred (per Michael's request)
**Refs:** GRO-2397 (Hermes-side v3), GRO-2391 (drain cron), GRO-XXXX (this issue)

## Why this exists

GRO-2397 shipped a Hermes-cron-driven `fleet_watchdog.py` that auto-recovers from
fleet issues. But it depends on Hermes + the agentic-swarm-ops repo. **Prismatic
Engine should be standalone** — per the PWP standalone-first principle:

> Prismatic Engine should be able to do everything as a standalone app. If
> Hermes is uninstalled, Prismatic Engine doesn't lose any capabilities. It
> either merges or is additive.

So the watchdog detection+actions layer needs to live IN the engine, callable
without any orchestrator.

## What shipped

### New files (in prismatic-engine/)

| File | Lines | Purpose |
|---|---|---|
| `prismatic/fleet_watchdog.py` | 270 | Detection + report renderer (no Hermes deps) |
| `prismatic/fleet_actions.py` | 320 | Engine-aware recovery handlers |
| `tests/test_fleet_watchdog.py` | 180 | 16 tests, all pass |
| `scripts/prismatic-fleet-watchdog.service` | — | systemd oneshot |
| `scripts/prismatic-fleet-watchdog.timer` | — | 5min cadence |

### CLI integration

```
prismatic fleet-watchdog             # run with auto-actions
prismatic fleet-watchdog --dry-run   # report only
prismatic fleet-watchdog --json      # machine-readable
```

### Hermes cron paused

`500749c7949d` (Hermes-side fleet_watchdog) is **paused**. The engine's native
version takes over. The Hermes version can be re-enabled if needed for the
cross-fleet view (AGY + GPU + OAuth), but for engine-only health the native
path is cleaner.

## Detection layer — what PE knows natively

| Check | Source | Threshold |
|---|---|---|
| `prismatic-gateway.service` active | `systemctl is-active` | always |
| `prismatic-webhook-drain.timer` active | `systemctl is-active` | always |
| Gateway `/health` returns 200 | `urllib` localhost:9000 | always |
| Webhook queue pending count | `linear_webhook_queue.db` | ≥500 |
| State DB sizes | `prismatic_state/*.db` glob | ≥100MB |
| Stale lock entries | `~/.antigravity/swarm_locks.json` | >24h no heartbeat |
| Engine log sizes | `~/.prismatic/logs/*.log` glob | ≥10MB |

## Auto-actions (idempotent, no Hermes)

| Alert | Action | Side effect |
|---|---|---|
| gateway service down | `action_restart_gateway` | `systemctl start` + verify |
| drain timer down | `action_restart_drain_timer` | `systemctl start` |
| Webhook queue >500 | `action_drain_webhook_queue` | trigger drain service |
| State DB ≥100MB | `action_vacuum_state_dbs` | SQLite `VACUUM` |
| Stale locks | `action_clear_stale_locks` | evict from JSON registry |
| Log files ≥10MB | `action_rotate_logs` | unlink (assumes .gz backup or OK to lose tail) |
| Gateway /health non-200 | `action_probe_gateway_health` | diagnostic probe (read-only) |

## Out of scope (intentionally)

- **AGY processes** → agentic-swarm-ops/agy_watchdog owns this
- **OAuth tokens** → hermes/linear_oauth_refresh.sh
- **GPU cluster** → agentic-swarm-ops/agy_watchdog owns failover

The Hermes-side `fleet_watchdog.py` covers those. The engine's native one
covers **engine-owned** health. **Two complementary watches, no overlap.**

## Verification (live)

1. **Tests**: `pytest tests/test_fleet_watchdog.py` → **16/16 pass**
2. **Full suite**: `pytest tests/` → 281 passed (was 265), no regressions
3. **CLI**: `python3 -m prismatic.fleet_watchdog` → silent on green (exit 0)
4. **Triggered alert**: stopped `prismatic-webhook-drain.timer` → watchdog
   detected → auto-restarted → log confirms action taken
5. **systemd timer**: `prismatic-fleet-watchdog.timer` active, runs every 5min

## Architectural rule going forward

**Before adding any new fleet-watchdog detection**, ask:
- "Is this engine-owned?" → add it to `prismatic/fleet_watchdog.py`
- "Is this cross-fleet (Hermes domain)?" → add it to `agentic-swarm-ops/ops/fleet_watchdog.py`
- "Both?" → add to both, with different thresholds tuned to each domain

## Backwards compatibility

`agy_watchdog.py` and the Hermes-side `fleet_watchdog.py` are unchanged.
The Hermes cron `500749c7949d` is paused but not deleted — re-enable it
anytime via `hermes cron resume 500749c7949d`.

The engine's `prismatic-gateway.service`, `prismatic-webhook-drain.service`,
and `prismatic-fleet-watchdog.service` are now the canonical systemd-managed
services for the engine. The watchdog keeps them all healthy.

## What Michael sees

When green: nothing (silent).
When alert: a Telegram/console message with:
```
🛰️ Prismatic Engine Fleet Watchdog — <timestamp>
Status: 🔴 red
Alerts: N (healthy checks: M)

🔴 prismatic-X.service not active
   → ✅ action: action_restart_X
     Started prismatic-X.service

Healthy checks:
  🟢 Webhook queue 22 pending
  🟢 All state DBs within size
  ...
```

The "→ ✅ action: ..." line is the key change. You see **what the system did**
before you even read the alert.