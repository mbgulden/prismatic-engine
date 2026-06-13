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
# File format: PID TIMESTAMP_ISO8601
# Example: 1230107 2026-06-13T06:17:00Z
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

    read -r PID TIMESTAMP < "$HEARTBEAT_FILE" || true
    if [[ -z "$PID" || -z "$TIMESTAMP" ]]; then
        echo "CORRUPT: heartbeat.pid has invalid format"
        exit 1
    fi

    # Check if PID is alive
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "DEAD: PID $PID is not running"
        exit 1
    fi

    # Check timestamp freshness
    NOW_EPOCH=$(date +%s)
    HEARTBEAT_EPOCH=$(date -d "$TIMESTAMP" +%s 2>/dev/null || echo 0)
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
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "$PID $TIMESTAMP" > "$HEARTBEAT_FILE"
echo "Heartbeat written: PID=$PID at $TIMESTAMP"
