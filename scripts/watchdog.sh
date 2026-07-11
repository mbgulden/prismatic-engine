#!/usr/bin/env bash
# ==============================================================================
# Prismatic Engine — Watchdog Monitor
# Runs periodically (via systemd timer) to check dispatcher health.
# Triggers rollback after 3 consecutive failures within a 120s window.
#
# Usage:
#   scripts/watchdog.sh                     # Run health check
#   scripts/watchdog.sh --reset             # Clear failure counter
#   scripts/watchdog.sh --status            # Show current state
# ==============================================================================
set -euo pipefail

PRISMATIC_HOME="${PRISMATIC_HOME:-/home/ubuntu}"
STATE_DIR="${PRISMATIC_HOME}/.prismatic/run"
FAILURE_FILE="${STATE_DIR}/watchdog_failures.txt"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HEARTBEAT_SCRIPT="${SCRIPT_DIR}/heartbeat.sh"
ROLLBACK_SCRIPT="${SCRIPT_DIR}/rollback.sh"
MAX_CONSECUTIVE_FAILURES=3
HEALTH_PORT="${PRISMATIC_PORT:-9000}"

mkdir -p "$STATE_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${STATE_DIR}/watchdog.log"
}

# ── Reset ──
if [[ "${1:-}" == "--reset" ]]; then
    rm -f "$FAILURE_FILE"
    log "Failure counter reset."
    exit 0
fi

# ── Status ──
if [[ "${1:-}" == "--status" ]]; then
    if [[ -f "$FAILURE_FILE" ]]; then
        COUNT=$(cat "$FAILURE_FILE")
        echo "Failures: $COUNT/$MAX_CONSECUTIVE_FAILURES"
    else
        echo "Failures: 0/$MAX_CONSECUTIVE_FAILURES (clean)"
    fi
    if [[ -f "${STATE_DIR}/heartbeat.pid" ]]; then
        cat "${STATE_DIR}/heartbeat.pid"
    fi
    exit 0
fi

# ── Health Check ──
FAILURES=0
if [[ -f "$FAILURE_FILE" ]]; then
    FAILURES=$(cat "$FAILURE_FILE")
fi

HEALTHY=false
SERVICE_ACTIVE=false
HEARTBEAT_FRESH=false

# Source of truth: the live gateway /health endpoint operators use.
# systemd and heartbeat.pid are diagnostic signals only; they must not
# turn a reachable gateway into a false-red report.
HEALTH_URL="http://localhost:${HEALTH_PORT}/health"
HEALTH_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$HEALTH_URL" 2>/dev/null || echo "000")
if [[ "$HEALTH_RESPONSE" == "200" ]]; then
    log "CHECK 1/3: live gateway health endpoint $HEALTH_URL → $HEALTH_RESPONSE — PASS (source=live_gateway)"
    HEALTHY=true
else
    log "CHECK 1/3: live gateway health endpoint $HEALTH_URL → $HEALTH_RESPONSE — FAIL (source=live_gateway)"
fi

# Diagnostic 1: systemd service status.
if systemctl --user is-active --quiet prismatic-dispatcher.service 2>/dev/null; then
    SERVICE_ACTIVE=true
    log "CHECK 2/3: systemd service prismatic-dispatcher.service active — PASS (diagnostic=service)"
else
    if $HEALTHY; then
        log "CHECK 2/3: systemd service prismatic-dispatcher.service inactive/unavailable — DIAGNOSTIC ONLY (live gateway healthy)"
    else
        log "CHECK 2/3: systemd service prismatic-dispatcher.service inactive/unavailable — FAIL (diagnostic=service)"
    fi
fi

# Diagnostic 2: heartbeat file freshness.
hb_result=$(bash "$HEARTBEAT_SCRIPT" --check 2>&1) && hb_status=$? || hb_status=$?
if [[ $hb_status -eq 0 ]]; then
    HEARTBEAT_FRESH=true
    log "CHECK 3/3: heartbeat file is fresh — PASS (diagnostic=heartbeat; $hb_result)"
else
    if $HEALTHY; then
        log "CHECK 3/3: heartbeat file check failed: $hb_result — DIAGNOSTIC ONLY (live gateway healthy)"
    else
        log "CHECK 3/3: heartbeat file check failed: $hb_result — FAIL (diagnostic=heartbeat)"
    fi

    # Repair the diagnostic heartbeat when the service really is the owner.
    if $SERVICE_ACTIVE; then
        log "  (systemd says active, refreshing heartbeat diagnostic)"
        bash "$HEARTBEAT_SCRIPT" 2>/dev/null || true
    fi
fi

# ── Decision ──
if $HEALTHY; then
    # Healthy — reset failure counter
    if [[ -f "$FAILURE_FILE" ]]; then
        OLD_COUNT=$(cat "$FAILURE_FILE")
        rm -f "$FAILURE_FILE"
        log "✅ Healthy — failure counter reset (was $OLD_COUNT/$MAX_CONSECUTIVE_FAILURES)"
    else
        log "✅ Healthy — no failures recorded"
    fi
    exit 0
else
    # Unhealthy — increment counter
    FAILURES=$((FAILURES + 1))
    echo "$FAILURES" > "$FAILURE_FILE"
    log "⚠️  Unhealthy — failure $FAILURES/$MAX_CONSECUTIVE_FAILURES"

    if [[ $FAILURES -ge $MAX_CONSECUTIVE_FAILURES ]]; then
        log "🚨 THRESHOLD REACHED — triggering rollback..."
        if [[ -x "$ROLLBACK_SCRIPT" ]]; then
            bash "$ROLLBACK_SCRIPT"
        else
            log "❌ rollback.sh not found or not executable at $ROLLBACK_SCRIPT"
        fi
        rm -f "$FAILURE_FILE"
    fi
    exit 1
fi
