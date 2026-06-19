---
type: Project
title: AGY Activation Investigation (June 2026)
description: Why AGY wasn't running, how the nudge-based dispatch chain works, and how to bring AGY back online. Captured after the Tier 7 work was complete but agents were silent.
resource: okf/projects/prismatic-engine/agy-activation-investigation.md
tags: [project, prismatic-engine, agy, dispatch, nudge, activation, investigation]
timestamp: 2026-06-19T22:45:00Z
linear_issue: GRO-2085
---

# AGY Activation Investigation

After the Tier 7 production-grade work shipped and the webhook handler was verified dispatching, AGY itself was not running. **Dispatch was firing, but the signal went nowhere.** This investigation explains why, maps the full chain, and gives the activation sequence.

## TL;DR

**AGY Sandbox Supervisor cron was paused on 2026-06-18 at 16:50 UTC.** The last AGY run exited with SIGTERM (code -15). Since then, no AGY process has been launched. The dispatch pipeline writes `/tmp/prismatic/nudge-fred` correctly, but no consumer is reading it.

**To re-enable AGY:** resume the cron job. That's it. The full dispatch chain is intact.

## Investigation 1: Why is AGY not running?

### What I checked

1. **AGY processes** — `ps -ef | grep agy` returns 0 results.
2. **Antigravity CLI** — `~/.local/bin/agy` and `~/.local/bin/agy-bin` both exist (1-line bash wrapper → 172MB ELF binary). Not running.
3. **AGY systemd service** — none registered. AGY is launched by cron + tmux, not systemd.
4. **AGY cron jobs** — checked all jobs in `~/.hermes/profiles/orchestrator/cron/jobs.json`:
   - `AGY Golden Thread Project Review` — daily, last ran 06:04 today, OK
   - `AGY Watchdog — Stuck Detection` — every 5min, last OK
   - `AGY Resource Monitor` — every 5min, last OK
   - `AGY OAuth Auto-Refresh` — every 45min, last OK
   - `🔮 Second Witness — AGY Prismatic review terminal` — every 30min, last OK
   - **`AGY Sandbox Supervisor — event-driven organic scaling`** — **every 15min, enabled=False, state=paused, paused_at=2026-06-18T16:50:56**
5. **Last error** — `Script exited with code -15` (SIGTERM).

### Conclusion

The cron that **starts AGY sandbox workers** (`agy_sandbox_event_supervisor_cron.sh`) was paused 24 hours ago. The pause happened deliberately (`enabled: false`, not a transient error). Whoever paused it was probably cleaning up after a test run.

**The cron has nothing to do with the webhook dispatcher.** The dispatcher works independently (writes nudge files). But without the supervisor, **nothing reads those nudge files and launches AGY.**

## Investigation 2: The full IPC bridge — how dispatch reaches AGY

### Chain (top to bottom)

```
[Linear webhook]
  → POST https://webhooks.growthwebdev.com/webhooks/linear
  → Cloudflare Tunnel ("Growth Web v2") → http://127.0.0.1:9000
  → Prismatic Engine gateway (HMAC validation passes)
  → linear_webhook handler
  → dispatch_issue_by_identifier(identifier)
  → signal_fred(issue_id, title, priority)
  → FileSignalProvider.send_work(target="fred", ...)
  → writes /tmp/prismatic/nudge-fred (JSON payload, mode 0600)
  → ??? [GAP: no consumer right now]
```

### What reads `/tmp/prismatic/nudge-fred`?

When AGY is alive, this is the chain:

```
[every 1 min] nudge_detector.py cron
  → checks /tmp/prismatic/nudge-* files
  → if found, posts alert to Hermes (or creates /tmp/trigger-fred-work)
  
[then] Hermes orchestrator picks up trigger via skills
  → loads antigravity-cli-orchestration skill
  → spawns AGY subprocess via launch_agy_with_artifact.py
  → AGY reads nudge payload, executes the task, writes result.md
  → orchestrator acks nudge, transitions issue to agent:done
```

Key files (all in `~/.hermes/profiles/orchestrator/`):

| File | Role |
|---|---|
| `scripts/nudge_detector.py` | Polls every 1 min, finds nudge files, fires alerts |
| `scripts/nudge_executor.py` | Executes nudges (called via cron or skill) |
| `scripts/launch_agy_with_artifact.py` | PTY + tmux wrapper that handles AGY's `/tmp` write restriction |
| `scripts/agy_sandbox_event_supervisor.py` | The supervisor that spawns AGY in tmux sessions |
| `scripts/agy_sandbox_event_supervisor_cron.sh` | Cron wrapper for the supervisor (the one that's paused) |
| `skills/agent-orchestration/antigravity-cli-orchestration/SKILL.md` | The full AGY-launching skill with non-TTY launch patterns |
| `skills/operations/nudge-escalation-monitor/SKILL.md` | Stale-nudge detection (escalates >30min old nudges) |

### What is on disk right now

- `/tmp/prismatic/nudge-fred` — 347 bytes, written 21:12 (our last test dispatch). **Still there, unconsumed.**
- `/tmp/prismatic/nudge-kai` — 236 bytes, written 12:25 today
- `/tmp/prismatic/nudge-ned` — 374 bytes, written 16:34 today
- `/tmp/prismatic/.gpu_state.json` — 107 bytes, updated 22:33
- `/tmp/prismatic/watchdog_state.json` — 481 bytes, updated 22:36

So nudges ARE accumulating. None are being consumed. The nudge executor (LLM-driven) is also paused or not running.

### Who watches the IPC bridge for messages TO AGY?

The Prismatic Engine gateway exposes an IPC bridge on a Unix socket at `$PRISMATIC_STATE_DIR/ipc_bridge.sock` and an HTTP endpoint at `POST /api/gateway/events`. AGY normally subscribes to one of these to receive event notifications. Since AGY isn't running, nothing is subscribed, and the events queue up (or are dropped).

## Investigation 3: AGY activation sequence

### Step-by-step to bring AGY back online

1. **Verify the supervisor script is still valid**

   ```bash
   ls -la /home/ubuntu/.hermes/profiles/orchestrator/scripts/agy_sandbox_event_supervisor.py
   python3 /home/ubuntu/.hermes/profiles/orchestrator/scripts/agy_sandbox_event_supervisor.py --help 2>&1 | head
   ```

2. **Check the AGY auth token is fresh**

   ```bash
   ls -la ~/.gemini/antigravity-cli/antigravity-oauth-token
   # If last-modified > 1 hour ago, run: agy auth login
   ```

3. **Manually launch the supervisor once (dry-run)**

   ```bash
   cd /home/ubuntu/.hermes/profiles/orchestrator
   timeout 60 ./scripts/agy_sandbox_event_supervisor_cron.sh --max-concurrent 1 --once
   ```

   This runs the supervisor for one cycle. It should:
   - Mount tmpfs at /tmp/agy_sandboxes
   - Pick up any queued nudges
   - Launch AGY in a tmux session
   - Process one task
   - Exit cleanly

4. **If dry-run succeeds, re-enable the cron**

   ```bash
   # Via cronjob tool:
   cronjob action=update job_id=...
   # OR directly:
   python3 -c "
   import json
   p = '/home/ubuntu/.hermes/profiles/orchestrator/cron/jobs.json'
   data = json.load(open(p))
   for j in data['jobs']:
       if 'sandbox supervisor' in j.get('name','').lower():
           j['enabled'] = True
           j['state'] = 'scheduled'
           j['paused_at'] = None
           j['paused_reason'] = None
   json.dump(data, open(p,'w'), indent=2)
   "
   ```

5. **Verify it's running**

   ```bash
   # Within 15 min, you should see AGY processes:
   ps -ef | grep -E '[a]gy' | head
   
   # Nudge files should start getting consumed:
   ls -la /tmp/prismatic/nudge-*
   
   # The cron should have logged a successful run:
   python3 -c "
   import json
   data = json.load(open('/home/ubuntu/.hermes/profiles/orchestrator/cron/jobs.json'))
   for j in data['jobs']:
       if 'sandbox supervisor' in j.get('name','').lower():
           print(f\"last_status: {j['last_status']}  last_run: {j['last_run_at']}\")
   "
   ```

### What success looks like

After re-enabling:
- Within 15 min: AGY sandbox supervisor runs, processes existing nudges
- `/tmp/prismatic/nudge-fred` deleted (acked)
- AGY processes spawn in tmux sessions (`tmux ls` shows `agy-GRO-XXXX`)
- Audit log shows new entries with `outcome: "dispatched"` and signal_fred successfully invoking AGY
- Real Linear issues with `agent:fred` label trigger AGY launches

### What failure looks like

If after re-enabling you see:
- `last_status: "error"` with `ImportError` or `linear_call` errors → scripts need patching (see `linear_api_compat.py` shim)
- AGY launches but immediately dies → AGY OAuth token expired, run `agy auth login`
- Nudge files persist without consumption → supervisor is paused or failing silently; check journal

## What was learned

### Architecture clarity

The Prismatic Engine gateway is just the **dispatch producer**. It writes nudge files but does NOT launch AGY. That's the **AGY Sandbox Supervisor's job**. The producer and consumer are separate cron jobs/services.

### Operational gap

The webhook → dispatcher → nudge chain is fully production-grade. The nudge → AGY chain was dependent on a cron that someone paused. There's no automated alerting when the consumer is paused while nudges are accumulating.

### Recommended improvement

Add a health check: if `nudge-fred` file is older than 5 minutes AND AGY processes count is 0, fire an alert to the morning digest. This would have caught the silent failure 24 hours earlier.

## References

- AGY Sandbox Supervisor cron: job ID in `~/.hermes/profiles/orchestrator/cron/jobs.json`
- FileSignalProvider: `prismatic-engine/prismatic/providers/signals/file.py`
- nudge_detector.py: `~/.hermes/profiles/orchestrator/scripts/nudge_detector.py`
- antigravity-cli-orchestration skill: `~/.hermes/profiles/orchestrator/skills/agent-orchestration/antigravity-cli-orchestration/SKILL.md`
- Nudge format spec: `prismatic-engine/prismatic/providers/signals/base.py` (SignalPayload dataclass)
- IPC bridge: `prismatic-engine/prismatic/gateway/ipc_bridge.py`

## Action items for the user

1. **Decide whether to re-enable AGY Sandbox Supervisor cron** — yes if you want AGY to pick up nudges automatically.
2. **Decide what to do with accumulated nudges** — the 3 stale nudge files (fred, kai, ned) are stale work that wasn't picked up.
3. **Consider adding the health check alert** (nudge age + AGY process count) so this doesn't go silent again.
