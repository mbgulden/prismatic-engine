# scripts/ops/ — Factory Operations: Monitor + Responder

This directory contains the **operational layer** of the Prismatic Engine: the heartbeat monitor and the auto-responder that gives the monitor teeth. These scripts run via systemd on the gateway host, NOT inside the engine process.

## What's here

| File | Purpose |
|---|---|
| `factory_monitor.py` | Heartbeat health check. Runs every 15 min, reports on services/endpoints/bus/curator/pool/vault. Stdlib only. |
| `factory_responder.py` | Action-taker. Reads monitor output, classifies alerts, takes automated action: service restart, Linear issue creation, AGY dispatch, Telegram notification with narrative messages. |
| `clear_stale_escalations.py` | One-shot cleanup for the 6 stale escalations from the GRO-3035 rollback. |
| `systemd/factory-monitor.service` | Systemd unit: oneshot service that runs the monitor. |
| `systemd/factory-monitor.timer` | Systemd timer: fires every 15 minutes (`OnCalendar=*:0/15`). |
| `systemd/factory-responder.service` | Systemd unit: runs the responder. Called after the monitor in the same cycle (the responder invokes the monitor inline). |
| `systemd/factory-responder.timer` | Optional: separate timer if you want the responder on its own schedule. Currently the monitor timer triggers both. |

## How it works (end-to-end)

```
   ┌─────────────────────────────────────────────┐
   │ systemd timer (every 15 min)                │
   │ factory-monitor.timer                       │
   └────────────────┬────────────────────────────┘
                    │
                    ▼
   ┌─────────────────────────────────────────────┐
   │ factory-monitor.service (oneshot)           │
   │   - runs factory_monitor.py                 │
   │   - checks services, endpoints, bus, etc.  │
   │   - outputs JSON to stdout                  │
   │   - exits 0 (OK) or 2 (CRITICAL)           │
   └────────────────┬────────────────────────────┘
                    │
                    ▼
   ┌─────────────────────────────────────────────┐
   │ factory-responder.service (oneshot)         │
   │   - runs factory_responder.py               │
   │   - calls monitor inline (reads JSON)       │
   │   - classifies each alert:                  │
   │     * service down → systemctl restart     │
   │     * endpoint 404/5xx → restart gateway    │
   │     * general WARN → Telegram (debounced)  │
   │     * CRITICAL → Linear + Telegram          │
   │   - sends narrative Telegram to Michael    │
   │   - logs everything to                      │
   │     ~/.prismatic/responder.log              │
   └─────────────────────────────────────────────┘
```

## Deployment on a new host

### 1. Install the scripts

```bash
# Copy the monitor + responder to the orchestrator profile
mkdir -p /home/ubuntu/.hermes/profiles/orchestrator/scripts
cp scripts/ops/factory_monitor.py /home/ubuntu/.hermes/profiles/orchestrator/scripts/
cp scripts/ops/factory_responder.py /home/ubuntu/.hermes/profiles/orchestrator/scripts/
chmod +x /home/ubuntu/.hermes/profiles/orchestrator/scripts/factory_*.py
```

### 2. Install the systemd units

```bash
sudo cp scripts/ops/systemd/factory-monitor.service /etc/systemd/system/
sudo cp scripts/ops/systemd/factory-monitor.timer /etc/systemd/system/
sudo cp scripts/ops/systemd/factory-responder.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable factory-monitor.timer
```

The monitor timer automatically triggers the responder (via the responder service's `After=factory-monitor.service` dependency). If you want the responder on a separate schedule, also `sudo systemctl enable factory-responder.timer`.

### 3. Set up Telegram credentials

The responder sends narrative messages to your phone. Set up the bot once:

```bash
# 1. Create a Telegram bot via @BotFather, get the token
# 2. Send a message to your new bot, then visit
#    https://api.telegram.org/bot<TOKEN>/getUpdates to get your chat_id
# 3. Store the creds in the vault:

python3 -c "
import json
v = '/home/ubuntu/.prismatic/vault/secrets.json'
with open(v) as f: d = json.load(f)
d['secrets']['telegram.bot_token'] = '<YOUR_BOT_TOKEN>'
d['secrets']['telegram.chat_id'] = '<YOUR_CHAT_ID>'
with open(v, 'w') as f: json.dump(d, f, indent=2)
"
chmod 600 /home/ubuntu/.prismatic/vault/secrets.json
```

Or, alternatively, set them as env vars in the orchestrator profile (e.g., `active-oahu/.env`):

```
TELEGRAM_BOT_TOKEN=<your token>
TELEGRAM_HOME_CHANNEL=<your chat id>
```

The responder looks in the vault first, then falls back to env vars.

### 4. Set up Linear API key (for CRITICAL escalation)

The responder creates a Linear issue for every CRITICAL alert (debounced 24h per pattern). If you don't have one already:

```bash
# 1. Get a Linear API key from https://linear.app/settings/api
# 2. Add it to the orchestrator profile env:
echo "LINEAR_API_KEY=lin_api_..." >> /home/ubuntu/.hermes/profiles/orchestrator/.env
```

If you skip this, the responder still works — it just skips Linear issue creation. Telegram notifications still go out.

### 5. Verify it works

```bash
# Run the monitor manually
python3 /home/ubuntu/.hermes/profiles/orchestrator/scripts/factory_monitor.py

# Run the responder manually (dry-run first)
python3 /home/ubuntu/.hermes/profiles/orchestrator/scripts/factory_responder.py --dry-run

# Force a run of both
sudo systemctl start factory-monitor.service
sudo systemctl start factory-responder.service

# Check the logs
tail -20 /home/ubuntu/.hermes/profiles/orchestrator/logs/factory-monitor.log
tail -20 /home/ubuntu/.hermes/profiles/orchestrator/logs/factory-responder.log
tail -20 /home/ubuntu/.prismatic/responder.log
```

You should see:
- Monitor: a clean factory health report
- Responder: a "no action needed" message (because the factory is fine)
- Telegram: no message (because there were no alerts)

## Testing the responder end-to-end

To verify the responder actually works (auto-restart, Telegram, Linear), simulate an alert:

```bash
# Create a fake monitor output: prismatic-curator is down
python3 << 'PYEOF'
import json
fake = {
    "severity": "CRITICAL",
    "alerts": ["CRITICAL: service prismatic-curator is not active"],
    "checks": {
        "services": {
            "prismatic-gateway": {"active": True, "recent_restart": False},
            "prismatic-consumer": {"active": True, "recent_restart": False},
            "prismatic-curator": {"active": False, "recent_restart": False},
        },
        "endpoints": {"/health": {"status": 200, "ok": True}},
        "bus": {"total": 310, "processed": 310, "pending": 0},
    },
}
with open("/tmp/fake-monitor.json", "w") as f:
    json.dump(fake, f)
PYEOF

# Run the responder on the fake alert
python3 /home/ubuntu/.hermes/profiles/orchestrator/scripts/factory_responder.py \
    --monitor-output /tmp/fake-monitor.json --once

# Check: did prismatic-curator come back?
systemctl is-active prismatic-curator

# Check the Telegram message
# (look at the recent messages in your phone)

# Check the Linear issue
# (look for GRO-XXXX in the GRO team)
```

## How Michael's Telegram messages look

The responder sends 4 message types:

| Type | When | Decision asked? |
|---|---|---|
| 🚨 **EMERGENCY — RESOLVED** | I auto-fixed it | Never — you do nothing |
| ⚠️ **Heads up** | Informational signal | Never |
| 🚨 **REAL DECISION NEEDED** | Recovery failed, rollback is the right call | Yes (single Yes/No) |
| 🔍 **Investigation dispatched** | Sent AGY to investigate | Never |

The DECISION message **always** states Fred's professional recommendation, **always** lists the cost of both options, and **always** has a safe default that fires if you don't reply. The default is the right call 95% of the time.

## File paths reference

| What | Where |
|---|---|
| Vault (encrypted secrets) | `~/.prismatic/vault/secrets.json` (+ `.gpg` backup) |
| Bus (event log) | `~/.prismatic/bus/event_log.sqlite` |
| Curator state | `~/.prismatic/curator/state.sqlite` |
| Daily digests | `~/.prismatic/curator/digests/YYYY-MM-DD.md` |
| Responder debounce state | `~/.prismatic/responder-state.json` |
| Telegram offset cursor | `~/.prismatic/telegram-offset` |
| Responder log (every action) | `~/.prismatic/responder.log` |
| Monitor log | `~/.hermes/profiles/orchestrator/logs/factory-monitor.log` |
| Responder log (stdout) | `~/.hermes/profiles/orchestrator/logs/factory-responder.log` |
| AGY dispatch output | `~/.hermes/profiles/orchestrator/logs/agy-dispatch.log` |

## Tuning

### Adjusting debounce

The monitor/responder state at `~/.prismatic/responder-state.json` has debounce timestamps. To force re-notification for a specific alert:

```bash
python3 -c "
import json
with open('/home/ubuntu/.prismatic/responder-state.json') as f:
    s = json.load(f)
s['last_sent'].pop('restart_prismatic-curator', None)  # or any alert_key
with open('/home/ubuntu/.prismatic/responder-state.json', 'w') as f:
    json.dump(s, f, indent=2)
"
```

### Adjusting thresholds

Edit `factory_monitor.py` — the `WARN`/`CRITICAL` thresholds are at the bottom of `assess()`. Add new alert types by editing `assess()` and `classify_alert()`.

### Disabling auto-actions

To run the monitor without auto-actions (e.g., for testing), set `enable_actions: false` in the systemd unit's `ExecStart` command:

```ini
ExecStart=/home/ubuntu/.prismatic/venv_stable/bin/python3 /home/ubuntu/.hermes/profiles/orchestrator/scripts/factory_responder.py --once --dry-run
```

## What this is NOT

This is **not** the agent that dispatches new work. The **AGY lane supervisor** (in `prismatic/curator/`) handles that. This is purely the **operational heartbeat** — keeping the factory running, alerting when things break, and trying to auto-recover before paging Michael.

If you want to dispatch new work to the engine, that's a separate flow: write a Linear issue with the `dispatch:ready` label, and the lane's supervisor picks it up. (See the curator's `tag_event()` rules and the `dispatch_consumer_v3.py` worker pool.)
