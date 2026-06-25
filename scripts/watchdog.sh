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
HEARTBEAT_SCRIPT="${PRISMATIC_HOME}/work/prismatic-engine/scripts/heartbeat.sh"
ROLLBACK_SCRIPT="${PRISMATIC_HOME}/work/prismatic-engine/scripts/rollback.sh"
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

# Check 1: systemd service status
# Production service is a system-level unit, not the old user-level prismatic-dispatcher.service.
# The old check created constant false FAIL lines even when /health was green.
if systemctl is-active --quiet prismatic-gateway.service 2>/dev/null; then
    log "CHECK 1/3: prismatic-gateway.service is active — PASS"
else
    log "CHECK 1/3: prismatic-gateway.service is NOT active — FAIL"
fi

# Check 2: heartbeat file freshness
if bash "$HEARTBEAT_SCRIPT" --check 2>/dev/null; then
    log "CHECK 2/3: heartbeat is fresh — PASS"
else
    hb_result=$(bash "$HEARTBEAT_SCRIPT" --check 2>&1 || true)
    log "CHECK 2/3: heartbeat check failed: $hb_result — FAIL"

    # Also refresh heartbeat if the gateway itself is healthy
    if systemctl is-active --quiet prismatic-gateway.service 2>/dev/null; then
        log "  (prismatic-gateway.service active, updating heartbeat)"
        bash "$HEARTBEAT_SCRIPT" 2>/dev/null || true
    fi
fi

# Check 3: health endpoint
HEALTH_URL="http://localhost:${HEALTH_PORT}/health"
HEALTH_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$HEALTH_URL" 2>/dev/null || echo "000")
if [[ "$HEALTH_RESPONSE" == "200" ]]; then
    log "CHECK 3/3: health endpoint $HEALTH_URL → $HEALTH_RESPONSE — PASS"
    HEALTHY=true
else
    log "CHECK 3/3: health endpoint $HEALTH_URL → $HEALTH_RESPONSE — FAIL"
fi

# Check 4: systemd only (if health endpoint unavailable)
if ! $HEALTHY; then
    if systemctl is-active --quiet prismatic-gateway.service 2>/dev/null; then
        log "CHECK (fallback): prismatic-gateway.service active despite health endpoint failure"
        HEALTHY=true
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
