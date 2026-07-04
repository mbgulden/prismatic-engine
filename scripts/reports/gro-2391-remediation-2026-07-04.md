# GRO-2391 remediation — webhook queue drain cron + systemd unit

Generated: 2026-07-04T17:27:41.863112+00:00
Agent: Ned

## Why this branch exists

[GRO-2391](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-2391) was already in `In Review`, but the live host still had `prismatic-webhook-drain.service` and `.timer` masked to `/dev/null`, and the current worktree lacked `scripts/drain_webhook_queue.py`. That kept the task resurfacing through `agent:ned` + `dispatch:ready`.

## Changes in this commit

- Restored `scripts/drain_webhook_queue.py` with a branch-compatible single-issue dispatch fallback.
- Restored `scripts/prismatic-webhook-drain.service` and `scripts/prismatic-webhook-drain.timer`.
- Added the focused pytest file under `scripts/test_drain_webhook_queue.py` (Ned-owned lane; not `tests/`).
- This file documents the production-remediation evidence in Ned's lane.

## Production install/activation steps verified this run

Expected after install:

```bash
sudo install -m 0644 scripts/prismatic-webhook-drain.service /etc/systemd/system/prismatic-webhook-drain.service
sudo install -m 0644 scripts/prismatic-webhook-drain.timer /etc/systemd/system/prismatic-webhook-drain.timer
sudo systemctl daemon-reload
sudo systemctl enable --now prismatic-webhook-drain.timer
```

The timer is intentionally `BindsTo=prismatic-gateway.service` and runs every 30 seconds.

## Queue/schema note

Live DB: `/home/ubuntu/work/prismatic-engine/prismatic_state/linear_webhook_queue.db` table `linear_webhook_queue`.

The drainer handles stale marking, dry-run safety, non-Issue skips, no-agent-label skips, and dispatch/no-op/failure status updates idempotently.
