# GRO-2312 current cron verification — Ned profile scheduler

Date: 2026-07-04
Agent: Ned
Issue: GRO-2312

## Verdict

GRO-2312 is already resolved. The original 2026-06-23 failure hypothesis is stale: Ned profile cron jobs are firing now, and the documented workaround branch already captured the producer/consumer dispatcher pattern.

Current state has two healthy schedulers relevant to this issue:

1. **Ned profile-local scheduler** is active through `hermes --profile ned gateway run`.
2. **Orchestrator dispatcher mirror** (`48876764f897`) is also active and continues to feed Ned task specs.

No code fix is required on this pass. The remaining problem is stale Linear routing labels (`agent:ned`, `dispatch:ready`) keeping an already-finalized In Review issue in Ned's queue.

## Fresh evidence

### Ned autonomous task loop (`a9374c15f022`)

From `/home/ubuntu/.hermes/profiles/ned/cron/jobs.json` on 2026-07-04:

- `enabled: true`
- `state: scheduled`
- `last_run_at: 2026-07-04T17:36:40.912759+00:00`
- `last_status: ok`
- `next_run_at: 2026-07-04T17:41:40.912759+00:00`
- `script: prismatic/lanes/ned/scan_tasks.py`

Recent output files in `/home/ubuntu/.hermes/profiles/ned/cron/output/a9374c15f022/`:

- `2026-07-04_17-36-40.md`
- `2026-07-04_17-17-49.md`
- `2026-07-04_17-08-06.md`
- `2026-07-04_17-00-04.md`
- `2026-07-04_16-49-45.md`

The directory currently contains 50 retained output files for 2026-07-04, with the latest run at `17:36:40`.

Agent log evidence from `/home/ubuntu/.hermes/profiles/ned/logs/agent.log`:

- `2026-07-04 17:00:04,417 INFO cron.scheduler: Job 'a9374c15f022': agent returned [SILENT] — skipping delivery`
- `2026-07-04 17:17:49,021 INFO cron.scheduler: Job 'a9374c15f022': agent returned [SILENT] — skipping delivery`
- Multiple `Job 'a9374c15f022': loaded credential pool...` entries between 17:05 and 17:23 UTC.

### Orchestrator mirror (`48876764f897`)

From `/home/ubuntu/.hermes/profiles/orchestrator/cron/jobs.json` on 2026-07-04:

- `name: Ned Delta Dispatcher — replaces 6 Ned LLM crons`
- `enabled: true`
- `state: scheduled`
- `last_run_at: 2026-07-04T11:31:22.184238-06:00`
- `last_status: ok`
- `next_run_at: 2026-07-04T11:46:22.184238-06:00`
- `script: ned_delta_dispatcher.py`

Agent log evidence from `/home/ubuntu/.hermes/profiles/orchestrator/logs/agent.log`:

- `2026-07-04 15:00:10,230 INFO cron.scheduler: Job '48876764f897': agent returned [SILENT] — skipping delivery`
- `2026-07-04 17:16:19,821 INFO cron.scheduler: Job '48876764f897': agent returned [SILENT] — skipping delivery`
- `2026-07-04 17:31:22,182 INFO cron.scheduler: Job '48876764f897': agent returned [SILENT] — skipping delivery`

### Existing documentation artifact

The requested OKF documentation already exists on the prior pushed branch `origin/ned/GRO-2312-cron-pattern`:

- `okf/integrations/autonomous-task-loop-pattern.md`
- Commit: `7632db2 [Ned] Document autonomous task loop pattern (GRO-2312): cron fires via mirrored orchestrator job, dispatcher fix was the real cure`

Current checkout of `growthwebdev-knowledge` does not have that branch content in the worktree, but the pushed branch contains the document and the prior Linear comment recorded it as the deliverable.

## Acceptance criteria status

| Criterion | Status | Evidence |
|---|---|---|
| Cron job `a9374c15f022` fires on schedule | ✅ Met | `last_run_at` is 2026-07-04 17:36 UTC, status `ok`; output files are current. |
| Verify in agent.log: `Job 'a9374c15f022'` appears within schedule window | ✅ Met | Ned `agent.log` contains current `cron.scheduler` entries for `a9374c15f022`. |
| No regression: orchestrator jobs still fire | ✅ Met | Orchestrator job `48876764f897` last ran successfully at 17:31 UTC. |
| Document chosen approach | ✅ Met | Prior pushed branch `origin/ned/GRO-2312-cron-pattern` has the OKF doc; this pass adds an in-lane current verification note. |

## Operational conclusion

This is not an active infrastructure outage. Treat GRO-2312 as stale routing residue: preserve `In Review`, remove Ned dispatch labels, and hand to peer review/closure.
