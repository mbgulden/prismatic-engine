# Bot-to-Profile Conversion (June 2026)

How to convert standalone `bot.py` Next Step bots to full Hermes gateway profiles.

## Problem

Standalone bots run as custom `bot.py` scripts with:
- No MCP tools (HD MCP, GDrive MCP)
- No skill loading
- Fragile env-var-based config
- Manual systemd service management
- No gateway lifecycle (restart, health checks, tool enforcement)

## Solution: Repoint systemd to Hermes gateway

### Step 1: Verify profile has gateway config

The profile MUST have in `config.yaml`:
```yaml
gateway:
  port: <unique_port>
  telegram:
    bot_token: ${TELEGRAM_BOT_TOKEN}
    allowed_chats: "<chat_id>"
```

And in `.env`:
```
TELEGRAM_BOT_TOKEN=<bot_token>
GATEWAY_ALLOW_ALL_USERS=true
```

### Step 2: Create gateway systemd service

```ini
[Unit]
Description=<Name> — Hermes Gateway Profile (@<bot_username>)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
EnvironmentFile=$PRISMATIC_HOME/.hermes/profiles/<profile>/.env
Environment=HOME=$PRISMATIC_HOME
ExecStart=/home/ubuntu/.local/bin/hermes --profile <profile> gateway run
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=<identifier>

[Install]
WantedBy=multi-user.target
```

### Step 3: Deploy

```bash
sudo systemctl stop <old-bot-service> 2>/dev/null
sudo cp /tmp/<profile>-gateway.service /etc/systemd/system/<profile>.service
sudo systemctl daemon-reload
sudo systemctl enable <profile>.service
sudo systemctl start <profile>.service
```

## Proven: Autobot + Jeff (June 13, 2026)

- **Autobot** (@Autob0tautob0t_bot): Converted from `autobot.py` SSE bridge to full gateway on port 8091. Enabled toolsets: terminal, file, search, web, skills, cronjob, session_search, delegation.
- **Jeff** (@TheNextNextStepBot): Converted from `next-step-bot/bot.py` to full gateway. MCP servers auto-started: GDrive MCP + HD MCP server. HD chart calculations now available (import `swisseph` not `pyswisseph`).

## Key Finding: swisseph Import

The Swiss Ephemeris package installs via `pip install pyswisseph` but imports as `swisseph`:
```python
import swisseph as swe  # ← correct
import pyswisseph       # ← ModuleNotFoundError
```

Must be installed system-wide for gateway profiles:
```bash
sudo pip install --break-system-packages pyswisseph mcp
```
