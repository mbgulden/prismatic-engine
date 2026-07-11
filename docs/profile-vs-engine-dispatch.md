# Profile Dispatcher vs Engine Dispatcher — Coexistence

Two dispatchers operate on the same Linear queue. This doc explains which runs when, what each is responsible for, and how they coexist.

## Quick reference

| Concern | Profile dispatcher (`agent_dispatcher.py`) | Engine dispatcher (`prismatic/dispatcher.py`) |
|---|---|---|
| Home | `~/.hermes/profiles/orchestrator/scripts/` | `prismatic-engine/prismatic/` |
| Triggers | Hermes cron `e2f1a3b4c5d6` (every 5 min) | Future: shadow mode via wrapper |
| Routing model | `next_label` chain (Worker → AGY → Fred → Done) | Pipeline templates (decompose → review → approve) |
| Bypass detection | **Yes** (GRO-2024) | No (single-system trust) |
| AGY wrapper integration | Yes (`launch_agy_with_artifact.py`) | No (uses agy-bin directly) |
| Telemetry | Event emitter to SSE feed | `prismatic/telemetry` collector |
| AGY stall recovery | `recover_stalled_agy()` (escalates to Fred after 3 retries) | Built-in stall tracker |
| State DB | `~/.hermes/profiles/orchestrator/state/event-router/router.db` | `./prismatic_state/event_router.db` |

## Which one runs first

Today (Jun 19 2026): **Profile dispatcher runs as the primary.** It's the one wired to the cron. Engine dispatcher is only invoked manually via the wrapper.

Future (Tier 2): Both run. Profile primary, engine shadow. Wrapper at `bin/prismatic-dispatcher-wrapper.sh` sources the orchestrator `.env`, sets `PRISMATIC_TEAM_ID`, and `exec`s `python3 -m prismatic.dispatcher serve --once`.

## Why both systems

- **Profile dispatcher** encodes the GRO-2024 review loop: Worker → AGY peer review → Fred verification → Done. The `next_label` chain enforces this and the bypass-detection catches any attempt to skip Fred.
- **Engine dispatcher** is the durable home for any future state-machine routing. Pipeline templates can express complex orchestrations (decompose → review → apply → publish) that don't fit the simple `next_label` model.

The profile dispatcher's bypass detection (line 2134 of `agent_dispatcher.py`) is the **enforcement point** for the review loop. Engine dispatcher doesn't have this; it relies on the pipeline template being correct. So the profile dispatcher remains the security-critical path.

## Migration strategy

- **Now (Tier 2):** Wrap engine dispatcher as a shadow. Both run, profile is authoritative.
- **Later (Tier 3+):** Port the bypass-detection logic into the engine dispatcher. When the engine version is verified, the profile dispatcher becomes a thin shim.
- **Eventually (Tier 4):** Profile dispatcher can be archived. Single source of truth lives in the engine repo.

## How to invoke

```bash
# Profile dispatcher (current primary)
python3 /home/ubuntu/.hermes/profiles/orchestrator/scripts/agent_dispatcher.py

# Engine dispatcher via wrapper (Tier 2 shadow)
/home/ubuntu/work/prismatic-engine/bin/prismatic-dispatcher-wrapper.sh --once

# Engine dispatcher directly
cd /home/ubuntu/work/prismatic-engine
PRISMATIC_TEAM_ID=b6fb2651-... LINEAR_API_KEY=... python3 -m prismatic.dispatcher serve --once
```

## Refs

- Tier 2: GRO-2030 (this work)
- Tier 1: GRO-2008/2010/2020 (LinearBudget codification + engine module move)
- Tier 1c enforcement: GRO-2024 (review loop codification, bypass detection)
- Engine-vs-harness: `skills/orchestration/prismatic-engine-operations/SKILL.md`
- Single source of truth for loop: `skills/orchestration/orchestrator-delegation-discipline/references/review-loop-canonical-codification.md`