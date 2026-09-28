# Gateway Runs API

The gateway `/runs` endpoints read from the shared run-history store used by
supervisor processes. By default that store is the SQLite database at
`~/.prismatic/runs.db`; set `PRISMATIC_RUNS_DB` to point the gateway and
writers at a different SQLite file, or `PRISMATIC_RUN_RECORDS_PATH` to use the
legacy JSON list format in tests.

Each run row is serialized with `run_id`, `issue_id`, `agent_name`, `status`,
`started_at`, `completed_at`, `output_path`, `error_message`, and
`duration_seconds`. `duration_seconds` is populated when both start and
completion timestamps are present.

Operational check:

```bash
python3 - <<'PY'
import sqlite3, os
p = os.path.expanduser(os.environ.get("PRISMATIC_RUNS_DB", "~/.prismatic/runs.db"))
with sqlite3.connect(p) as db:
    print(db.execute("SELECT COUNT(*), MAX(started_at) FROM runs").fetchone())
PY
```

If `/runs` looks stale, first verify the gateway and supervisor are using the
same database path.

## PWP KPI Dashboard Surface (GRO-4919, added 2026-09-13)

The `publish_kpi_tracker` capability's `build_dashboard()` writes its rendered
multi-site KPI pages into a "publish_root" that the PWP dashboard host serves
at `/pwp/kpi/` (see `site_builder.py` `dashboard_route` and the generated
`render_index` asset hrefs). The gateway now provides that serving layer:

- `GET /pwp/kpi/` → multi-site index (`index.html`)
- `GET /pwp/kpi/{filename}` → per-site pages (`<slug>.html`), `pwp-publish-kpi.css`,
  `dashboard_data.json`, and `<slug>.prior.json`. Leaf files only; path
  traversal and non-`.html/.json/.css` names are rejected.

Behavior notes:

- The dashboard is rendered into the stable PWP state dir
  (`$PRISMATIC_STATE_DIR/pwp/kpi-dashboard/`, default `~/.prismatic/pwp/kpi-dashboard/`)
  and re-rendered at most every 5 minutes (TTL), so metric snapshots update
  without a restart. Runtime values come from the per-site `<slug>.runtime.json`
  snapshots plus any live-mode adapters with credentials; missing values render
  as "—".
- The import path is `prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker`.
  The `/api/pwp/kpi/*` cluster previously imported `plugins.pwp...`, which did not
  resolve in the deployed gateway (no top-level `plugins` package); it was
  rewritten to the live path in the #376 rescue — do not reintroduce the old path.
