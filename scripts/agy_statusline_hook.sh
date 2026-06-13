#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────
# scripts/agy_statusline_hook.sh — AGY Status Line Hook (for settings.json)
#
# Minimal hook that receives AGY status line JSON on stdin and
# pipes it to the live parser. Called by AGY's statusLine hook
# system on every state change.
#
# Registered in: ~/.gemini/antigravity-cli/settings.json
#   "statusLine": {
#     "command": "/home/ubuntu/work/prismatic-engine/scripts/agy_statusline_hook.sh",
#     "enabled": true
#   }
# ────────────────────────────────────────────────────────────────

PRISMATIC_ENGINE="${PRISMATIC_HOME:-/home/ubuntu}/work/prismatic-engine"
PARSER="$PRISMATIC_ENGINE/prismatic/agy_live_parser.py"

# Run the parser with the prismatic-engine on PYTHONPATH
cd "$PRISMATIC_ENGINE" && python3 -u "$PARSER" 2>>/tmp/agy_statusline_hook.log
