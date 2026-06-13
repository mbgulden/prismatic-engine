# HD MCP System Dependencies for Next Step Bots

**Problem (Jun 2026):** Jamie (@TheNextNextStepBot) couldn't load HD MCP skills. The bot imports `from mcp_server import get_deep_context, get_relationship_composite`, which transitively imports `cosmic_calculator` → `swisseph`, plus `mcp.server.fastmcp` for MCP transport.

The orchestrator had `pyswisseph` installed in its profile home (`~/.hermes/profiles/orchestrator/home/.local/`), but Jamie runs as the `ubuntu` user via systemd — those packages are invisible.

**Symptoms:** 
- `No module named 'swisseph'` — `pyswisseph` not installed system-wide
- `No module named 'mcp'` — `mcp` SDK not installed system-wide
- Bot starts, connects to Telegram (200 OK), but every HD-related call fails silently

**Fix:**

```bash
# Install system-wide (not just in profile home)
sudo pip install --break-system-packages pyswisseph
sudo pip install --break-system-packages --ignore-installed typing-extensions mcp
```

**Verification:**
```bash
sudo -u ubuntu python3 -c "
import sys
sys.path.insert(0, '${PRISMATIC_HOME}/work/OpenHumanDesignMCP/hd-mcp-server/src')
from mcp_server import get_deep_context, get_relationship_composite
print('✅ HD MCP importable')
"
```

**Why `--ignore-installed typing-extensions`:** The `mcp` package requires a newer `typing-extensions` than the Debian system package. Without `--ignore-installed`, pip refuses to upgrade it. Safe to force — `mcp` depends on the newer version.

**After fix:** Restart the bot's systemd service:
```bash
sudo systemctl restart next-step-bot
```

**Pitfalls:**
- `pip install --user` installs to the current user's home — invisible to the systemd `ubuntu` user
- Don't install inside a venv unless the systemd service uses that venv's python
- The ephemeris path must be accessible: `$PRISMATIC_HOME/work/OpenHumanDesignMCP/hd-mcp-server/ephemeris/`
- Verify the bot's process environment: `cat /proc/<pid>/environ | tr '\0' '\n' | grep -E 'PYTHONPATH|MCP'`
