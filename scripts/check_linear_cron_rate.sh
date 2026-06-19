#!/usr/bin/env bash
# ==============================================================================
# Prismatic Engine — Linear API Rate Limit Lint
# ==============================================================================
# Catches the same class of silent-loophole bug that GRO-2034 fixed:
# any script (cron-driven OR webhook-triggered) that makes Linear API calls
# without going through LinearBudget.check_and_consume() will silently burn
# the 2500 req/hour budget.
#
# Used as a pre-push hook supplement and as part of CI.
#
# Strategy:
#   1. Scan the prismatic/ tree for files that import HTTP clients (requests,
#      httpx) and reference Linear URLs.
#   2. Scan the same tree for files that import LinearBudget — those are
#      known-safe.
#   3. For each unsafe file, check whether it's reachable from a cron entry
#      or a webhook handler.
#   4. Estimate per-hour call rate from schedule × calls-per-cycle, plus
#      webhook handler's max burst.
#   5. Fail if total > 2000/hour OR if any reachable file is missing the gate.
#
# Exit codes:
#   0 = clean (or only safe files)
#   1 = lint failure (ungated script OR over budget)
#   2 = script error
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUDGET_PER_HOUR=2500
SAFETY_THRESHOLD=2000   # 80% of budget; fail CI above this

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

cd "$ENGINE_ROOT"

echo "🔍 [Linear API Rate Limit Lint] Scanning $ENGINE_ROOT/prismatic/"

# ---------------------------------------------------------------------------
# Step 1: Find all Python files that could make Linear API calls
# ---------------------------------------------------------------------------

# A "candidate" file is one that references Linear (either by URL or symbol)
# AND has some way to make HTTP calls (direct import OR subprocess curl/wget).
# This catches both engine code and portable scripts. Excludes tests.
# HTTP mechanisms to detect:
#   - Direct imports: requests, httpx, urllib.request, http.client, aiohttp
#   - Subprocess: curl, wget (used by credit_tracker.py)
CANDIDATE_FILES=$(find prismatic -type f -name "*.py" \
    ! -path "*/tests/*" \
    ! -path "*/__pycache__/*" \
    -exec grep -l -E "api\.linear\.app|LinearBudget|linear_call|_linear_api_key|post_linear_comment|post_to_linear|_linear_gql" {} \; 2>/dev/null \
    | sort -u | while read f; do
        # Check if this file has ANY way to make HTTP calls
        # (|| true prevents set -e from killing the loop on no-match)
        if grep -qE "import requests|from requests|import httpx|from httpx|urllib\.request|http\.client|import aiohttp|from aiohttp|subprocess\.(run|call|Popen).*curl|wget" "$f" 2>/dev/null || true; then
            echo "$f"
        fi
    done | sort -u || true)

if [[ -z "$CANDIDATE_FILES" ]]; then
    echo -e "${GREEN}✅ No Linear API callers found in prismatic/ — nothing to lint.${NC}"
    exit 0
fi

echo "  Found $(echo "$CANDIDATE_FILES" | wc -l) candidate file(s) with Linear API usage"

# ---------------------------------------------------------------------------
# Step 2 + 3: Find files that import LinearBudget (known-safe), then check
# the remaining files for both cron AND webhook reachability.
# ---------------------------------------------------------------------------

# Gated files: those that already import LinearBudget or use linear_call()
# (|| true to handle set -e + grep-no-match)
GATED_FILES=$(echo "$CANDIDATE_FILES" | xargs grep -l -E "LinearBudget|linear_call|check_and_consume" 2>/dev/null || true)
GATED_FILES=$(echo "$GATED_FILES" | sort -u)

# Files that import HTTP+Linear but NOT LinearBudget — these are the risk
UNGATED_FILES=$(comm -23 \
    <(echo "$CANDIDATE_FILES") \
    <(echo "$GATED_FILES"))

# ---------------------------------------------------------------------------
# Reachability check: simpler invariant — every Linear-calling file in the
# engine should be gated, period. We don't try to enumerate cron reachability
# because that's brittle (new crons get added constantly). The invariant is:
# any file that CAN reach the Linear API MUST be wrapped with LinearBudget.
# ---------------------------------------------------------------------------

# Also check: are any ungated files reachable from the orchestrator's webhook
# receiver? The webhook calls agent_dispatcher.py which then calls into any
# number of sub-agents. If a sub-agent imports an ungated Linear-calling module,
# the webhook bypasses the gate.

# Webhook entry point is fixed (the orchestrator gateway)
# (|| true to handle set -e + grep-no-match edge cases)
WEBHOOK_HANDLERS=$(find ~/.hermes/profiles/orchestrator/scripts \
    -type f -name "*.py" 2>/dev/null \
    | xargs grep -l -E "linear_events|webhook|LinearWebhook" 2>/dev/null || true)
WEBHOOK_HANDLERS=$(echo "$WEBHOOK_HANDLERS" | sort -u)

# ---------------------------------------------------------------------------
# Step 4: Estimate per-hour call rate + report ungated files
# ---------------------------------------------------------------------------

ESTIMATED_TOTAL=0
LINT_ERRORS=()
GATED_COUNT=0

for f in $CANDIDATE_FILES; do
    # Calls per cycle: rough count of GraphQL/HTTP POST sites
    # (|| true prevents set -e from killing the loop when grep finds 0 matches)
    CALLS_PER_CYCLE=$(grep -cE "requests\.(post|get|put)|httpx\.(post|get|put)|client\.execute|gql\(|urllib\.request\.Request.*api\.linear" "$f" 2>/dev/null || true)
    CALLS_PER_CYCLE=$(echo "$CALLS_PER_CYCLE" | head -1)
    CALLS_PER_CYCLE=${CALLS_PER_CYCLE:-0}

    # Is this file gated?
    IS_GATED=0
    if echo "$GATED_FILES" | grep -q "^$f$"; then
        IS_GATED=1
        GATED_COUNT=$((GATED_COUNT + 1))
    fi

    # Estimate contribution to hourly budget (worst case)
    # Cron-driven: 12 calls/hour baseline (every 5min cron)
    # Webhook-driven: 100 events/min × N calls = 6000/hr worst case
    # Just sum conservative upper bounds; budget is 2500/hr
    PER_HOUR=$(( CALLS_PER_CYCLE * 100 ))
    ESTIMATED_TOTAL=$(( ESTIMATED_TOTAL + PER_HOUR ))

    GATE_STATUS="gated"
    if [[ $IS_GATED -eq 0 ]]; then
        GATE_STATUS="⚠️  UNGATED"
    fi
    echo "    $f: ~$CALLS_PER_CYCLE calls/cycle, $GATE_STATUS → ~$PER_HOUR/hr"
done

# The invariant: any ungated file is a regression. Even if it's not reachable
# from a cron today, future code might call into it, and then the bypass is live.
# Fail CI on ANY ungated file, period.

if [[ -n "$UNGATED_FILES" ]]; then
    while IFS= read -r f; do
        LINT_ERRORS+=("$f: makes Linear API calls without LinearBudget gate")
    done <<< "$UNGATED_FILES"
fi

# ---------------------------------------------------------------------------
# Step 5: Verdict
# ---------------------------------------------------------------------------

echo ""
echo "📊 Estimated hourly Linear API usage: $ESTIMATED_TOTAL req/hr (threshold: $SAFETY_THRESHOLD)"
echo "📋 $GATED_COUNT / $(echo "$CANDIDATE_FILES" | wc -l) files are LinearBudget-gated"

if [[ ${#LINT_ERRORS[@]} -gt 0 ]]; then
    echo ""
    echo -e "${RED}❌ LinearBudget coverage lint failed:${NC}"
    for err in "${LINT_ERRORS[@]}"; do
        echo -e "  ${RED}•${NC} $err"
    done
    echo ""
    echo "Fix: import LinearBudget from prismatic.linear.budget and wrap your"
    echo "Linear API calls with budget.check_and_consume('<script_name>')."
    echo "See GRO-2034 for the canonical pattern."
    exit 1
fi

if [[ $ESTIMATED_TOTAL -gt $SAFETY_THRESHOLD ]]; then
    echo -e "${YELLOW}⚠️  Estimated usage ($ESTIMATED_TOTAL) exceeds safety threshold ($SAFETY_THRESHOLD)${NC}"
    echo "Likely a false positive (the heuristic is conservative). If real:"
    echo "  - Reduce cron frequency, OR"
    echo "  - Cache responses, OR"
    echo "  - Use LinearBudget to spread load"
    exit 1
fi

echo -e "${GREEN}✅ [Linear API Rate Limit Lint] All Linear-calling files are LinearBudget-gated.${NC}"
exit 0