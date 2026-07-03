#!/usr/bin/env bash
# ==============================================================================
# Prismatic Engine — Heartbeat Writer
# Writes the dispatcher PID and current timestamp to heartbeat.pid.
# Called by ExecStartPost= in prismatic-dispatcher.service after daemon starts.
#
# Usage:
#   scripts/heartbeat.sh                    # Write heartbeat
#   scripts/heartbeat.sh --check            # Check if heartbeat is fresh (<120s)
#
# File format: PID
# Example: 1230107
# ==============================================================================
set -euo pipefail

PRISMATIC_HOME="${PRISMATIC_HOME:-/home/ubuntu}"
HEARTBEAT_FILE="${PRISMATIC_HOME}/.prismatic/run/heartbeat.pid"
MAX_AGE_SECONDS=120

mkdir -p "$(dirname "$HEARTBEAT_FILE")"

if [[ "${1:-}" == "--check" ]]; then
    if [[ ! -f "$HEARTBEAT_FILE" ]]; then
        echo "MISSING: heartbeat.pid not found at $HEARTBEAT_FILE"
        exit 1
    fi

    PID=$(cat "$HEARTBEAT_FILE")
    if [[ -z "$PID" ]]; then
        echo "CORRUPT: heartbeat.pid is empty"
        exit 1
    fi

    # Check if PID is alive (if not unknown)
    if [[ "$PID" != "unknown" ]]; then
        if ! kill -0 "$PID" 2>/dev/null; then
            echo "DEAD: PID $PID is not running"
            exit 1
        fi
    fi

    # Check file freshness (mtime)
    NOW_EPOCH=$(date +%s)
    if [[ "$(uname)" == "Darwin" ]]; then
        HEARTBEAT_EPOCH=$(stat -f %m "$HEARTBEAT_FILE")
    else
        HEARTBEAT_EPOCH=$(stat -c %Y "$HEARTBEAT_FILE")
    fi
    AGE=$((NOW_EPOCH - HEARTBEAT_EPOCH))

    if [[ $AGE -gt $MAX_AGE_SECONDS ]]; then
        echo "STALE: heartbeat is ${AGE}s old (max ${MAX_AGE_SECONDS}s)"
        exit 1
    fi

    echo "OK: PID $PID alive, heartbeat ${AGE}s ago"
    exit 0
fi

# Write mode
PID=$(systemctl --user show prismatic-dispatcher.service -p MainPID --value 2>/dev/null || echo "unknown")
echo "$PID" > "$HEARTBEAT_FILE"
echo "Heartbeat written: PID=$PID at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
