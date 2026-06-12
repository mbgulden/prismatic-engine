#!/usr/bin/env bash
# ============================================================================
# Prismatic Engine — Pipeline Artifact Verification Script
# ============================================================================
# Checks that all INGEST pipeline artifacts and governance files exist on disk.
# Run: bash scripts/verify-pipeline.sh
# Part of INGEST-5 (GRO-1467) — Automate the pipeline.
#
# Exit codes:
#   0 = all artifacts present
#   1 = one or more artifacts missing
# ============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MISSING=0
FOUND=0

check() {
    local label="$1"
    local path="$2"
    if [[ -e "$path" ]]; then
        echo "  ✅ $label: $path"
        FOUND=$((FOUND + 1))
    else
        echo "  ❌ MISSING: $label — $path"
        MISSING=$((MISSING + 1))
    fi
}

echo "============================================"
echo " Prismatic Engine — Pipeline Verification"
echo " $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "============================================"

# ── INGEST Pipeline Artifacts ───────────────────────────────
echo ""
echo "── INGEST Pipeline Artifacts ──"

check "INGEST-1: Index"              "/tmp/doc-ingestion/01-index.md"
check "INGEST-2: Extracted blocks"   "/tmp/doc-ingestion/02-extracted-blocks"

# ── INGEST-3: Skills ────────────────────────────────────────
echo ""
echo "── INGEST-3: Generated Skills ──"

check "7-step loop skill"            "$REPO_ROOT/SKILLS/prismatic-7-step-loop/SKILL.md"
check "Alchemy quality gates"        "$REPO_ROOT/SKILLS/alchemy-quality-gates/SKILL.md"
check "Portability skill"            "$REPO_ROOT/SKILLS/prismatic-portability/SKILL.md"
check "Lane governance skill"        "$REPO_ROOT/SKILLS/lane-governance/SKILL.md"
check "Agent soul template"          "$REPO_ROOT/SKILLS/agent-soul-template/SKILL.md"
check "Plugin development skill"     "$REPO_ROOT/SKILLS/prismatic-plugin-development/SKILL.md"
check "Skills README"                "$REPO_ROOT/SKILLS/README.md"

# ── INGEST-3: Templates ─────────────────────────────────────
echo ""
echo "── INGEST-3: Generated Templates ──"

check "Cron job templates"           "$REPO_ROOT/templates/cron/cron-job-templates.md"
check "Linear issue templates"       "$REPO_ROOT/templates/linear/issue-templates.md"

# ── Governance Files ────────────────────────────────────────
echo ""
echo "── Governance Files ──"

check "PRISMATIC_ENGINE.yaml"        "$REPO_ROOT/PRISMATIC_ENGINE.yaml"
check "SOUL.md"                      "$REPO_ROOT/SOUL.md"
check "COMMIT_CONVENTION.md"         "$REPO_ROOT/COMMIT_CONVENTION.md"

# ── Automation Infrastructure ───────────────────────────────
echo ""
echo "── Automation Infrastructure ──"

check "Pre-push hook (script)"       "$REPO_ROOT/scripts/pre-push-hook.py"
check "Pre-push hook (symlink)"      "$REPO_ROOT/.git/hooks/pre-push"
check "Swarm lock CLI"               "$REPO_ROOT/prismatic/lock.py"
check "Event dispatcher"             "$REPO_ROOT/prismatic/dispatcher.py"
check "Pipeline router"              "$REPO_ROOT/prismatic/router.py"
check "Agent config"                 "$REPO_ROOT/config/agents.yaml"
check "Verification script"          "$REPO_ROOT/scripts/verify-pipeline.sh"

# ── Agent Swarm Ops Integration ─────────────────────────────
echo ""
echo "── Agent Swarm Ops Integration ──"

check "Swarm lock DB"                "/home/ubuntu/.antigravity/swarm_locks.json"
check "Swarm CLI"                    "/home/ubuntu/.antigravity/swarm.js"

# ── INGEST-4 (future) ───────────────────────────────────────
echo ""
echo "── INGEST-4: Integration Guide (not yet produced) ──"

check "Integration guide (future)"   "/tmp/doc-ingestion/04-integration-guide.md"

# ── Summary ─────────────────────────────────────────────────
echo ""
echo "============================================"
TOTAL=$((FOUND + MISSING))
echo " Results: $FOUND / $TOTAL artifacts found"
if [[ $MISSING -eq 0 ]]; then
    echo " Status:  ✅ ALL ARTIFACTS PRESENT"
    exit 0
else
    echo " Status:  ❌ $MISSING artifact(s) missing"
    exit 1
fi
