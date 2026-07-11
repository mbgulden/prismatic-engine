#!/usr/bin/env bash
# ==============================================================================
# Prismatic Engine — Canary Test Runner
# Runs integration validation against a mock Linear server to verify the
# dispatcher, label routing, and state transitions work correctly.
#
# Usage:
#   ./scripts/canary_test.sh              # Run all canary tests
#   ./scripts/canary_test.sh --verbose    # Run with verbose output
# ==============================================================================
set -euo pipefail

VERBOSE=false
if [[ "${1:-}" == "--verbose" ]]; then
    VERBOSE=true
fi

CANARY_PID=""
REPORT_PATH="/tmp/canary_test_report.json"
PASSED=0
FAILED=0

cleanup() {
    if [[ -n "$CANARY_PID" ]] && kill -0 "$CANARY_PID" 2>/dev/null; then
        kill "$CANARY_PID" 2>/dev/null || true
        wait "$CANARY_PID" 2>/dev/null || true
    fi
    rm -f "$REPORT_PATH"
}
trap cleanup EXIT

log() {
    if $VERBOSE; then
        echo "  $*"
    fi
}

pass() {
    PASSED=$((PASSED + 1))
    echo "  ✅ $1"
}

fail() {
    FAILED=$((FAILED + 1))
    echo "  ❌ $1"
}

# ── Phase 1: Environment Check ──────────────────────────
echo "🧪 [Prismatic Canary] Environment check..."

# Verify Python is available
if command -v python3 &>/dev/null; then
    pass "python3 available"
else
    fail "python3 not found"
fi

# Verify the mock server script exists
if [[ -f scripts/mock_linear_server.py ]]; then
    pass "mock_linear_server.py exists"
else
    fail "mock_linear_server.py not found"
fi

# Verify pre-commit hook is installed
if [[ -d .git ]]; then
    if [[ -x .git/hooks/pre-commit ]]; then
        pass "pre-commit hook installed"
    else
        fail "pre-commit hook not installed"
    fi
else
    pass "pre-commit hook skipped (no .git repo)"
fi

# Verify pre-push hook is installed
if [[ -d .git ]]; then
    if [[ -x .git/hooks/pre-push ]]; then
        pass "pre-push hook installed"
    else
        fail "pre-push hook not installed"
    fi
else
    pass "pre-push hook skipped (no .git repo)"
fi

# ── Phase 2: Mock Server Startup ────────────────────────
echo "🧪 [Prismatic Canary] Starting mock Linear server..."

CANARY_PORT=9001
python3 scripts/mock_linear_server.py --port "$CANARY_PORT" &
CANARY_PID=$!
sleep 1

# Verify server is running
if kill -0 "$CANARY_PID" 2>/dev/null; then
    pass "mock server started (PID $CANARY_PID)"
else
    fail "mock server failed to start"
    echo "{\"status\": \"aborted\", \"reason\": \"mock server failed\"}" > "$REPORT_PATH"
    exit 1
fi

# ── Phase 3: API Integration Tests ──────────────────────
echo "🧪 [Prismatic Canary] Running API integration tests..."

# Test: labels query
log "Testing labels query..."
LABEL_RESPONSE=$(curl -s -X POST "http://127.0.0.1:$CANARY_PORT/graphql" \
    -H "Content-Type: application/json" \
    -d '{"query": "{ issueLabels { nodes { id name } } }"}' 2>&1 || echo "CURL_FAILED")

if echo "$LABEL_RESPONSE" | grep -q "agent:ned"; then
    pass "labels query returns agent:ned"
else
    fail "labels query failed: ${LABEL_RESPONSE:0:100}"
fi

# Test: issue query
log "Testing issue query..."
ISSUE_RESPONSE=$(curl -s -X POST "http://127.0.0.1:$CANARY_PORT/graphql" \
    -H "Content-Type: application/json" \
    -d '{"query": "{ team(id: \"test\") { issues { nodes { id identifier } } } }"}' 2>&1 || echo "CURL_FAILED")

if echo "$ISSUE_RESPONSE" | grep -q "NED-101"; then
    pass "issue query returns NED-101"
else
    fail "issue query failed: ${ISSUE_RESPONSE:0:100}"
fi

# Test: mutation
log "Testing mutation..."
MUTATION_RESPONSE=$(curl -s -X POST "http://127.0.0.1:$CANARY_PORT/graphql" \
    -H "Content-Type: application/json" \
    -d '{"query": "mutation { issueUpdate(id: \"issue-101\", input: { stateId: \"state-ip-id\" }) { success } }"}' 2>&1 || echo "CURL_FAILED")

if echo "$MUTATION_RESPONSE" | grep -q '"success": true'; then
    pass "mutation returns success"
else
    fail "mutation failed: ${MUTATION_RESPONSE:0:100}"
fi

# ── Phase 4: Pre-Commit Hook Test ───────────────────────
echo "🧪 [Prismatic Canary] Testing pre-commit hook..."

if [[ -d .git ]]; then
    if "${VERBOSE}"; then
        bash scripts/pre-commit-hook.sh && pass "pre-commit hook runs" || fail "pre-commit hook failed"
    else
        bash scripts/pre-commit-hook.sh > /dev/null 2>&1 && pass "pre-commit hook runs" || fail "pre-commit hook failed"
    fi
else
    pass "pre-commit hook test skipped (no .git repo)"
fi

# ── Phase 5: Report ─────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════"
echo "🧪 Canary Test Results"
echo "═══════════════════════════════════════════════════════"
echo "  Passed: $PASSED"
echo "  Failed: $FAILED"
echo ""

# Write JSON report
cat > "$REPORT_PATH" << JSONEOF
{
    "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "passed": $PASSED,
    "failed": $FAILED,
    "total": $((PASSED + FAILED)),
    "result": "$([ $FAILED -eq 0 ] && echo "PASS" || echo "FAIL")"
}
JSONEOF

if [[ $FAILED -eq 0 ]]; then
    echo "✅ All canary tests passed."
    exit 0
else
    echo "❌ $FAILED test(s) failed. See $REPORT_PATH for details."
    exit 1
fi
