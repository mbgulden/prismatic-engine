# prismatic-cron

Cron capability plugin for the Prismatic Engine — the first consumer of the
generic plugin lifecycle (`on_suspend` / `on_resume`).

It fires configured jobs on 5-field cron schedules with three handler kinds
(`script`, `prompt`, `webhook`) and records every execution as a JSON-line run
receipt with an idempotency key under
`$PRISMATIC_HOME/plugin-state/prismatic-cron/runs.jsonl`.

Scheduling is **infrastructure, not an agent tool**: the plugin registers no
agent tools and exposes job status only through its own API route. It never
edits `prismatic/gateway/server.py` or any other core file.

> **Disabled by default.** `auto_enable: false` in the manifest: the plugin
> registers on attach but starts DISABLED. An operator must explicitly enable
> it (`PluginLoader.enable("prismatic-cron")`) before any job can fire.

## Attach

Drop (or symlink) this directory on the plugin discovery path as `cron/`
(the directory name must match the first segment of `entry_point`:
`cron.plugin:CronPlugin`), then attach:

```
prismatic plugins attach ./plugins/cron --config ./cron-config.yaml
prismatic plugins enable prismatic-cron
```

`swarmcron>=0.3.0` is declared in `dependencies.pip` and installed at load
time. On enable, the loader validates the config against the manifest's
`config_schema`; `CronPlugin.on_init()` re-validates defensively and raises
`PluginValidationError` on any violation.

## Config syntax

```yaml
# cron-config.yaml
script_timeout_sec: 120   # optional; subprocess timeout for script handlers

jobs:
  - name: nightly-report
    schedule: "0 2 * * *"                       # 5-field cron, required
    handler:
      kind: script                              # script | prompt | webhook
      ref: "/usr/local/bin/nightly-report.sh"   # command string for script
    enabled: true                               # default true; disabled jobs never fire
    deliver:                                    # optional
      channel: email
      target: ops@example.com
    metadata:                                   # optional free-form
      team: ops
    policy:                                     # optional
      max_runs_per_day: 1

  - name: agent-standup-nudge
    schedule: "*/15 * * * *"
    handler:
      kind: prompt
      ref: "prompts/daily-standup.md"           # receipt marked prompt-dispatched;
                                                # the harness/agent layer picks it up —
                                                # the plugin never calls LLMs itself

  - name: ping-hook
    schedule: "0 * * * *"
    handler:
      kind: webhook
      ref: "https://example.com/hook"           # JSON payload POSTed on fire
    enabled: false
```

Handler semantics:

| kind     | behaviour |
|----------|-----------|
| `script` | Command validated with `swarmcron.security.validate_task_command`, environment sanitized with `swarmcron.security.sanitize_env`, run via subprocess with timeout; stdout/stderr captured, secret-redacted, tail stored on the receipt. |
| `prompt` | No execution. A receipt with status `prompt-dispatched` records the prompt ref for the harness/agent layer. |
| `webhook`| POSTs `{plugin, job, fired_at, idempotency_key}` JSON to the URL; 2xx → `ok`. |

Each receipt carries an `idempotency_key` — sha256 over the canonical
`{plugin, job, fired_at_minute, handler_kind, handler_ref}` (styled after
`prismatic/journal.py::signal_idempotency_key`). A restart that re-ticks the
same minute produces the same key and is deduped, never double-fired. The
`policy.max_runs_per_day` cap is enforced per UTC calendar day; over-cap fires
are recorded as `skipped` receipts with a reason.

## Operator API

Plugin-registered route (declared in `register_api_routes()`, no core edits):

- `GET /api/cron/jobs` → `[{name, schedule, enabled, next_fire_at, last_run: {fired_at, status}}]`

## State & lifecycle

- State root: `$PRISMATIC_HOME/plugin-state/prismatic-cron/`
  (`Path(os.environ.get("PRISMATIC_HOME") or Path.home())`).
  - `runs.jsonl` — append-only run receipts (source of truth).
  - `state.json` — suspend snapshot (`{"jobs": [...], "runs_tail": [...]}`),
    written belt-and-braces by `on_suspend()` alongside the loader's own
    persistence; re-read by `on_init()` if no suspend snapshot flows through.
  - `swarmcron-tasks.json` — `SwarmCronRegistry` store (reserved for future
    registry-backed scheduling).
- `on_suspend()` stops the ~30s tick thread and returns the JSON-serializable
  snapshot; `on_resume(state)` restores jobs + run history and restarts ticks.
- The tick thread is a daemon; a firing job never blocks more than one tick,
  and handler exceptions are captured as `failed` receipts, never raised.

## jobs.json compatibility (future migration path — explicitly NOT done here)

The job definition fields intentionally express everything the harness
profile's `cron/jobs.json` expresses — `name`, `schedule`, script/prompt
handler, `enabled`, `deliver` — so journal cron jobs **could** migrate onto
this plugin later. No migration is attempted: the engine owns deterministic
journal work and harnesses own cron today.

`CronPlugin.to_schedule_record(job)` documents the mapping each job would take
as a `prismatic.schedules.ScheduleRecord` (`owner="prismatic"`,
`schedule_type="cron"`), including `next_run_at`, `last_run`, and handler
metadata — the shape a future migration would write.

## Tests

```
PYTHONPATH=~/workspace/prismatic-engine-work/deps python3 -m pytest tests/test_plugin_cron.py -q
```

Covers: manifest parses; `config_schema` accepts a valid config and rejects a
bad cron expression; evaluator `next_fire`; the fire path writes a receipt
with the expected idempotency key (tick driven manually, no sleeps);
`on_suspend`/`on_resume` round-trip of jobs + history; disabled jobs never
fire; `max_runs_per_day` enforcement; prompt-dispatched receipts.
