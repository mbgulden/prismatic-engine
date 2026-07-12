# GRO-3405 verification — Hermes daily journal snapshot

## TL;DR

`Hermes daily journal snapshot` (`ce3dd849ede5`, profile `fred`) is not currently silent-failing. The detector alert was stale by the time this run inspected the live scheduler: the job is enabled, scheduled every 60 minutes, last ran successfully on 2026-07-05 at 05:11:06 -06:00, and has ten recent per-run output artifacts.

## Live evidence — 2026-07-05

### Cron ledger

Source: `/home/ubuntu/.hermes/profiles/fred/cron/jobs.json`

```json
{
  "id": "ce3dd849ede5",
  "name": "Hermes daily journal snapshot",
  "enabled": true,
  "state": "scheduled",
  "script": "journal_snapshot.py",
  "last_run_at": "2026-07-05T05:11:06.977174-06:00",
  "last_status": "ok",
  "last_error": null,
  "last_delivery_error": null,
  "schedule": {"kind": "interval", "minutes": 60, "display": "every 60m"},
  "deliver": "local",
  "next_run_at": "2026-07-05T06:11:06.977174-06:00"
}
```

At inspection time the last successful scheduled run was 35.6 minutes old, below the 90-minute silent-failure threshold for a 60-minute interval job.

### Per-job delivery/output logs

Source: `/home/ubuntu/.hermes/profiles/fred/cron/output/ce3dd849ede5/`

Ten recent artifacts exist. The latest scheduled artifact is:

```text
/home/ubuntu/.hermes/profiles/fred/cron/output/ce3dd849ede5/2026-07-05_05-11-06.md
# Cron Job: Hermes daily journal snapshot
Job ID: ce3dd849ede5
Mode: no_agent (script)
{
  "changed": true,
  "signals": 772,
  "today_file": "/home/ubuntu/work/Hermes-Research/journals/inbox/2026-07-05.md",
  "lines": 40
}
```

The nine prior artifacts between 2026-07-04 20:27 and 2026-07-05 04:27 also completed and wrote valid JSON (`changed: false`, `signals: 0`) rather than crashing or going missing.

### Live smoke run

Command:

```bash
timeout 90 python3 /home/ubuntu/.hermes/profiles/fred/scripts/journal_snapshot.py
```

Output:

```json
{
  "changed": true,
  "signals": 789,
  "today_file": "/home/ubuntu/work/Hermes-Research/journals/inbox/2026-07-05.md",
  "lines": 40
}
```

Exit code: 0.

### Script and log inspection

`/home/ubuntu/.hermes/profiles/fred/scripts/journal_snapshot.py` is a thin wrapper that uses an absolute binary path:

```python
BIN = "/home/ubuntu/.local/bin/prismatic-journal-snapshot"
os.environ["PATH"] = "/home/ubuntu/.local/bin:" + os.environ.get("PATH", "")
os.execvp(BIN, [BIN, *sys.argv[1:]])
```

This covers the historical cron PATH failure mode. Recent Fred profile logs contained no journal/snapshot errors in the last sampled windows; the only recent `errors.log` entries were unrelated `tools.vision_tools` image-analysis errors.

## Why the original alert fired

The Linear issue description captured a stale detector snapshot with last run `2026-07-04T07:33:46.504663-06:00`. Since then, the scheduler ledger and output directory show repeated successful runs through `2026-07-05T05:11:06.977174-06:00`. This is a stale silent-cron ticket, not an active cron break.

## Remaining concerns

| Concern | Owner | Notes |
|---|---|---|
| Duplicate silent-cron filings for the same journal snapshot family | Ned / dispatcher cleanup | Adjacent GRO-3393/GRO-3396/GRO-3399/GRO-3408 work already addressed journal snapshot failure modes; this ticket should move to review rather than trigger another code fix. |
| `git_dirty` events can still record `fatal: not a git repository` in journal indexes | Follow-up hygiene, not GRO-3405 blocker | The snapshot command exits 0 and writes valid journal output despite that noisy event. |

## Files inspected

- `/tmp/issue-batches/GRO-3405.txt`
- `/home/ubuntu/.hermes/profiles/fred/cron/jobs.json`
- `/home/ubuntu/.hermes/profiles/fred/cron/output/ce3dd849ede5/*.md`
- `/home/ubuntu/.hermes/profiles/fred/scripts/journal_snapshot.py`
- `/home/ubuntu/.hermes/profiles/fred/logs/errors.log`
- `/home/ubuntu/.hermes/profiles/fred/logs/agent.log`
- `/home/ubuntu/work/Hermes-Research/journals/inbox/2026-07-05.md`
- `/home/ubuntu/work/Hermes-Research/journals/.index/events-2026-07-05.json`

## Recommendation

Move GRO-3405 to In Review with this verification report. No production code change is required for this ticket; the current cron job is firing and producing output.
