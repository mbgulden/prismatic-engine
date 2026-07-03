# GRO-2438 verification — AGY Sandbox Supervisor cron recovery

Verified by: Ned  
Timestamp: 2026-07-03T05:47:04.469388+00:00  
Linear: https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-2438

## Verdict

✅ The silent-failure condition described in GRO-2438 is no longer present.

The original issue was filed when job `faf8d91da716` (`AGY Sandbox Supervisor — event-driven organic scaling`) had not run since `2026-06-25T05:29:01.675268-06:00`.

Live inspection on 2026-07-03 shows the same job is enabled, running every 5 minutes, and producing cron output artifacts.

## Evidence collected

Source inspected:

- `/home/ubuntu/.hermes/profiles/fred/cron/jobs.json`
- `/home/ubuntu/.hermes/profiles/fred/cron/output/faf8d91da716/`
- Mirrored orchestrator cron record at `/home/ubuntu/.hermes/profiles/orchestrator/cron/jobs.json`

Observed job fields:

```json
{
  "id": "faf8d91da716",
  "name": "AGY Sandbox Supervisor — event-driven organic scaling",
  "enabled": true,
  "schedule": {"kind": "interval", "minutes": 5, "display": "every 5m"},
  "deliver": "local",
  "last_run_at": "2026-07-02T23:44:46.968604-06:00",
  "last_status": "ok",
  "last_error": null,
  "last_delivery_error": null,
  "script": "agy_sandbox_event_supervisor_cron.sh",
  "no_agent": true
}
```

Latest output artifacts present (50 retained files); most recent inspected:

- `/home/ubuntu/.hermes/profiles/fred/cron/output/faf8d91da716/2026-07-02_23-44-46.md`
- size: 556 bytes
- mode: `no_agent (script)`
- output included supervisor startup lines and a clean guard exit because another supervisor already held `/home/ubuntu/.prismatic/run/supervisor.lock`.

Relevant latest output excerpt:

```text
[supervisor-cwd] using /archive/agy_sandboxes/GRO-3201/ (has prismatic.curator.issue_to_task + gateway)
[agx-stdin-fix] AGY_SUPERVISOR_NO_STDIN=1 (prevents pipe_read hang)
cutoff=2026-07-02T18:44:44.276566Z stale_hours=11.0 apply=True
candidates=0
[sandbox-root] fast-ssd primary: /archive/agy_sandboxes
  [lock] another supervisor holds /home/ubuntu/.prismatic/run/supervisor.lock — exiting
```

## Interpretation

This is not a current silent cron failure. The job is observable, has a fresh `last_run_at`, `last_status=ok`, no delivery error, and per-run markdown logs.

The lock-exit line is expected non-overlap behavior, not a crash: the supervisor found an active supervisor lock and exited rather than overlapping.

## Remaining gap

None for GRO-2438. If Michael wants to improve this further, the follow-up would be observability polish: emit an explicit heartbeat row/event for "skipped due active supervisor lock" so watchdogs can distinguish no-op lock exits from real failures without reading the markdown artifact.
