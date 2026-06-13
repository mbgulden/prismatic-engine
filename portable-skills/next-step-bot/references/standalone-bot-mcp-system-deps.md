# Standalone Bot MCP Dependency Diagnostic (Jun 2026)

**Root cause:** Jamie (TheNextNextStepBot) failed to load HD MCP skills for weeks because `swisseph` and `mcp` SDK were only installed in the Hermes orchestrator's sandboxed user site-packages, not system-wide.

## Problem

The standalone bot (`bot.py`) runs as the `ubuntu` user via systemd. It imports `mcp_server` → which imports `swisseph` and `mcp.server.fastmcp`. If these are only in `~/.hermes/profiles/orchestrator/home/.local/lib/python3.12/`, the `ubuntu` user can't access them.

**Symptom:** Bot starts, responds to messages normally, but never calls HD MCP tools. Falls back to generic responses with no chart data. No visible error to the user — just "struggling to do its job."

## Diagnostic

```bash
# Test raw import as the ubuntu user
sudo -u ubuntu python3 -c "
import sys
sys.path.insert(0, '${PRISMATIC_HOME}/work/OpenHumanDesignMCP/hd-mcp-server/src')
from mcp_server import get_deep_context, get_relationship_composite
print('OK')
"
```

**Failure output:**
- `ModuleNotFoundError: No module named 'swisseph'` → pyswisseph not installed system-wide
- `ModuleNotFoundError: No module named 'mcp'` → mcp SDK not installed system-wide

## Fix

```bash
# Install both system-wide
sudo pip install --break-system-packages pyswisseph mcp

# Verify
sudo -u ubuntu python3 -c "
import sys
sys.path.insert(0, '${PRISMATIC_HOME}/work/OpenHumanDesignMCP/hd-mcp-server/src')
from mcp_server import get_deep_context
print('OK')
"

# Restart the bot
sudo systemctl restart next-step-bot
```

## Why This Matters

The orchestrator (Fred) and standalone bots (Jamie, Sage) have DIFFERENT Python environments:
- **Fred**: user site-packages at `~/.hermes/profiles/orchestrator/home/.local/`
- **Jamie/Sage**: systemd `User=ubuntu`, uses system site-packages

Always install MCP dependencies system-wide when they're shared between the orchestrator and standalone bots.
