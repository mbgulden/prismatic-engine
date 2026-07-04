# GRO-3385 verification refresh — Tier-1 Silent Failure Watchdog

Date: 2026-07-04 15:59 UTC
Agent: Ned
Issue: GRO-3385
Cron job: `5c4980c8f395` (`Tier-1 Silent Failure Watchdog — every 6h, Telegram to Michael`)

## TL;DR

The original silent-failure condition is currently mitigated. The scheduled 06:00 MT / 12:00 UTC run completed successfully, wrote a cron artifact, sent Telegram, and recorded `last_status: ok` with no delivery error. The task reappeared because `agent:ned`, `dispatch:ready`, and `agent:needs-human-review` remained on an already-verified In Review issue after the abandonment guard fired.

## Live evidence

### jobs.json state

Profiles inspected: `fred`, `orchestrator`, `ned`.

Both `fred` and `orchestrator` contain matching job `5c4980c8f395`:

```json
{
  "enabled": true,
  "state": "scheduled",
  "last_run_at": "2026-07-04T06:00:41.338203-06:00",
  "last_status": "ok",
  "last_error": null,
  "last_delivery_error": null,
  "deliver": "local",
  "schedule": {"kind": "cron", "expr": "0 */6 * * *", "display": "0 */6 * * *"},
  "next_run_at": "2026-07-04T12:00:00-06:00"
}
```

`ned` has no matching job entry.

### Latest scheduled artifact

Latest artifact checked:

`/home/ubuntu/.hermes/profiles/fred/cron/output/5c4980c8f395/2026-07-04_06-00-41.md`

Relevant lines:

```text
Loaded 90 cron jobs from /home/ubuntu/.hermes/profiles
Current silent failures: 4
📄 Digest written to /tmp/tier1_silent_digest.md
✅ Telegram sent to chat 8190664947
Filing 2 Linear issues for new silent failures (with 7d dedup)...
State saved to /tmp/tier1_silent_failure_state.json
```

The orchestrator output path for the same job/run is present with the same artifact content and size.

### Live dry-run refresh

Command:

```bash
bash /home/ubuntu/.hermes/profiles/orchestrator/scripts/tier1_silent_failure_watchdog.sh --dry-run --no-linear
```

Result:

```text
═══ Tier-1 Silent Failure Watchdog — 2026-07-04 15:59:40 UTC ═══
Loaded 91 cron jobs from /home/ubuntu/.hermes/profiles
Current silent failures: 3
  🆕 New (not in previous state): 0
  ✅ Recovered since last run:   1
  🔁 Continuing:                3
📄 Digest written to /tmp/tier1_silent_digest.md
(dry-run — would send to Telegram + file Linear issues)
State saved to /tmp/tier1_silent_failure_state.json
EXIT:0
```

This proves the wrapper and Python watchdog still run cleanly with the safe `--dry-run --no-linear` arguments.

## Why this was re-dispatched

The issue was already In Review and had two prior verification comments:

- 2026-07-03: wrapper bug fixed in place and dry-run passed.
- 2026-07-04 02:02 UTC: scheduled run landed and cleared `jobs.json` to `last_status: ok`.

After that, the post-hoc abandonment guard added `agent:needs-human-review`, and the active routing labels remained. That made the dispatcher surface the issue again despite the cron being healthy.

## Disposition

No code change is required for the watchdog. The correct cleanup is Linear ledger hygiene:

- keep state as In Review;
- remove stale active labels `agent:ned`, `dispatch:ready`, and `agent:needs-human-review`;
- add `agent:peer-review` if available;
- post this verification evidence for human review.

## Files inspected

- `/home/ubuntu/.hermes/profiles/fred/cron/jobs.json`
- `/home/ubuntu/.hermes/profiles/orchestrator/cron/jobs.json`
- `/home/ubuntu/.hermes/profiles/ned/cron/jobs.json`
- `/home/ubuntu/.hermes/profiles/fred/cron/output/5c4980c8f395/2026-07-04_06-00-41.md`
- `/home/ubuntu/.hermes/profiles/orchestrator/cron/output/5c4980c8f395/2026-07-04_06-00-41.md`
- `/home/ubuntu/.hermes/profiles/orchestrator/scripts/tier1_silent_failure_watchdog.sh`
- `/home/ubuntu/.hermes/profiles/orchestrator/scripts/tier1_silent_failure_watchdog.py`
- `/tmp/gro3385_tier1_dryrun_20260704.txt`
