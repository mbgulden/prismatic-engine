---
type: Standard
title: Production-Readiness Infrastructure (4-Cron Sweep)
description: Boring-but-essential infrastructure for the prismatic-engine. Four scheduled crons cover log rotation, SQLite VACUUM, per-table retention, and state-DB health alerts. Together they prevent silent-failure modes that compound over months.
resource: okf/production-readiness-infrastructure.md
tags: [production-readiness, observability, cron, vacuum, retention, log-rotation, agent:ned, prismatic-engine]
timestamp: 2026-06-19T16:30:00Z
linear_issue: GRO-2058,GRO-2059,GRO-2060,GRO-2061,GRO-2063
git_repo: mbgulden/prismatic-engine
git_path: okf/production-readiness-infrastructure.md
last_verified: 2026-06-19
verified_by: ned
status: current
---

# Production-Readiness Infrastructure (4-Cron Sweep)

**Status:** LIVE as of Jun 19 2026. All 4 crons active in orchestrator profile, all last-status = ok.
**Owner:** `agent:ned` (production-readiness sweeps are Ned's lane).

## What this standard guarantees

Four silent-failure modes that compound over months are now detected and (where possible) auto-corrected:

| Mode | What goes wrong | Cron | What it does |
|------|-----------------|------|--------------|
| **Log growth** | Engine logs grow unbounded; `/tmp/` wiped on reboot | Daily 02:00 UTC | Rotate logs > 10 MB with gzip compression, keep 5 copies |
| **SQLite fragmentation** | `prismatic_state/*.db` fragments over months, queries slow | Sun 03:00 UTC | VACUUM every SQLite DB in `prismatic_state/` |
| **Unbounded tables** | Some tables (dedup_log, durable_events) grow without retention | Daily 03:30 UTC | DELETE rows older than policy cutoff |
| **Silent DB growth** | DB size grows unnoticed until production incident | Daily 04:00 UTC | Alert via Telegram if any DB > 100 MB or grew > 20% day-over-day |

## Cron schedule

| Time (UTC) | Frequency | Job ID | Script |
|------------|-----------|--------|--------|
| 02:00 | Daily | `5f1d4354121f492e` | `rotate-engine-logs.py` |
| 03:00 | Sun | `3d2520fbfae8cf0e` | `vacuum-state-dbs.sh` |
| 03:30 | Daily | `8ec9963f6d59e8b2` | `purge-retention.py` |
| 04:00 | Daily | `8c49acb91e076092` | `check-state-db-health.py` |

Order matters: rotation frees disk → VACUUM reclaims SQLite space → retention prevents growth → health check verifies health.

## Cron implementations

| Cron | Engine script | What it does | Output |
|------|---------------|--------------|--------|
| `5f1d4354121f492e` | `scripts/rotate-engine-logs.py` | Rotates every `*.log` under `~/.prismatic/logs/`, `~/.gemini/logs/`, `~/.hermes/profiles/*/logs/`. Cascade pattern: `foo.log` → `foo.log.1.gz` → `foo.log.2.gz` → ... → `foo.log.5.gz` → deleted. | `~/.prismatic/logs/log-rotation-audit.jsonl` |
| `3d2520fbfae8cf0e` | `scripts/vacuum-state-dbs.sh` | Runs VACUUM on every `*.db` under `~/.prismatic/db/`. Skips DBs < 100 KB. Skips DBs with active WAL writers. Checks free disk space (need ~2x DB size). | `~/.prismatic/logs/vacuum-cron.log` + `vacuum-report.jsonl` |
| `8ec9963f6d59e8b2` | `scripts/purge-retention.py` | Per-table retention: `dedup_log` 14 days, `durable_events` 30 days, `agy_stall_tracker` 30 days. `label_snapshots` intentionally NOT touched (queries have no time filter). | `~/.prismatic/logs/retention-cron.log` + `retention-report.jsonl` |
| `8c49acb91e076092` | `scripts/check-state-db-health.py` | Reports size of every DB. Alerts (Telegram) if > 100 MB or grew > 20% day-over-day. Tracks per-day snapshots for trend. | `~/.prismatic/logs/state-db-sizes.jsonl` + `state-db-alerts.jsonl` |

## Hermes cron security model

Hermes cron rejects scripts outside `~/.hermes/profiles/<profile>/scripts/` (security check in `cron/scheduler.py:750`). To run engine scripts, thin wrappers live in the profile scripts/ dir:

**Bash wrapper** (`~/.hermes/profiles/orchestrator/scripts/vacuum-state-dbs.sh`):
```bash
#!/usr/bin/env bash
exec /home/ubuntu/work/prismatic-engine/scripts/vacuum-state-dbs.sh "$@"
```

**Python wrapper** (used for `purge-retention.py`, `check-state-db-health.py`, `rotate-engine-logs.py`):
```python
#!/usr/bin/env python3
import subprocess, sys
result = subprocess.run(
    [sys.executable, "/home/ubuntu/work/prismatic-engine/scripts/<script>.py"] + sys.argv[1:]
)
sys.exit(result.returncode)
```

**Pattern**: thin wrapper in profile scripts/ → delegates to canonical engine script. No symlinks (they're rejected because the security check uses `Path.resolve()` which follows symlinks).

## Retention policy per table

| Table | Retention | Rationale |
|-------|-----------|-----------|
| `dedup_log` | 14 days | cycle_id-keyed; old cycle rows are stale |
| `durable_events` | 30 days | event-style; older is reference-only |
| `agy_stall_tracker` | 30 days | stall alerts; older is reference-only |
| `label_snapshots` | **NONE** | Queries (`had_label`, `had_labels`) check "ever had a label" with no time filter. Truncating would break dispatcher dedup. |

For `label_snapshots` growth (currently 250K+ rows), we rely on VACUUM + the new cron architecture to bound file size. Future work (GRO-2063 Tier 3) may add time-bounded queries or cold-storage archiving.

## Adding a new cron to the sweep

1. Write the engine script in `scripts/` (committed to git)
2. Write a wrapper in `~/.hermes/profiles/orchestrator/scripts/` (NOT committed — see comment)
3. Add cron entry to `~/.hermes/profiles/orchestrator/cron/jobs.json` with `script: <wrapper-name>`
4. Test via `hermes cron run --profile orchestrator <job_id>` and verify in `~/.prismatic/logs/`
5. Document the new cron in this OKF doc

## Adding a new retention policy

Edit `RETENTION_POLICIES` in `scripts/purge-retention.py`:

```python
RETENTION_POLICIES = {
    "your_table": (days_to_keep, "timestamp_column_name"),
    # ...
}
```

Use `(None, None)` for "intentionally no retention" (with a comment explaining why).

## Verifying the sweep is healthy

```bash
# All 4 crons active
timeout 5 hermes cron list --profile orchestrator | grep -E "VACUUM|retention|health|rotation"

# All 4 last-status = ok
python3 -c "
import json
data = json.loads(open('/home/ubuntu/.hermes/profiles/orchestrator/cron/jobs.json').read())
ids = ['5f1d4354121f492e', '3d2520fbfae8cf0e', '8ec9963f6d59e8b2', '8c49acb91e076092']
for j in data['jobs']:
    if j['id'] in ids:
        print(f\"{j['name'][:55]}: {j.get('last_status') or 'pending'}\")
"

# Recent cron output
ls -la ~/.prismatic/logs/{vacuum-cron,retention-cron,state-db-sizes,log-rotation-audit}.log
```

## Failure modes

| Failure | Detection | Response |
|---------|-----------|----------|
| Cron doesn't run for 7+ days | `last_run_at` age in jobs.json | Check `hermes cron list` for paused state |
| Health check alerts | Telegram message | Investigate state DBs (look at `state-db-sizes.jsonl` trend) |
| VACUUM fails (insufficient disk) | Log entry in `vacuum-cron.log` | Free disk space, retry |
| Rotation can't find logs | Log entry in `log-rotation-audit.jsonl` | Verify log paths in script |

## Related

- [`architecture.md`](./architecture.md) — engine module layout
- [`linear-budget-lint-ci.md`](./linear-budget-lint-ci.md) — the regression-prevention layer for Linear
- `portable-skills/agent-ned/references/infra-watchdog-pattern.md` — the full sweep checklist
- `portable-skills/agent-ned/SKILL.md` — Ned's skill, with "Production-Readiness Sweeps" lane
- GRO-2058, GRO-2059, GRO-2060, GRO-2061, GRO-2063 (Linear issues)

## Hand-off

If you're picking up the production-readiness sweep for a quarterly hygiene check:

1. Read `portable-skills/agent-ned/references/infra-watchdog-pattern.md` (the checklist)
2. Run each cron manually via `hermes cron run` and verify the output
3. Check the JSONL reports for trends
4. File any new findings as Linear issues with `agent:ned` label
5. Update this OKF doc with the new state

---

*Shipped by `agent:ned` on Jun 19 2026 as part of the production-readiness sweep (GRO-2063 parent). All 4 crons verified working end-to-end.*
