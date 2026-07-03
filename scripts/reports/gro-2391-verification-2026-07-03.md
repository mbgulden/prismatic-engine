# GRO-2391 verification — webhook queue drain cron + systemd unit

Generated: 2026-07-03T13:18:40.461844+00:00
Agent: Ned

## Linear issue

[GRO-2391](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-2391) — `[INFRA] Webhook queue drain cron + systemd unit (production-grade)`

## Evidence rerun today

- Implementation commit exists in git history: `4ab6a1fc [Fred] Webhook queue drainer + systemd unit + tests (#GRO-2391)`.
- Files present on branch `feature/tier-5a-okf-pilot` / this verification branch:
  - `scripts/drain_webhook_queue.py`
  - `scripts/prismatic-webhook-drain.service`
  - `scripts/prismatic-webhook-drain.timer`
  - `tests/test_drain_webhook_queue.py`
- Targeted test rerun from `/tmp/ned-gro-2391`:
  - `python3 -m pytest tests/test_drain_webhook_queue.py -q`
  - Result: `9 passed in 0.72s`

## Live production probe from this cron run

- Current worktree `/home/ubuntu/work/prismatic-engine` does **not** contain `scripts/drain_webhook_queue.py` (`test -f` exit 1), so the shipped implementation is not present on the currently checked-out Ned worktree branch.
- `/etc/systemd/system/prismatic-webhook-drain.service` absent (`test -f` exit 1).
- `/etc/systemd/system/prismatic-webhook-drain.timer` absent (`test -f` exit 1); `systemctl status prismatic-webhook-drain.timer` reports unit is masked/inactive.
- `prismatic-gateway.service`: `systemctl is-active` returned `activating` during probe.
- Health probes:
  - `curl http://localhost:9000/health` → `000`
  - `curl https://webhooks.growthwebdev.com/health` → `502`
- Queue DB exists at `prismatic_state/linear_webhook_queue.db`, size `3354624`; schema table is `linear_webhook_queue` (not `webhook_events`), so queue-stat tooling must use the live schema name.

## Disposition

The code/test side for [GRO-2391](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-2391) is already implemented and passes its targeted tests. The production installation/activation side is not verified healthy from the current host state: drain timer is absent/masked and webhook health is failing.

This verification report is the missing post-abandonment evidence. Recommended next action is human/SRE review of whether to merge/install the existing implementation branch and unmask/install the timer; I did not perform systemd installation or service mutation from cron.
