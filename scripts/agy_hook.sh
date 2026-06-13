#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────
# scripts/agy_hook.sh — AGY Status Line Hook
#
# Wraps AGY execution and pipes its stdout (status line JSON) to
# the live parser engine, which extracts model state, token usage,
# and rate limit data into the telemetry database.
#
# Usage:
#   agy_hook.sh [AGY_ARGS...]
#
# Examples:
#   agy_hook.sh --print "Goal: audit the repo"
#   agy_hook.sh --print --model "Gemini 3.5 Flash (Medium)" "Goal: research"
#
# The AGY binary is invoked normally; its stdout is intercepted and
# each line is parsed by agy_live_parser.py. The original AGY output
# is also saved to a log file for debugging.
# ────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Paths ──────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRISMATIC_DIR="$(dirname "$SCRIPT_DIR")"
AGY_BIN="${AGY_BIN:-/home/ubuntu/.local/bin/agy}"
AGY_HOME="${PRISMATIC_HOME:-/home/ubuntu/.hermes/profiles/orchestrator/home}"
LOG_DIR="${PRISMATIC_STATE_DIR:-$PRISMATIC_DIR/prismatic_state}/agy_logs"
PARSER="$PRISMATIC_DIR/prismatic/agy_live_parser.py"

# ── Ensure log directory ───────────────────────────────────────
mkdir -p "$LOG_DIR"

# ── Generate run ID and log path ───────────────────────────────
RUN_TS="$(date +%Y%m%d_%H%M%S)"
RUN_ID="agy-hook-${RUN_TS}-$$"
AGY_LOG="$LOG_DIR/${RUN_ID}.log"

export AGY_LIVE_RUN_ID="$RUN_ID"

# ── Invoke AGY, tee to both parser and log ─────────────────────
echo "[agy_hook] Run ID: $RUN_ID, Log: $AGY_LOG" >&2
echo "[agy_hook] CMD: $AGY_BIN" "$@" >&2

# AGY stdout → tee → (a) parser (via stdin pipe), (b) log file
# AGY stderr → normal stderr (captured separately in log)
"$AGY_BIN" \
    --dangerously-skip-permissions \
    "$@" \
    2>"$AGY_LOG" \
    | HOME="$AGY_HOME" python3 -u "$PARSER" 2>>"$AGY_LOG"

EXIT_CODE=$?

echo "[agy_hook] AGY exited with code $EXIT_CODE" >&2
exit $EXIT_CODE
