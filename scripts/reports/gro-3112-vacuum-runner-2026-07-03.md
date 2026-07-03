# GRO-3112 — Unified SQLite vacuum runner verification (2026-07-03)

## Scope

Built a unified Prismatic Engine SQLite state database maintenance runner for [GRO-3112](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3112).

## Files changed

- `scripts/ops/vacuum_state_dbs.py` — discovers `.db` files under configured roots, runs `PRAGMA integrity_check`, commits before `VACUUM`, then runs `VACUUM` and `ANALYZE` per DB.
- `scripts/ops/vacuum-state-dbs.sh` — stable shell entrypoint for operators/systemd.
- `scripts/ops/systemd/prismatic-vacuum-state-dbs.service` — oneshot service template.
- `scripts/ops/systemd/prismatic-vacuum-state-dbs.timer` — weekly timer template (`Sun 03:20`, persistent, randomized delay).
- `scripts/ops/test_vacuum_state_dbs.py` — unit coverage for root parsing, DB discovery, dry-run behavior, maintenance success, and per-DB failure isolation.
- This report documents operator usage and verification in Ned's writable `scripts/` lane.

## Verification run

```text
$ python3 -m pytest scripts/ops/test_vacuum_state_dbs.py -q
......                                                                   [100%]
6 passed in 0.10s
```

```text
$ scripts/ops/vacuum-state-dbs.sh --dry-run
Prismatic SQLite vacuum runner
roots=$HOME/.prismatic/db,$HOME/work/prismatic-engine/prismatic_state
OK $HOME/.prismatic/db/event_router.db before=42479616 after=42479616 delta=0 dry-run: would run integrity_check, VACUUM, ANALYZE
OK $HOME/work/prismatic-engine/prismatic_state/curator_metrics.db before=12288 after=12288 delta=0 dry-run: would run integrity_check, VACUUM, ANALYZE
OK $HOME/work/prismatic-engine/prismatic_state/dedup.db before=20480 after=20480 delta=0 dry-run: would run integrity_check, VACUUM, ANALYZE
OK $HOME/work/prismatic-engine/prismatic_state/event_bus.db before=40960 after=40960 delta=0 dry-run: would run integrity_check, VACUUM, ANALYZE
OK $HOME/work/prismatic-engine/prismatic_state/event_router.db before=4358144 after=4358144 delta=0 dry-run: would run integrity_check, VACUUM, ANALYZE
OK $HOME/work/prismatic-engine/prismatic_state/linear_budget.db before=2084864 after=2084864 delta=0 dry-run: would run integrity_check, VACUUM, ANALYZE
OK $HOME/work/prismatic-engine/prismatic_state/linear_webhook_queue.db before=3354624 after=3354624 delta=0 dry-run: would run integrity_check, VACUUM, ANALYZE
OK $HOME/work/prismatic-engine/prismatic_state/plugin_lifecycle.db before=12288 after=12288 delta=0 dry-run: would run integrity_check, VACUUM, ANALYZE
summary total=8 ok=8 failed=0
```

## Notes

The runner intentionally does not install or enable the systemd timer from this cron run. It ships the service/timer templates and operator runbook; activation remains an explicit deployment step.
