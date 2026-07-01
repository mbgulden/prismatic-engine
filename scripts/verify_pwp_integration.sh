#!/usr/bin/env bash
# verify_pwp_integration.sh — runs in CI / pre-deploy / cron
# Verifies that the critical Phase D + Epic 1 work is present in the tree.
# Exits non-zero if anything is missing — this guards against auto-reverts
# like the GRO-3035 ned-cron incident on 2026-07-01.

set -e

REPO_ROOT="${1:-/home/ubuntu/work/prismatic-engine}"
cd "$REPO_ROOT" || exit 1

echo "=== Prismatic Engine integration verification ==="
echo

FAILED=0

check_file() {
  if [ -f "$1" ]; then
    echo "  OK   $2 ($1)"
  else
    echo "  MISSING $2 ($1)"
    FAILED=$((FAILED + 1))
  fi
}

check_grep() {
  if grep -q "$2" "$1" 2>/dev/null; then
    echo "  OK   $3 (found in $1)"
  else
    echo "  MISSING $3 (not found in $1)"
    FAILED=$((FAILED + 1))
  fi
}

# Phase D + Epic 1 files
check_file "prismatic/gateway/server.py" "Gateway server"
check_file "prismatic/curator/lane.py" "Curator lane"
check_file "prismatic/curator/dispatcher.py" "Curator dispatcher"
check_file "prismatic/supervisor/recovery.py" "Bounded supervisor pool"
check_file "scripts/linear_relabel.py" "Linear relabel script"

# Endpoints
check_grep "prismatic/gateway/server.py" "def curator_health" "/curator/health endpoint"
check_grep "prismatic/gateway/server.py" "def events_recent" "/events/recent endpoint"
check_grep "prismatic/gateway/server.py" "def events_bus_stats" "/events/bus-stats endpoint"
check_grep "prismatic/gateway/server.py" "_check_observability_auth" "observability auth middleware"
check_grep "prismatic/gateway/server.py" "JSONResponse" "JSONResponse import"

# Curator logic
check_grep "prismatic/curator/lane.py" "def tag_event" "tag_event() rule engine"
check_grep "prismatic/curator/lane.py" "sqlite3.connect" "SQLite persistence (sqlite3 import + connect call)"
check_grep "prismatic/curator/lane.py" "render_digest" "digest rendering"
check_grep "prismatic/curator/dispatcher.py" "LaneBudgetTracker" "budget tracker"
check_grep "prismatic/curator/dispatcher.py" "decide_dispatch" "dispatch decision"
check_grep "prismatic/supervisor/recovery.py" "class SupervisorPool" "bounded pool class"
check_grep "prismatic/supervisor/recovery.py" "_reap_zombies" "zombie reaper"

# Tests
test_count=$(find . -name "test_*.py" -not -path "*/.venv_dev/*" -not -path "*/__pycache__/*" | wc -l)
echo
if [ "$test_count" -ge 3 ]; then
  echo "  OK   $test_count test files found"
else
  echo "  WARN only $test_count test files found (expected >= 3)"
fi

# Live service health (only if running on this host)
if command -v systemctl >/dev/null 2>&1; then
  echo
  echo "=== Live service check ==="
  for svc in prismatic-gateway prismatic-consumer prismatic-curator; do
    if systemctl is-active "$svc" >/dev/null 2>&1; then
      echo "  OK   $svc is active"
    else
      echo "  WARN $svc is not active"
    fi
  done
  if curl -s -o /dev/null -w '%{http_code}' http://localhost:9000/curator/health 2>/dev/null | grep -q 200; then
    echo "  OK   /curator/health returns 200"
  else
    echo "  WARN /curator/health not returning 200"
  fi
fi

echo
if [ "$FAILED" -gt 0 ]; then
  echo "=== FAILED: $FAILED check(s) missing ==="
  exit 1
else
  echo "=== ALL CHECKS PASSED ==="
  exit 0
fi
