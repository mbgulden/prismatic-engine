#!/usr/bin/env bash
# ==============================================================================
# Prismatic Engine — Auto-Rollback Script
# Reverts the active symlink to the previous stable version and restarts the
# dispatcher daemon. Triggered by watchdog.sh after 3 consecutive failures.
#
# Usage:
#   scripts/rollback.sh                     # Rollback to previous
#   scripts/rollback.sh --to <version>      # Rollback to specific version
#   scripts/rollback.sh --status            # Show current active/previous
# ==============================================================================
set -euo pipefail

PRISMATIC_HOME="${PRISMATIC_HOME:-/home/ubuntu}"
ACTIVE_LINK="${PRISMATIC_HOME}/.prismatic/active"
PREVIOUS_LINK="${PRISMATIC_HOME}/.prismatic/previous"
VERSIONS_DIR="${PRISMATIC_HOME}/.prismatic/versions"
ROLLBACK_LOG="${PRISMATIC_HOME}/.prismatic/logs/rollback.log"
SERVICE_NAME="prismatic-dispatcher.service"

mkdir -p "$(dirname "$ROLLBACK_LOG")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$ROLLBACK_LOG"
}

# ── Status ──
if [[ "${1:-}" == "--status" ]]; then
    echo "Active:  $(readlink -f "$ACTIVE_LINK" 2>/dev/null || echo 'MISSING')"
    echo "Previous: $(readlink -f "$PREVIOUS_LINK" 2>/dev/null || echo 'MISSING')"
    echo ""
    echo "Available versions:"
    if [[ -d "$VERSIONS_DIR" ]]; then
        ls -1 "$VERSIONS_DIR" 2>/dev/null || echo "  (none)"
    fi
    echo ""
    systemctl --user status "$SERVICE_NAME" --no-pager -l 2>/dev/null || echo "  (service status unavailable)"
    exit 0
fi

# ── Rollback ──
if [[ "${1:-}" == "--to" ]]; then
    TARGET_VERSION="${2:-}"
    if [[ -z "$TARGET_VERSION" ]]; then
        log "ERROR: --to requires a version name"
        exit 1
    fi
    TARGET_PATH="${VERSIONS_DIR}/${TARGET_VERSION}"
    if [[ ! -d "$TARGET_PATH" ]]; then
        log "ERROR: version $TARGET_VERSION not found at $TARGET_PATH"
        exit 1
    fi
else
    # Default: use previous link target
    TARGET_PATH=$(readlink -f "$PREVIOUS_LINK" 2>/dev/null || echo "")
    if [[ -z "$TARGET_PATH" || ! -d "$TARGET_PATH" ]]; then
        log "ERROR: no previous version available for rollback"
        log "  Previous link: $PREVIOUS_LINK → $(readlink "$PREVIOUS_LINK" 2>/dev/null || echo 'N/A')"
        log "  Available versions:"
        ls -1 "$VERSIONS_DIR" 2>/dev/null | while read -r v; do log "    $v"; done
        exit 1
    fi
fi

CURRENT_ACTIVE=$(readlink -f "$ACTIVE_LINK" 2>/dev/null || echo "")

log "══════════════════════════════════════════════"
log "🚨 ROLLBACK INITIATED"
log "  Current active:  $CURRENT_ACTIVE"
log "  Rolling back to: $TARGET_PATH"
log "══════════════════════════════════════════════"

# 1. Save current active as previous (for audit trail)
if [[ -n "$CURRENT_ACTIVE" ]]; then
    echo "$CURRENT_ACTIVE" > "${PRISMATIC_HOME}/.prismatic/previous_rollback_target"
    log "  Saved previous target: $CURRENT_ACTIVE"
fi

# 2. Atomic symlink swap
if ln -sfn "$TARGET_PATH" "$ACTIVE_LINK"; then
    log "  ✅ Active symlink updated: $(readlink "$ACTIVE_LINK")"
else
    log "  ❌ Failed to update active symlink"
    exit 1
fi

# 3. Update previous link
ln -sfn "$TARGET_PATH" "$PREVIOUS_LINK"
log "  Previous link updated: $(readlink "$PREVIOUS_LINK")"

# 4. Restart daemon
log "  Restarting $SERVICE_NAME..."
if systemctl --user restart "$SERVICE_NAME" 2>/dev/null; then
    sleep 2
    if systemctl --user is-active --quiet "$SERVICE_NAME"; then
        log "  ✅ Service restarted and active"
    else
        log "  ⚠️  Service restarted but NOT active — checking logs..."
        systemctl --user status "$SERVICE_NAME" --no-pager -l 2>/dev/null | tail -10 | while read -r line; do
            log "    $line"
        done
    fi
else
    log "  ❌ Failed to restart $SERVICE_NAME"
    exit 1
fi

# 5. Write recovery heartbeat
PID=$(systemctl --user show "$SERVICE_NAME" -p MainPID --value 2>/dev/null || echo "unknown")
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "$PID $TIMESTAMP" > "${PRISMATIC_HOME}/.prismatic/run/heartbeat.pid"
log "  Heartbeat written: PID=$PID"

log "══════════════════════════════════════════════"
log "✅ ROLLBACK COMPLETE — system recovered"
log "══════════════════════════════════════════════"
