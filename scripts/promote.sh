#!/usr/bin/env bash
# ==============================================================================
# Prismatic Engine — Promote Pipeline
# Full 6-step pipeline from dev workspace → stable runtime:
#   1. Export archive from dev workspace
#   2. Build sandbox with new version
#   3. Run sandbox integration tests (canary)
#   4. Atomic symlink swap (promote)
#   5. Restart daemon
#   6. Watchdog health verification
#
# Usage:
#   scripts/promote.sh                       # Full pipeline (prompts for confirm)
#   scripts/promote.sh --yes                 # Non-interactive (skip confirm)
#   scripts/promote.sh --dry-run             # Validate without promoting
#   scripts/promote.sh --version v0.2.0      # Specify version tag
# ==============================================================================
set -euo pipefail

PRISMATIC_HOME="${PRISMATIC_HOME:-/home/ubuntu}"
WORKSPACE_DIR="${PRISMATIC_HOME}/work/prismatic-engine"
ACTIVE_LINK="${PRISMATIC_HOME}/.prismatic/active"
PREVIOUS_LINK="${PRISMATIC_HOME}/.prismatic/previous"
VERSIONS_DIR="${PRISMATIC_HOME}/.prismatic/versions"
SANDBOX_DIR="${PRISMATIC_HOME}/.prismatic/sandbox"
STABLE_VENV="${PRISMATIC_HOME}/.prismatic/venv_stable"
PROMOTE_LOG="${PRISMATIC_HOME}/.prismatic/logs/promote.log"
CANARY_SCRIPT="${WORKSPACE_DIR}/scripts/canary_test.sh"
HEARTBEAT_SCRIPT="${WORKSPACE_DIR}/scripts/heartbeat.sh"
SERVICE_NAME="prismatic-dispatcher.service"

YES_MODE=false
DRY_RUN=false
VERSION=""

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes|-y) YES_MODE=true ;;
        --dry-run|-n) DRY_RUN=true ;;
        --version|-v) VERSION="$2"; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
    shift
done

if [[ -z "$VERSION" ]]; then
    # Auto-generate version from git
    cd "$WORKSPACE_DIR"
    GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    VERSION="v0.1.0-${GIT_SHA}"
fi

BUILD_DIR="${SANDBOX_DIR}/build-${VERSION}"
SANDBOX_VENV="${SANDBOX_DIR}/venv"

mkdir -p "$(dirname "$PROMOTE_LOG")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$PROMOTE_LOG"
}

banner() {
    log ""
    log "═══════════════════════════════════════════════════════"
    log "  $*"
    log "═══════════════════════════════════════════════════════"
}

fail() {
    log "❌ $*"
    if [[ "$DRY_RUN" != "true" ]]; then
        exit 1
    fi
}

# ── Pre-flight ──
banner "Prismatic Promote Pipeline — Version $VERSION"
log "PRISMATIC_HOME: $PRISMATIC_HOME"
log "Workspace: $WORKSPACE_DIR"
log "Dry run: $DRY_RUN"

# Check workspace exists
if [[ ! -d "$WORKSPACE_DIR" ]]; then
    fail "Workspace not found: $WORKSPACE_DIR"
fi

# Check we're on main branch
cd "$WORKSPACE_DIR"
CURRENT_BRANCH=$(git branch --show-current)
if [[ "$CURRENT_BRANCH" != "main" && "$CURRENT_BRANCH" != "master" ]]; then
    log "⚠️  Not on main branch: $CURRENT_BRANCH"
    if [[ "$YES_MODE" != "true" ]]; then
        echo -n "Continue anyway? [y/N] "
        read -r answer
        if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
            exit 0
        fi
    fi
fi

# ── Step 1: Export Archive ──
banner "Step 1/6: Export Archive"
EXPORT_PATH="/tmp/prismatic-export-${VERSION}.tar.gz"

# Stash any pre-existing dirty files
PRE_EXISTING_DIRTY=false
if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then
    log "Stashing pre-existing changes..."
    git stash push -m "pre-promote: auto-stash $(date -u +%Y-%m-%dT%H:%M:%SZ)" 2>/dev/null || true
    PRE_EXISTING_DIRTY=true
fi

# Export the prismatic source
if $DRY_RUN; then
    log "  [DRY RUN] Would create archive from git HEAD"
else
    git archive --format=tar.gz --prefix="prismatic_engine/" -o "$EXPORT_PATH" HEAD
    log "  ✅ Archive created: $EXPORT_PATH ($(du -h "$EXPORT_PATH" | cut -f1))"
fi

# Restore stashed changes
if $PRE_EXISTING_DIRTY; then
    git stash pop 2>/dev/null || log "  ⚠️  Could not restore stashed changes (check git stash list)"
fi

# ── Step 2: Build Sandbox ──
banner "Step 2/6: Build Sandbox"

if $DRY_RUN; then
    log "  [DRY RUN] Would build sandbox at $BUILD_DIR"
else
    # Clean previous sandbox
    rm -rf "$BUILD_DIR" 2>/dev/null || true
    mkdir -p "$BUILD_DIR"

    # Extract archive
    tar -xzf "$EXPORT_PATH" -C "$BUILD_DIR"
    log "  Extracted archive to $BUILD_DIR"

    # Install into sandbox venv
    if [[ ! -d "$SANDBOX_VENV" ]]; then
        python3 -m venv "$SANDBOX_VENV"
        log "  Created sandbox venv: $SANDBOX_VENV"
    fi

    "$SANDBOX_VENV/bin/pip" install --upgrade pip build -q 2>&1 | tail -1
    cd "$BUILD_DIR/prismatic_engine"
    if "$SANDBOX_VENV/bin/pip" install -e . -q 2>&1 | tail -3; then
        log "  ✅ Sandbox build installed"
    else
        fail "Sandbox build failed"
    fi

    # Copy to versions dir for symlink target
    VERSION_DIR="${VERSIONS_DIR}/${VERSION}"
    rm -rf "$VERSION_DIR" 2>/dev/null || true
    cp -r "$BUILD_DIR/prismatic_engine" "$VERSION_DIR"
    log "  ✅ Version copied to $VERSION_DIR"
fi

# ── Step 3: Canary Tests ──
banner "Step 3/6: Canary Integration Tests"

if $DRY_RUN; then
    log "  [DRY RUN] Would run canary tests"
else
    if [[ -x "$CANARY_SCRIPT" ]]; then
        if bash "$CANARY_SCRIPT" 2>&1 | tail -20 | while read -r line; do log "  $line"; done; then
            log "  ✅ Canary tests passed"
        else
            fail "Canary tests failed — aborting promote"
        fi
    else
        log "  ⚠️  canary_test.sh not found — skipping (use --force to override)"
    fi
fi

# ── Step 4: Atomic Symlink Swap ──
banner "Step 4/6: Atomic Promotion (Symlink Swap)"

CURRENT_ACTIVE=$(readlink -f "$ACTIVE_LINK" 2>/dev/null || echo "NONE")
log "  Current active: $CURRENT_ACTIVE"

if $DRY_RUN; then
    log "  [DRY RUN] Would swap: active → $VERSION_DIR"
    log "  [DRY RUN] Would save: previous → $CURRENT_ACTIVE"
else
    # Save current as previous
    if [[ -n "$CURRENT_ACTIVE" && "$CURRENT_ACTIVE" != "NONE" ]]; then
        ln -sfn "$CURRENT_ACTIVE" "$PREVIOUS_LINK"
        echo "$CURRENT_ACTIVE" > "${PRISMATIC_HOME}/.prismatic/previous_rollback_target"
        log "  Previous saved: $CURRENT_ACTIVE"
    fi

    # Atomic swap
    ln -sfn "$VERSION_DIR" "$ACTIVE_LINK"
    log "  ✅ Active symlink updated: $(readlink "$ACTIVE_LINK")"
fi

# ── Step 5: Restart Daemon ──
banner "Step 5/6: Restart Daemon"

if $DRY_RUN; then
    log "  [DRY RUN] Would restart $SERVICE_NAME"
else
    log "  Restarting $SERVICE_NAME..."
    if systemctl --user restart "$SERVICE_NAME" 2>/dev/null; then
        sleep 2
        if systemctl --user is-active --quiet "$SERVICE_NAME"; then
            log "  ✅ Service restarted and active"
        else
            fail "Service failed to start after restart — rollback needed"
        fi
    else
        fail "Failed to restart $SERVICE_NAME"
    fi

    # Write heartbeat
    bash "$HEARTBEAT_SCRIPT" 2>/dev/null || true
    log "  Heartbeat written"
fi

# ── Step 6: Watchdog Health Verification ──
banner "Step 6/6: Watchdog Health Verification"

if $DRY_RUN; then
    log "  [DRY RUN] Would verify health endpoint for 30s"
else
    # Quick health check loop (30s)
    HEALTHY=false
    for i in $(seq 1 6); do
        if curl -s -o /dev/null -w "%{http_code}" --max-time 5 "http://localhost:${PRISMATIC_PORT:-9000}/health" 2>/dev/null | grep -q "200"; then
            log "  ✅ Health check passed (attempt $i)"
            HEALTHY=true
            break
        fi
        log "  ⏳ Health check attempt $i/6 — waiting..."
        sleep 5
    done

    if $HEALTHY; then
        log "  ✅ Service healthy after promotion"
    else
        log "  ⚠️  Service did not become healthy within 30s"
        log "  ⚠️  Watchdog will trigger rollback if it stabilizes in failure"
    fi
fi

# ── Cleanup ──
rm -f "$EXPORT_PATH"
if $DRY_RUN; then
    rm -rf "$BUILD_DIR" 2>/dev/null || true
fi

banner "✅ PROMOTE PIPELINE COMPLETE — $VERSION"
log "  Active: $(readlink "$ACTIVE_LINK" 2>/dev/null || echo 'N/A')"
log "  Previous: $(readlink "$PREVIOUS_LINK" 2>/dev/null || echo 'N/A')"
