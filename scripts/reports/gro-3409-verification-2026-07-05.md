# GRO-3409 verification — gpt-oss-quota-headroom silent-failure

Verified at: 2026-07-05T12:24:22Z

## Issue

[GRO-3409](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3409) reported silent failure for orchestrator cron job `558b141146ed` (`gpt-oss-quota-headroom`) with root-cause family `unknown`.

## Findings

- The original job `558b141146ed` is now intentionally disabled in `/home/ubuntu/.hermes/profiles/orchestrator/cron/jobs.json`.
- Its `paused_reason` states it was auto-disabled by Golden Thread sync on 2026-06-30 16:10 UTC because `http://100.78.237.7:31435/api/quota` was unreachable.
- The old job's last output artifacts in `/home/ubuntu/.hermes/profiles/orchestrator/cron/output/558b141146ed/` all fail with the same endpoint-unreachable error, matching the paused reason.
- A successor quota job exists and is enabled: `47d47b744c4c` (`AGY Quota Poller — every 15min, writes to quota_state.db`) running `/home/ubuntu/.hermes/profiles/orchestrator/scripts/agy_quota_poller.sh` every 15 minutes.
- The successor job's ledger showed `last_status: ok`, `last_error: null`, and `last_delivery_error: null` before the manual verification run.

## Live verification run

Command:

```bash
bash /home/ubuntu/.hermes/profiles/orchestrator/scripts/agy_quota_poller.sh
```

Result: exit `0`.

Relevant output:

```text
=== Quota state updated @ 2026-07-05 12:24:28 UTC ===
Tier:    Antigravity (id=free-tier)
Paid:    Google AI Ultra (id=g1-ultra-tier)
Project: sonic-ocean-m4st2

Model                                       Remain  Exhausted               Resets
----------------------------------------------------------------------------------
claude-sonnet-4-6                            11.3%         no   15:40:21Z (+5955m)
gpt-oss-120b-medium                          11.3%         no   15:40:21Z (+5955m)
claude-opus-4-6-thinking                     11.3%         no   15:40:21Z (+5955m)
gemini-3.5-flash-extra-low                   98.2%         no    15:12:23Z (+167m)
```

## Disposition

No code change was required in Ned's lane. The obsolete endpoint-specific watcher is paused with an explicit reason, and the replacement AGY quota poller is enabled and live-verified. This issue should stay in `In Review`; stale active routing label `agent:ned` can be replaced with `agent:peer-review` so the scanner stops resurfacing it as actionable Ned work.
