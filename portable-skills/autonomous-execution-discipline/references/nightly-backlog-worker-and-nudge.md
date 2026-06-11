# Nightly Backlog Worker & Nudge Trigger

Pattern for autonomous backlog clearing + on-demand activation. Set up 2026-06-03 for Michael's GrowthWebDev Linear board.

## Nightly Worker Cron

Runs at 4:00 UTC daily (after 10pm Mountain Time). Self-contained prompt that:
1. Loads `autonomous-execution-discipline` + `golden-thread` skills
2. Queries Linear for all Backlog/Todo items
3. Prioritizes by revenue impact (Stripe/payment > infra that enables revenue > content/SEO > everything else)
4. Works through as many autonomous items as possible
5. Skips items requiring human input (physical hardware, dashboard access, domain expertise)
6. Updates Linear as items complete
7. Reports results to origin (Telegram)

```bash
cronjob action=create \
  name="Nightly Autonomous Backlog Worker" \
  schedule="0 4 * * *" \
  deliver="origin" \
  skills='["autonomous-execution-discipline","golden-thread"]' \
  prompt="You are the Nightly Autonomous Backlog Worker. Michael wants you to clear through his Linear backlog every night..."
```

## Nudge Trigger (On-Demand Activation)

Michael can activate the backlog worker at any time by:
- Saying "nudge" or "keep working" in chat (triggers autonomous-execution-discipline)
- Running `touch /tmp/nudge-fred` (triggers the nudge cron job)

The nudge cron runs every minute and checks for `/tmp/nudge-fred`:
1. If the file exists → remove it and run the backlog worker
2. If not → silent (no output, no agent run)

```bash
# nudge-check.sh (saved to ~/.hermes/profiles/orchestrator/scripts/)
#!/bin/bash
if [ -f /tmp/nudge-fred ]; then
    rm -f /tmp/nudge-fred
    echo "nudge triggered — working backlog now"
else
    exit 0
fi
```

```bash
cronjob action=create \
  name="Nudge Trigger — On-Demand Backlog Worker" \
  schedule="* * * * *" \
  deliver="origin" \
  script="nudge-check.sh" \
  skills='["autonomous-execution-discipline","golden-thread"]' \
  prompt="You are the Nudge-Triggered Backlog Worker..."
```

## Smart Behavior — Don't Interrupt Active Sessions

The nightly cron runs in a separate session — it doesn't know if Michael is actively chatting. To avoid interruptions:
- The cron delivers to `origin` (back to this chat), not to a push notification channel
- Michael can adjust the cron schedule based on his routine
- The nudge is pull-based — Michael triggers it, not the other way around
- If Michael is actively working, he can ignore the cron report until later

## Priority Stack (Revenue-First)

When picking backlog items:
1. **Revenue blockers** — Stripe bugs, payment checkout failures, billing issues
2. **Revenue enablers** — SSL, nginx, deployment, payment server startup
3. **Traffic drivers** — SEO content, meta descriptions, schema injection, orphan page fixes
4. **Everything else** — docs, tests, cleanup, research

Always skip items that genuinely need Michael: physical hardware setup, Cloudflare dashboard changes (no API access), strategic decisions, domain expertise content.
