#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────
# core/telemetry/agy_hook.sh — AGY Status Line Hook (Repo Root Version)
#
# Receives raw status line JSON on stdin and pipes it to the live
# parser, which records telemetry events to the Prismatic database.
#
# Usage:
#   agy --headless --issue GRO-1234 | agy_hook.sh --issue GRO-1234
#
# The --issue flag is optional — if omitted, the parser reads the
# PRISMATIC_ISSUE_ID environment variable.
# ────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Parse arguments ─────────────────────────────────────────────
ISSUE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --issue)
            ISSUE="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: agy_hook.sh [--issue GRO-NNNN]"
            echo "  Reads NDJSON from stdin, pipes to agy_live_parser.py"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

# ── Resolve the Prismatic Engine root ───────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Walk up to find prismatic_state/ which marks the engine root
if [ -d "$SCRIPT_DIR/../../../../prismatic_state" ]; then
    PRISMATIC_ENGINE="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
elif [ -d "$SCRIPT_DIR/../../../prismatic_state" ]; then
    PRISMATIC_ENGINE="$(cd "$SCRIPT_DIR/../../.." && pwd)"
elif [ -d "$SCRIPT_DIR/../../prismatic_state" ]; then
    PRISMATIC_ENGINE="$(cd "$SCRIPT_DIR/../.." && pwd)"
elif [ -d "$SCRIPT_DIR/../prismatic_state" ]; then
    PRISMATIC_ENGINE="$(cd "$SCRIPT_DIR/.." && pwd)"
else
    PRISMATIC_ENGINE="${PRISMATIC_ENGINE:-}"
    if [ -z "$PRISMATIC_ENGINE" ]; then
        echo "[agy_hook] Error: Cannot resolve Prismatic Engine root. Set PRISMATIC_ENGINE." >&2
        exit 1
    fi
fi

export PRISMATIC_ENGINE
export PRISMATIC_STATE_DIR="$PRISMATIC_ENGINE/prismatic_state"

PARSER="$PRISMATIC_ENGINE/prismatic/agy_live_parser.py"

# ── Build the parser command ────────────────────────────────────
PARSER_CMD=("python3" "-u" "$PARSER")
if [ -n "$ISSUE" ]; then
    PARSER_CMD+=("--issue" "$ISSUE")
fi

# Ensure the state directory exists
mkdir -p "$PRISMATIC_STATE_DIR" 2>/dev/null || true

# Pipe stdin through the parser
exec "${PARSER_CMD[@]}" 2>>/tmp/agy_statusline_hook.log
