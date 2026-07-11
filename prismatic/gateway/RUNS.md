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
