#!/usr/bin/env bash
# ==============================================================================
# Prismatic Engine — SQLite VACUUM Cron (GRO-2059)
# ==============================================================================
# Runs VACUUM on every SQLite DB under ~/.prismatic/ to reclaim disk space
# and keep query plans optimal.
#
# VACUUM rebuilds the entire DB file, so:
#   - Requires free disk space equal to ~2x the current DB size (for the
#     temp copy)
#   - Briefly locks each DB exclusively (other connections will block)
#   - For LARGE DBs (>100MB) this can take 30-60 seconds per file
#
# Schedule: weekly Sunday 03:00 UTC (in cron)
# Skips DBs that are <100 KB (negligible benefit, not worth the lock)
# Skips DBs currently in WAL mode with active writers (best-effort check)
# ==============================================================================

set -euo pipefail

# State root can be overridden via env (engine reads PRISMATIC_STATE_DIR)
# Logs always go to the canonical ~/.prismatic/logs (not $HOME-relative, which
# breaks when the script runs under a Hermes profile with a fake $HOME)
STATE_ROOT="${PRISMATIC_STATE_DIR:-/home/ubuntu/.prismatic/db}"
METRICS_DIR="/home/ubuntu/.prismatic/logs"
LOG_FILE="$METRICS_DIR/vacuum-cron.log"

mkdir -p "$METRICS_DIR"

log() {
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "$LOG_FILE"
}

log "=== vacuum-cron start ==="
log "STATE_ROOT=$STATE_ROOT"

# Discover DBs: walk all *.db files under STATE_ROOT
# Use find with -name; skip if directory doesn't exist
if [[ ! -d "$STATE_ROOT" ]]; then
    log "STATE_ROOT does not exist; nothing to vacuum"
    exit 0
fi

DB_FILES=$(find "$STATE_ROOT" -type f -name "*.db" 2>/dev/null | sort -u)
DB_COUNT=$(echo "$DB_FILES" | grep -c . || true)
log "Found $DB_COUNT SQLite DB(s) under $STATE_ROOT"

if [[ "$DB_COUNT" -eq 0 ]]; then
    log "No DBs found; exiting"
    exit 0
fi

# Size threshold: skip DBs under 100 KB (negligible benefit)
MIN_SIZE_BYTES=102400  # 100 KB

# Output: JSONL log of per-DB actions for trend tracking
VACUUM_REPORT="$METRICS_DIR/vacuum-report.jsonl"
> "$VACUUM_REPORT"

TOTAL_FREED_BYTES=0
TOTAL_DBS_VACUUMED=0
TOTAL_DBS_SKIPPED=0

for db in $DB_FILES; do
    name=$(basename "$db")
    size_before=$(stat -c%s "$db" 2>/dev/null || echo 0)

    # Skip tiny DBs
    if [[ "$size_before" -lt "$MIN_SIZE_BYTES" ]]; then
        log "SKIP $name: $size_before bytes (< $MIN_SIZE_BYTES)"
        echo "{\"db\":\"$name\",\"action\":\"skip\",\"reason\":\"too_small\",\"bytes_before\":$size_before}" >> "$VACUUM_REPORT"
        TOTAL_DBS_SKIPPED=$((TOTAL_DBS_SKIPPED + 1))
        continue
    fi

    # Check for active WAL writers (best-effort heuristic).
    # If a -wal file exists and was modified in the last 60s, another
    # process is actively writing. Skip to avoid lock contention.
    wal_file="${db}-wal"
    if [[ -f "$wal_file" ]]; then
        wal_mtime=$(stat -c%Y "$wal_file" 2>/dev/null || echo 0)
        now=$(date +%s)
        age=$((now - wal_mtime))
        if [[ "$age" -lt 60 ]]; then
            log "SKIP $name: WAL active (modified ${age}s ago)"
            echo "{\"db\":\"$name\",\"action\":\"skip\",\"reason\":\"wal_active\",\"bytes_before\":$size_before}" >> "$VACUUM_REPORT"
            TOTAL_DBS_SKIPPED=$((TOTAL_DBS_SKIPPED + 1))
            continue
        fi
    fi

    # Check free disk space (need ~2x DB size for VACUUM temp copy)
    avail_bytes=$(df --output=avail -B1 "$db" 2>/dev/null | tail -1 | tr -d ' ')
    needed_bytes=$((size_before * 2))
    if [[ "$avail_bytes" -lt "$needed_bytes" ]]; then
        avail_mb=$((avail_bytes / 1024 / 1024))
        size_mb=$((size_before / 1024 / 1024))
        log "SKIP $name: insufficient disk space (need ${size_mb}MB x2, have ${avail_mb}MB)"
        echo "{\"db\":\"$name\",\"action\":\"skip\",\"reason\":\"low_disk\",\"bytes_before\":$size_before}" >> "$VACUUM_REPORT"
        TOTAL_DBS_SKIPPED=$((TOTAL_DBS_SKIPPED + 1))
        continue
    fi

    # Run VACUUM via python3 (sqlite3 CLI may not be installed everywhere)
    log "VACUUM $name (size: $((size_before / 1024)) KB)..."
    start_time=$(date +%s)
    if python3 -c "
import sqlite3, sys
try:
    conn = sqlite3.connect('$db')
    conn.execute('VACUUM')
    conn.close()
    sys.exit(0)
except Exception as e:
    print(f'VACUUM error: {e}', file=sys.stderr)
    sys.exit(1)
" 2>> "$LOG_FILE"; then
        end_time=$(date +%s)
        duration=$((end_time - start_time))
        size_after=$(stat -c%s "$db" 2>/dev/null || echo 0)
        freed=$((size_before - size_after))
        if [[ "$freed" -lt 0 ]]; then freed=0; fi
        TOTAL_FREED_BYTES=$((TOTAL_FREED_BYTES + freed))
        TOTAL_DBS_VACUUMED=$((TOTAL_DBS_VACUUMED + 1))
        log "  done: $((size_after / 1024)) KB (freed $((freed / 1024)) KB in ${duration}s)"
        echo "{\"db\":\"$name\",\"action\":\"vacuum\",\"bytes_before\":$size_before,\"bytes_after\":$size_after,\"freed\":$freed,\"duration_s\":$duration}" >> "$VACUUM_REPORT"
    else
        log "  FAILED (see $LOG_FILE for details)"
        echo "{\"db\":\"$name\",\"action\":\"failed\",\"bytes_before\":$size_before}" >> "$VACUUM_REPORT"
    fi
done

# Summary
log "=== vacuum-cron complete ==="
log "  DBs vacuumed: $TOTAL_DBS_VACUUMED"
log "  DBs skipped:   $TOTAL_DBS_SKIPPED"
log "  Bytes freed:   $TOTAL_FREED_BYTES ($((TOTAL_FREED_BYTES / 1024 / 1024)) MB)"

# Optional: post a single summary line to stdout for cron output capture
echo "[vacuum-cron] $TOTAL_DBS_VACUUMED vacuumed, $TOTAL_DBS_SKIPPED skipped, $((TOTAL_FREED_BYTES / 1024 / 1024)) MB freed"