#!/usr/bin/env bash
# ==============================================================================
# test_ned_memories_bak_sweep.sh
#
# Behavioral test for ~/.hermes/profiles/ned/scripts/ned_memories_bak_sweep.sh.
# Exercises the script in a sandboxed fake HOME so we never touch the real
# memories dir. Each test gets a fresh fake HOME for isolation.
#
# Lane: agent:ned (quality/qgate)
# Created: GRO-3124
#
# Pass criteria:
#   1. dry-run: counts stale files without deleting them; exit 0
#   2. --apply: deletes files >7d; exit 0; non-stale files preserved
#   3. exit code 2 fires if files >7d still remain after --apply (post-condition)
#   4. script fails gracefully if MEM_DIR is missing
#
# Usage:
#   bash scripts/quality/test_ned_memories_bak_sweep.sh
#   bash scripts/quality/test_ned_memories_bak_sweep.sh --verbose
# ==============================================================================
set -uo pipefail

VERBOSE=false
if [[ "${1:-}" == "--verbose" ]]; then
    VERBOSE=true
fi

REAL_SCRIPT="$HOME/.hermes/profiles/ned/scripts/ned_memories_bak_sweep.sh"
PARENT_SANDBOX="$(mktemp -d /tmp/ned-bak-sweep-test-XXXXXX)"

# Trap sandbox cleanup — restore any 0555'd test dirs first so rm can succeed
trap 'chmod -R u+rwX "$PARENT_SANDBOX" 2>/dev/null; rm -rf "$PARENT_SANDBOX"' EXIT

PASS=0
FAIL=0
FAILED_TESTS=()

log() { $VERBOSE && echo "  $*" || true; }
pass() { PASS=$((PASS + 1)); echo "  ✅ $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  ❌ $1"; FAILED_TESTS+=("$1"); }

# Helper: create a `.bak-*` file with controlled mtime (in days ago)
make_bak() {
    local path="$1"
    local age_days="$2"
    touch "$path"
    if [[ "$age_days" -gt 0 ]]; then
        touch -d "${age_days} days ago" "$path"
    fi
}

# Helper: build a fresh fake $HOME for each test invocation. Echoes the
# fake_home path so callers can populate it before running the sweep.
# Usage: fake_home="$(fresh_fake_home)"
fresh_fake_home() {
    local fake_home
    fake_home="$(mktemp -d "$PARENT_SANDBOX/fakehome-XXXXXX")"
    mkdir -p "$fake_home/.hermes/profiles/ned/scripts"
    cp "$REAL_SCRIPT" "$fake_home/.hermes/profiles/ned/scripts/"
    echo "$fake_home"
}

# Helper: run the sweep against a given fake_home, with a populated memories dir.
# Populates memories dir from <mem_src> using `cp -rp` (preserves mtime).
# Usage: run_sweep <fake_home> <mem_src> <mode...>
run_sweep() {
    local fake_home="$1"
    local mem_src="$2"
    shift 2
    cp -rp "$mem_src" "$fake_home/.hermes/profiles/ned/memories"
    HOME="$fake_home" bash "$fake_home/.hermes/profiles/ned/scripts/ned_memories_bak_sweep.sh" "$@"
}

echo "🧪 [test_ned_memories_bak_sweep] parent sandbox: $PARENT_SANDBOX"

# ── Test 1: dry-run counts stale files without deleting ──────────────
echo ""
echo "▶ Test 1: dry-run counts stale files without deleting"
MEM_SRC="$PARENT_SANDBOX/t1-memories"
mkdir -p "$MEM_SRC"
make_bak "$MEM_SRC/MEMORY.md.bak-test-fresh"  1
make_bak "$MEM_SRC/MEMORY.md.bak-test-stale"  10
make_bak "$MEM_SRC/USER.md.bak-test-fresh"    3
FAKE="$(fresh_fake_home)"

run_sweep "$FAKE" "$MEM_SRC" > /tmp/ned-test-out 2>&1
RC=$?
OUT=$(cat /tmp/ned-test-out)
log "  dry-run output: $OUT"

# Files in source should be untouched
AFTER=$(find "$MEM_SRC" -name "*.bak-*" | wc -l)
if [[ $RC -eq 0 ]] && [[ "$AFTER" == "3" ]] && echo "$OUT" | grep -q "found=3"; then
    pass "dry-run: 3 files found, 0 deleted, source untouched, exit 0"
else
    fail "dry-run: rc=$RC expected 0, after=$AFTER expected 3, output=$OUT"
fi

# ── Test 2: --apply deletes stale, preserves fresh ────────────────────
echo ""
echo "▶ Test 2: --apply deletes >7d, preserves ≤7d"
MEM_SRC="$PARENT_SANDBOX/t2-memories"
mkdir -p "$MEM_SRC"
make_bak "$MEM_SRC/MEMORY.md.bak-test-fresh"  1
make_bak "$MEM_SRC/MEMORY.md.bak-test-stale"  10
make_bak "$MEM_SRC/USER.md.bak-test-fresh"    3
FAKE="$(fresh_fake_home)"

run_sweep "$FAKE" "$MEM_SRC" --apply > /tmp/ned-test-out 2>&1
RC=$?
OUT=$(cat /tmp/ned-test-out)
log "  apply output: $OUT"

REMAINING=$(find "$FAKE/.hermes/profiles/ned/memories" -name "*.bak-*" | wc -l)
if [[ $RC -eq 0 ]] && [[ "$REMAINING" == "2" ]]; then
    pass "apply: 2 fresh (≤7d) files remain in fake-home memories, exit 0"
else
    fail "apply: rc=$RC expected 0, remaining=$REMAINING expected 2, output=$OUT"
fi

# ── Test 3: post-condition alert (exit 2) when stale files remain ─────
echo ""
echo "▶ Test 3: post-condition exit code 2 when stale remain"
# Make the parent dir read-only so files inside cannot be deleted (more
# deterministic than chmod'ing individual files which root can bypass).
MEM_SRC="$PARENT_SANDBOX/t3-memories"
mkdir -p "$MEM_SRC"
make_bak "$MEM_SRC/MEMORY.md.bak-test-readonly-stale" 30
chmod 555 "$MEM_SRC"
FAKE="$(fresh_fake_home)"

run_sweep "$FAKE" "$MEM_SRC" --apply > /tmp/ned-test-out 2>&1
RC=$?
OUT=$(cat /tmp/ned-test-out)
log "  apply output: $OUT"

# Restore permissions so parent cleanup can rm -rf
chmod 755 "$MEM_SRC" 2>/dev/null || true

if [[ $RC -eq 2 ]]; then
    pass "post-condition: exit 2 fired (stale remains after delete blocked)"
elif [[ $RC -eq 0 ]]; then
    fail "post-condition: rc=0 but expected 2 (DAC bypass; chmod 555 ineffective)"
else
    fail "post-condition: unexpected rc=$RC"
fi

# ── Test 4: missing MEM_DIR handled gracefully ────────────────────────
echo ""
echo "▶ Test 4: missing MEM_DIR handled gracefully"
FAKE="$(fresh_fake_home)"
# Skip the population step — invoke the script directly without a memories dir.
HOME="$FAKE" bash "$FAKE/.hermes/profiles/ned/scripts/ned_memories_bak_sweep.sh" \
    > /tmp/ned-test-out 2>&1
RC=$?
OUT=$(cat /tmp/ned-test-out)
log "  output: $OUT"

if [[ $RC -eq 1 ]] && echo "$OUT" | grep -q "missing"; then
    pass "missing MEM_DIR: exit 1 with helpful error"
else
    fail "missing MEM_DIR: rc=$RC expected 1; output=$OUT"
fi

# ── Summary ────────────────────────────────────────────────────────────
echo ""
echo "======================================"
echo "✅ Passed: $PASS"
echo "❌ Failed: $FAIL"
if [[ $FAIL -gt 0 ]]; then
    echo "Failed tests:"
    for t in "${FAILED_TESTS[@]}"; do echo "  - $t"; done
    exit 1
fi
echo "All behavioral tests passed."
exit 0
