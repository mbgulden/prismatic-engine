# Telegram Bot 409 Conflict — Full Diagnostic Workflow

When a Telegram bot returns `telegram.error.Conflict: terminated by other getUpdates request` (HTTP 409), another instance is polling the same bot token. This reference covers the full diagnostic path, sorted from most common to most obscure.

## Quick Check (30 seconds)

1. **How many Python processes are running bot.py?**
   ```bash
   ps aux | grep "bot.py" | grep -v grep
   ```
   More than one = problem. But one is NOT a guarantee of safety — see "Systemd Ghost Service" below.

2. **Check the actual env vars of the running process:**
   ```bash
   cat /proc/$(pgrep -f "next-step-{user}" | head -1)/environ | tr '\0' '\n' | grep TELEGRAM
   ```
   Verify the token matches what you expect. A wrong token = different bot entirely.

3. **Force-clear Telegram's state and restart:**
   ```bash
   TOKEN=$(grep TELEGRAM_BOT_TOKEN .env | cut -d= -f2)
   curl -s "https://api.telegram.org/bot${TOKEN}/deleteWebhook?drop_pending_updates=true"
   sleep 60  # Critical — Telegram long-poll timeout is ~50 seconds
   sudo systemctl restart your-service
   ```

## Systemd Ghost Service (The Hidden Killer)

**Symptom**: `ps aux` shows ONE bot process. No other bot.py instances anywhere. But 409 persists every 10 seconds indefinitely.

**Root cause**: An OLD systemd service for the same bot is still enabled and active. Systemd respawns the old process silently — by the time you check `ps aux`, the old one was killed by the new one's polling start, then systemd respawns it, it kills the new one's polling, and the cycle continues.

**Diagnostic commands**:
```bash
# List ALL systemd services — not just active ones
systemctl list-units --all | grep -iE "becca|sage|next-step|bot"

# Check if multiple services point to the same bot.py
systemctl cat next-step-becca 2>/dev/null
systemctl cat becca-sage 2>/dev/null

# See process ancestry — which service spawned which PID?
systemctl status next-step-becca becca-sage --no-pager
```

**Fix**:
```bash
# Stop + disable the OLD service permanently
sudo systemctl stop next-step-becca --no-block
sudo systemctl disable next-step-becca --now

# Verify only one service remains
systemctl list-units | grep -E "becca|sage"

# Restart the new service
sudo systemctl restart becca-sage

# Wait 15 seconds, then verify no 409s
sleep 15
sudo journalctl -u becca-sage --no-pager -l --since "20 seconds ago" | grep -c "409 Conflict"
# Should return 0
```

## Real Case: June 2, 2026 — Becca's Sage Bot

**Duration**: 40 minutes of failed debugging

**What was tried** (all failed):
- `pkill -9 -f "becca/bot.py"` multiple times
- Waiting 45-60 seconds between kills and restarts
- Force-clearing webhooks via `deleteWebhook?drop_pending_updates=true`
- Killing processes by PID with `kill -9`
- Adding `drop_pending_updates=True` to `run_polling()`

**What actually worked**: Discovering that `next-step-becca.service` (created May 27) was still enabled and active alongside the new `becca-sage.service`. Both ran the same `bot.py` with the same token. `systemctl stop next-step-becca && systemctl disable next-step-becca` resolved it immediately.

**Why it was invisible**: `ps aux | grep becca` showed only ONE Python process because the two services were killing each other's long-poll connections every few seconds. The losing process died, the winning process showed in `ps`, then systemd respawned the loser and the cycle repeated. At any given moment, only one appeared in `ps`.

## `terminal(background=true)` Spawn Cascade

**Pattern**: Multiple `terminal(background=true)` calls during debugging create bash wrappers that survive `pkill`:

```bash
# Each call creates: bash → python bot.py
terminal(background=true): "cd ... && python bot.py"  # spawns bash PID A → python PID B
terminal(background=true): "cd ... && python bot.py"  # spawns bash PID C → python PID D
terminal(background=true): "cd ... && python bot.py"  # spawns bash PID E → python PID F

# pkill -f "bot.py" kills B, D, F
# But A, C, E (bash wrappers) survive and may respawn
```

**Prevention**: Never use `terminal(background=true)` for long-lived services. Always deploy via systemd:
```ini
[Service]
Type=simple
User=ubuntu
WorkingDirectory=$PRISMATIC_HOME/work/next-step-becca
EnvironmentFile=$PRISMATIC_HOME/work/next-step-becca/.env
ExecStart=$PRISMATIC_HOME/.local/share/pipx/venvs/hermes-agent/bin/python $PRISMATIC_HOME/work/next-step-becca/bot.py
Restart=on-failure
RestartSec=10
```

## Prevention Checklist

When deploying a new bot or migrating a bot to a new service name:

- [ ] Verify no other systemd services use the same token: `grep -rl "BOT_TOKEN" /etc/systemd/system/`
- [ ] Stop + disable old services before starting new ones
- [ ] `app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)` is in bot.py
- [ ] After restart, wait 15 seconds then check for 409s: `journalctl -u service-name | grep "409 Conflict"`
- [ ] Never use `terminal(background=true)` for bots — systemd only
