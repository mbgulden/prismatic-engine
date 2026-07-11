# GRO-3406 verification — gpt-oss-quota-headroom silent-failure

Verified at: 2026-07-05T12:33:36Z

## Issue

[GRO-3406](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3406) reported silent failure for Fred-profile cron job `558b141146ed` (`gpt-oss-quota-headroom`) with root-cause family `unknown`.

## Findings

- The original Fred job `558b141146ed` is intentionally disabled in `/home/ubuntu/.hermes/profiles/fred/cron/jobs.json`.
- Its `paused_reason` states it was auto-disabled by Golden Thread sync on 2026-06-30 16:10 UTC because `http://100.78.237.7:31435/api/quota` was unreachable.
- The old Fred output artifacts in `/home/ubuntu/.hermes/profiles/fred/cron/output/558b141146ed/` all fail with the same endpoint-unreachable error, matching the paused reason.
- The same old job record/output is mirrored in the orchestrator profile; this is the same obsolete endpoint-specific watcher family already verified for [GRO-3409](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3409).
- A successor quota job exists and is enabled in both Fred and orchestrator ledgers: `47d47b744c4c` (`AGY Quota Poller — every 15min, writes to quota_state.db`) running `agy_quota_poller.sh` every 15 minutes.
- The successor job's latest scheduled artifacts exist under `/home/ubuntu/.hermes/profiles/fred/cron/output/47d47b744c4c/`, including `2026-07-05_06-30-41.md`, and show successful quota-state updates.
- The shared quota database `/home/ubuntu/.prismatic/quota_state.db` exists and contains quota snapshots after the live verification run.

## Live verification run

Command:

```bash
bash /home/ubuntu/.hermes/profiles/fred/scripts/agy_quota_poller.sh
```

Result: exit `0`.

Relevant output:

```text
=== Quota state updated @ 2026-07-05 12:33:06 UTC ===
Tier:    Antigravity (id=free-tier)
Paid:    Google AI Ultra (id=g1-ultra-tier)
Project: sonic-ocean-m4st2

Model                                       Remain  Exhausted               Resets
----------------------------------------------------------------------------------
claude-opus-4-6-thinking                     11.3%         no   15:40:21Z (+5947m)
claude-sonnet-4-6                            11.3%         no   15:40:21Z (+5947m)
gpt-oss-120b-medium                          11.3%         no   15:40:21Z (+5947m)
gemini-3-flash-agent                         98.2%         no    15:12:23Z (+159m)
```

## Disposition

No code change was required in Ned's lane. The obsolete endpoint-specific watcher is paused with an explicit reason, and the replacement AGY quota poller is enabled, scheduled, and live-verified. This issue should stay in `In Review`; stale active routing label `agent:ned` can be replaced with `agent:peer-review` so the scanner stops resurfacing it as actionable Ned work.
