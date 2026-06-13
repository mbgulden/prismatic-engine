# Bot Capability Audit — Runtime Verification

## When to Run
- When Michael asks "does X bot have Y capability?"
- After wiring new features (HD MCP, delegation, skills) to verify they took
- When a bot was restarted and you need to confirm the new config loaded
- Before handing a bot to a new user — verify what it can actually do

## Audit Workflow

### 1. Find bot directory and running PID

```bash
# Find bot directories
find /home/ubuntu/work -maxdepth 3 -type d | grep -iE "next-step|sage|jamie|sam|becca"

# Find running PIDs
ps aux | grep "bot.py" | grep -v grep
# Output: ubuntu  PID  ... python /home/ubuntu/work/next-step-becca/bot.py
```

### 2. Dump runtime environment variables

The `.env` file shows the CONFIGURED values. The `/proc/<PID>/environ` shows what the running process ACTUALLY has. They can diverge if the `.env` was edited without restart.

```bash
cat /proc/<PID>/environ | tr '\0' '\n' | grep -iE "NEXTSTEP|MCP|TELEGRAM|DEEPSEEK|ALLOWED|OHDMCP"
```

Key vars to check:
- `NEXTSTEP_NAME` — bot identity (Jamie, Sage, Sam, Jeff)
- `NEXTSTEP_PROFILE` — which family.json profile is active
- `NEXTSTEP_MCP_SRC` — path to HD engine (should be `/home/ubuntu/work/OpenHumanDesignMCP/hd-mcp-server/src`)
- `OHDMCP_FAMILY_JSON` — **critical**: separate env var required by `get_deep_context()` / `get_relationship_composite()` in the MCP server. Without it, HD profile lookups fail silently. Should match `NEXTSTEP_FAMILY_PATH`.
- `TELEGRAM_BOT_TOKEN` — verify this isn't shared with another bot (shared token = 409 conflict)
- `NEXTSTEP_MODEL` + `NEXTSTEP_BASE_URL` — which AI provider is the bot actually using

### 3. Verify MCP imports AND profile resolution work

```bash
# Quick import check
python3 -c "
import sys
sys.path.insert(0, '/home/ubuntu/work/OpenHumanDesignMCP/hd-mcp-server/src')
from mcp_server import get_deep_context, get_relationship_composite
from cosmic_calculator import calculate_natal_chart
print('MCP imports OK')
"

# Full profile resolution test — this catches missing OHDMCP_FAMILY_JSON
OHDMCP_FAMILY_JSON=/home/ubuntu/work/next-step-bot/family.json python3 -c "
import sys, os
os.environ['OHDMCP_FAMILY_JSON'] = '/home/ubuntu/work/next-step-bot/family.json'
sys.path.insert(0, '/home/ubuntu/work/OpenHumanDesignMCP/hd-mcp-server/src')
from mcp_server import get_deep_context, get_relationship_composite
from ephemeris_engine import init_ephemeris
init_ephemeris()

# Test each profile in the family
import json
with open('/home/ubuntu/work/next-step-bot/family.json') as f:
    profiles = json.load(f).get('family', {})

for name in profiles:
    result = get_deep_context(name)
    if 'error' in result:
        print(f'❌ {name}: {result[\"error\"]}')
    else:
        c = result.get('chart', {})
        print(f'✅ {name}: {c.get(\"type\", \"?\")} {c.get(\"profile\", \"?\")} {c.get(\"authority\", \"?\")}')
"
```

If this fails: `pip install --break-system-packages pyswisseph mcp` (the bot runs as system `ubuntu` user, not in a venv).

### 4. Scan bot source for capability references

```bash
grep -n "mcp_server\|get_deep_context\|transit\|bodygraph\|relate\|chart\|lookup_gate\|tool_name\|/chart\|DELEGATION_ENABLED" /path/to/bot.py | head -40
```

This reveals which HD tools are wired, which slash commands exist, and whether delegation is enabled.

### 5. Produce capability matrix

For each bot, report:

| Capability | How to verify |
|---|---|
| HD natal chart | `deep_context` tool + `/chart` command |
| HD transits | `transits` tool + silent pre-fetch in conversation context |
| HD relationships | `relate` tool (calls `get_relationship_composite`) |
| Astro-cartography | `map` tool (calls `calculate_cartography_lines`) |
| Gate lookup | `lookup_gate` import (Jeff-only extra) |
| Fred delegation | `!fred` command → `bot_delegation.ask_fred()` |
| Task management | `list` / `done` tools |
| Dopamine party | Check SOUL.md for celebration rules |
| Journal | `[JOURNAL: ...]` tag stripping in response handler |

## All Three Bots Share the Same MCP Engine

Jeff (next-step-bot), Sage (next-step-becca), and Sam (next-step-sam) all point `NEXTSTEP_MCP_SRC` at `/home/ubuntu/work/OpenHumanDesignMCP/hd-mcp-server/src/`. They import the same engine. Capability differences come from:
- Which tools the bot code actually exposes (e.g., Jeff has `lookup_gate`, Sage doesn't)
- Which family.json profile is active (`NEXTSTEP_PROFILE`)
- Whether `DELEGATION_ENABLED` resolved at import time

## Pitfalls

- **`.env` ≠ runtime**: Always check `/proc/<PID>/environ`, not the `.env` file. The bot may have been started with different env vars than what's currently in `.env`.
- **`OHDMCP_FAMILY_JSON` — the silent HD killer**: The MCP server's `get_deep_context()` and `get_relationship_composite()` read `OHDMCP_FAMILY_JSON`, NOT `NEXTSTEP_FAMILY_PATH`. If a bot has `NEXTSTEP_MCP_SRC` and `NEXTSTEP_FAMILY_PATH` but NOT `OHDMCP_FAMILY_JSON`, HD profile lookups return `"Profile 'becca' not found. Available: []"`. The bot starts fine, the `/chart` command works (via the fallback path), but relationship reports and cross-profile deep context fail. **The fix**: add `OHDMCP_FAMILY_JSON=/home/ubuntu/work/next-step-bot/family.json` to the bot's `.env` and restart. This was the root cause of a 45-minute debugging session where Sage appeared HD-capable but couldn't resolve Becca's profile through the MCP server path.
- **MCP_SRC directory must exist at startup**: The bot checks `Path(MCP_SRC).is_dir()` once at import time. If the directory didn't exist when the bot started, HD tools are silently unavailable even if you create the directory later. Restart the bot after fixing paths.
- **System-wide pyswisseph required**: The bot runs as system `ubuntu`, not inside a pipx venv. `pyswisseph` and `mcp` must be installed system-wide. See `references/standalone-bot-mcp-system-deps.md`.
- **Shared token detection**: If `getMe` returns a different bot name than expected, the token is shared. Each bot needs a unique token from @BotFather. See SKILL.md pitfalls for full diagnostic.
