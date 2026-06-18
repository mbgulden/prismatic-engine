#!/usr/bin/env bash
# ============================================================
# Prismatic Engine / AGY Swarm — canary_deploy.sh
# ============================================================
# Automates Stage 1 portability setup, checks env vars, creates
# compatibility symlinks, generates config files, and verifies credentials.
# ============================================================

set -euo pipefail

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info() { printf "${CYAN}%s${NC}\n" "$*"; }
ok()   { printf "${GREEN}✓ %s${NC}\n" "$*"; }
warn() { printf "${YELLOW}⚠ %s${NC}\n" "$*"; }
err()  { printf "${RED}✗ %s${NC}\n" "$*"; }

# --- Load Environment ---
ENV_FILE=".env"
if [ -f "$ENV_FILE" ]; then
    info "Loading environment from $ENV_FILE..."
    # Export all vars defined in .env
    set -a
    source "$ENV_FILE"
    set +a
else
    warn "No .env file found. Using existing environment variables or defaults."
fi

# --- Helper to expand tilde (~) ---
expand_path() {
    local path="$1"
    echo "${path/#\~/$HOME}"
}

# --- Set Default Paths ---
HERMES_ROOT=$(expand_path "${HERMES_ROOT:-$HOME/.hermes}")
HERMES_PROFILE="${HERMES_PROFILE:-orchestrator}"
AGY_SKILLS_DIR=$(expand_path "${AGY_SKILLS_DIR:-$HERMES_ROOT/profiles/$HERMES_PROFILE/skills}")
AGY_BRAIN_DIR=$(expand_path "${AGY_BRAIN_DIR:-$HERMES_ROOT/profiles/$HERMES_PROFILE/home/.gemini/antigravity-cli/brain}")
AGY_CONFIG_DIR=$(expand_path "${AGY_CONFIG_DIR:-$HOME/.antigravity}")
AGY_CONFIG_FILE=$(expand_path "${AGY_CONFIG_FILE:-$AGY_CONFIG_DIR/config.json}")
PRISMATIC_HOME=$(expand_path "${PRISMATIC_HOME:-$HOME/work/prismatic-engine}")
SOVEREIGN_HOME=$(expand_path "${SOVEREIGN_HOME:-$HOME/work/SovereignSentinel}")
WORK_ROOT=$(expand_path "${WORK_ROOT:-$HOME/work}")

SSE_GATEWAY_HOST="${SSE_GATEWAY_HOST:-localhost}"
SSE_GATEWAY_PORT="${SSE_GATEWAY_PORT:-8644}"
SSE_GATEWAY_URL="${SSE_GATEWAY_URL:-http://$SSE_GATEWAY_HOST:$SSE_GATEWAY_PORT}"

AGY_LOG_LEVEL="${AGY_LOG_LEVEL:-INFO}"
AGY_AUDIT_OUTPUT_DIR="${AGY_AUDIT_OUTPUT_DIR:-/tmp/}"

COMPAT_MODE="${COMPAT_MODE:-1}"
SMOKE_TEST="${SMOKE_TEST:-0}"

# Create Log File
LOG_FILE="$AGY_AUDIT_OUTPUT_DIR/deploy.log"
exec > >(tee -a "$LOG_FILE") 2>&1

info "=== Starting canary_deploy.sh ==="
info "Logs will be appended to $LOG_FILE"

# ============================================================
# Phase 1: Preflight Checks
# ============================================================
info "[Phase 1] Preflight Checks..."
PASSED_CHECKS=0
WARNINGS=0
FAILURES=0

# Check required credential env vars
for var in LINEAR_API_KEY GITHUB_TOKEN; do
    if [ -z "${!var:-}" ]; then
        warn "  [Phase 1] WARNING: Required credential variable $var is empty."
        WARNINGS=$((WARNINGS + 1))
    else
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
    fi
done
ok "Preflight checks completed."

# ============================================================
# Phase 2: Directory Setup
# ============================================================
info "[Phase 2] Setting up directories..."
for dir in "$HERMES_ROOT" "$AGY_CONFIG_DIR" "$WORK_ROOT" "$AGY_BRAIN_DIR"; do
    if [ ! -d "$dir" ]; then
        mkdir -p "$dir"
        info "  Created directory: $dir"
    fi
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
done
ok "Directory setup completed."

# ============================================================
# Phase 3: Symlink Creation
# ============================================================
info "[Phase 3] Creating compatibility symlinks..."
if [ "$COMPAT_MODE" = "1" ]; then
    # Create $COMPAT_BASE if it does not exist (needs sudo, so we check if writable or exist)
    # Default to the current user's $HOME — operators can override via COMPAT_BASE env var
    COMPAT_BASE="${COMPAT_BASE:-$HOME}"
    if [ ! -d "${COMPAT_BASE}" ]; then
        warn "  [Phase 3] ${COMPAT_BASE} does not exist on this machine. Skipping compatibility symlinks."
        WARNINGS=$((WARNINGS + 1))
    else
        COMPAT_HERMES="${COMPAT_HERMES:-$COMPAT_BASE/.hermes}"
        COMPAT_WORK="${COMPAT_WORK:-$COMPAT_BASE/work}"
        COMPAT_ANTIGRAVITY="${COMPAT_ANTIGRAVITY:-$COMPAT_BASE/.antigravity}"

        # Symlink .hermes to $HERMES_ROOT
        if [ ! -e "${COMPAT_HERMES}" ]; then
            ln -s "$HERMES_ROOT" "${COMPAT_HERMES}"
            info "  Linked ${COMPAT_HERMES} -> $HERMES_ROOT"
        fi
        # Symlink work to $WORK_ROOT
        if [ ! -e "${COMPAT_WORK}" ]; then
            ln -s "$WORK_ROOT" "${COMPAT_WORK}"
            info "  Linked ${COMPAT_WORK} -> $WORK_ROOT"
        fi
        # Symlink .antigravity to $AGY_CONFIG_DIR
        if [ ! -e "${COMPAT_ANTIGRAVITY}" ]; then
            ln -s "$AGY_CONFIG_DIR" "${COMPAT_ANTIGRAVITY}"
            info "  Linked ${COMPAT_ANTIGRAVITY} -> $AGY_CONFIG_DIR"
        fi
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
    fi
else
    info "  Compatibility mode disabled. Skipping symlinks."
fi
ok "Symlink phase completed."

# ============================================================
# Phase 4: Config Generation
# ============================================================
info "[Phase 4] Generating config.json and credentials.json..."
# Generate config.json if not exists
if [ ! -f "$AGY_CONFIG_FILE" ]; then
    cat <<EOF > "$AGY_CONFIG_FILE"
{
    "model_bindings": {
        "agent:agy": "${AGY_MODEL_DEFAULT:-gemini-3.1-flash}",
        "agent:agy-lite": "${AGY_MODEL_FLASH:-gemini-3.1-flash-lite}",
        "agent:agy-pro": "${AGY_MODEL_PRO:-gemini-3.1-pro-high}"
    },
    "fallback_chain": [
        "${AGY_MODEL_DEFAULT:-gemini-3.1-flash}",
        "${AGY_MODEL_FLASH:-gemini-3.1-flash-lite}"
    ],
    "default_model": "${AGY_MODEL_DEFAULT:-gemini-3.1-flash}"
}
EOF
    info "  Generated $AGY_CONFIG_FILE"
fi

# Generate scoped credentials.json in Hermes active profile if variables set
HERMES_CREDENTIALS="$HERMES_ROOT/profiles/$HERMES_PROFILE/credentials.json"
if [ ! -f "$HERMES_CREDENTIALS" ]; then
    mkdir -p "$(dirname "$HERMES_CREDENTIALS")"
    cat <<EOF > "$HERMES_CREDENTIALS"
{
  "_meta": {
    "version": "1.0.0",
    "created": "$(date +%Y-%m-%d)",
    "classification": "ORCHESTRATOR-ONLY",
    "note": "Auto-generated by canary_deploy.sh"
  },
  "credentials": {
    "LINEAR_API_KEY": {
      "value": "${LINEAR_API_KEY:-}",
      "scope": ["lane_general", "lane_4", "lane_5", "lane_6"],
      "description": "Linear API key",
      "source_env": "LINEAR_API_KEY"
    },
    "LINEAR_OAUTH_TOKEN": {
      "value": "${LINEAR_OAUTH_TOKEN:-}",
      "scope": ["lane_general", "lane_4", "lane_5", "lane_6"],
      "description": "Linear OAuth token",
      "source_env": "LINEAR_OAUTH_TOKEN"
    },
    "GITHUB_PAT_KEY": {
      "value": "${GITHUB_TOKEN:-}",
      "scope": ["lane_general", "lane_4", "lane_5", "lane_6"],
      "description": "GitHub token",
      "source_env": "GITHUB_TOKEN"
    },
    "OPENAI_API_KEY": {
      "value": "${OPENAI_API_KEY:-}",
      "scope": ["lane_general"],
      "description": "OpenAI API key",
      "source_env": "OPENAI_API_KEY"
    },
    "TELEGRAM_BOT_TOKEN": {
      "value": "${TELEGRAM_BOT_TOKEN:-}",
      "scope": ["lane_general", "lane_4", "lane_5", "lane_6"],
      "description": "Telegram bot token",
      "source_env": "TELEGRAM_BOT_TOKEN"
    }
  }
}
EOF
    chmod 600 "$HERMES_CREDENTIALS"
    info "  Generated $HERMES_CREDENTIALS with secure permissions (600)"
fi
PASSED_CHECKS=$((PASSED_CHECKS + 1))
ok "Config generation phase completed."

# ============================================================
# Phase 5: Credential Verification
# ============================================================
info "[Phase 5] Verifying credentials..."
if [ -n "${LINEAR_API_KEY:-}" ]; then
    info "  Verifying Linear API key..."
    LINEAR_HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST https://api.linear.app/graphql \
      -H "Authorization: $LINEAR_API_KEY" \
      -H "Content-Type: application/json" \
      -d '{"query": "{ viewer { id name } }"}' || echo "000")
    if [ "$LINEAR_HTTP_STATUS" = "200" ]; then
        ok "    Linear API Key is valid (status: 200)"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
    else
        warn "    Linear API Key verification returned status $LINEAR_HTTP_STATUS"
        WARNINGS=$((WARNINGS + 1))
    fi
else
    warn "  Linear API key not configured, skipping verification."
    WARNINGS=$((WARNINGS + 1))
fi

if [ -n "${GITHUB_TOKEN:-}" ]; then
    info "  Verifying GitHub Token..."
    GITHUB_HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/user || echo "000")
    if [ "$GITHUB_HTTP_STATUS" = "200" ]; then
        ok "    GitHub Token is valid (status: 200)"
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
    else
        warn "    GitHub Token verification returned status $GITHUB_HTTP_STATUS"
        WARNINGS=$((WARNINGS + 1))
    fi
else
    warn "  GitHub Token not configured, skipping verification."
    WARNINGS=$((WARNINGS + 1))
fi
ok "Credential verification phase completed."

# ============================================================
# Phase 6: Health Check
# ============================================================
info "[Phase 6] Running health checks..."
# Check if skills directory has files
if [ -d "$AGY_SKILLS_DIR" ]; then
    SKILLS_COUNT=$(find "$AGY_SKILLS_DIR" -name "*.md" | wc -l)
    info "  Found $SKILLS_COUNT skills in $AGY_SKILLS_DIR"
    PASSED_CHECKS=$((PASSED_CHECKS + 1))
else
    warn "  Skills directory not found: $AGY_SKILLS_DIR"
    WARNINGS=$((WARNINGS + 1))
fi

# Serve check (optional)
if [ "$SMOKE_TEST" = "1" ]; then
    info "  Running serve smoke test..."
    if command -v prismatic-engine &> /dev/null; then
        prismatic-engine serve --dry-run
        PASSED_CHECKS=$((PASSED_CHECKS + 1))
    else
        warn "    prismatic-engine CLI not found, skipping serve test."
        WARNINGS=$((WARNINGS + 1))
    fi
fi
ok "Health check phase completed."

# ============================================================
# Phase 7: Status Report
# ============================================================
info "[Phase 7] Deployment Status Report"
echo "--------------------------------------------------"
info "  Passed Checks: $PASSED_CHECKS"
if [ $WARNINGS -gt 0 ]; then
    warn "  Warnings:      $WARNINGS"
fi
if [ $FAILURES -gt 0 ]; then
    err "  Failures:      $FAILURES"
else
    ok "  Deployment Status: SUCCESSFUL"
fi
echo "--------------------------------------------------"

# Write status file
STATUS_FILE="$AGY_CONFIG_DIR/deploy-status.json"
cat <<EOF > "$STATUS_FILE"
{
  "status": "$([ $FAILURES -eq 0 ] && echo "SUCCESS" || echo "FAILED")",
  "timestamp": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "passed_checks": $PASSED_CHECKS,
  "warnings": $WARNINGS,
  "failures": $FAILURES
}
EOF

info "Deployment status saved to $STATUS_FILE"
info "=== Deployment Script Finished ==="
