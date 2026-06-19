"""Python log rotation for engine logs (GRO-2058)

A pure-Python log rotation cron that:
- Reads size of every *.log under ~/.prismatic/logs/, ~/.gemini/logs/, and
  ~/.hermes/profiles/*/logs/
- If a log exceeds threshold (default 10 MB), rotates it:
    foo.log → foo.log.1 (newest)
    foo.log.1 → foo.log.2
    ...
    foo.log.{N-1} → foo.log.N (oldest, deleted if > max-keep)
- Compresses rotated logs with gzip (saves disk)
- Writes JSONL audit trail
- Idempotent: safe to re-run

Schedule: daily 02:00 UTC (cron)
Threshold: 10 MB per file
Max keep: 5 rotated copies per file
Compression: gzip

Usage:
    python3 rotate-engine-logs.py [--dry-run] [--threshold-mb 10] [--max-keep 5]
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LOG_DIRS = [
    Path("/home/ubuntu/.prismatic/logs"),
    Path("/home/ubuntu/.gemini/logs"),
    Path("/home/ubuntu/.gemini/antigravity-cli/logs"),
]
# Per-profile logs
PROFILES_DIR = Path("/home/ubuntu/.hermes/profiles")
for profile_dir in PROFILES_DIR.iterdir() if PROFILES_DIR.exists() else []:
    log_dir = profile_dir / "logs"
    if log_dir.is_dir():
        LOG_DIRS.append(log_dir)

AUDIT_LOG = Path("/home/ubuntu/.prismatic/logs/log-rotation-audit.jsonl")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--threshold-mb", type=int, default=10)
    p.add_argument("--max-keep", type=int, default=5)
    p.add_argument("--quiet", action="store_true")
    return p.parse_args()


def should_rotate(log: Path, threshold_bytes: int) -> bool:
    """Return True if log exceeds threshold AND exists and is a regular file."""
    if not log.is_file():
        return False
    return log.stat().st_size >= threshold_bytes


def rotate_one(log: Path, max_keep: int, dry_run: bool) -> dict:
    """Rotate log: foo.log.N-1 → foo.log.N, ..., foo.log.1 → foo.log.2, foo.log → foo.log.1.

    Compression: the rotated log.1 is gzipped to log.1.gz.

    Idempotent: if log.1 already exists (e.g., from a previous rotation
    that wasn't completed), we still rotate forward — the old log.1 is
    moved to log.2 first, etc.
    """
    result = {
        "log": str(log),
        "rotated": False,
        "compressed": False,
        "size_before": log.stat().st_size,
        "size_after_compression": 0,
    }

    # Cascade: move N-1 → N, N-2 → N-1, ..., 1 → 2
    for i in range(max_keep, 0, -1):
        if i == max_keep:
            # Drop the oldest if it exists
            oldest = log.with_suffix(f".log.{max_keep}.gz")
            if oldest.exists() and not dry_run:
                oldest.unlink()
        elif i == 1:
            # log.1.gz → log.2.gz (rename)
            src_gz = log.with_suffix(".log.1.gz")
            dst_gz = log.with_suffix(f".log.{i + 1}.gz")
            if src_gz.exists() and not dry_run:
                src_gz.rename(dst_gz)
        else:
            # log.N-1.gz → log.N.gz
            src_gz = log.with_suffix(f".log.{i}.gz")
            dst_gz = log.with_suffix(f".log.{i + 1}.gz")
            if src_gz.exists() and not dry_run:
                src_gz.rename(dst_gz)

    # Now rotate foo.log → foo.log.1 (uncompressed), then compress
    rotated_path = log.with_suffix(".log.1")
    if not dry_run:
        shutil.move(str(log), str(rotated_path))
        # Compress in place
        with open(rotated_path, "rb") as f_in:
            compressed_data = gzip.compress(f_in.read())
        compressed_path = log.with_suffix(".log.1.gz")
        with open(compressed_path, "wb") as f_out:
            f_out.write(compressed_data)
        rotated_path.unlink()
        result["size_after_compression"] = len(compressed_data)
        result["rotated"] = True
        result["compressed"] = True

    return result


def main():
    args = parse_args()
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    threshold_bytes = args.threshold_mb * 1024 * 1024
    now_iso = datetime.now(timezone.utc).isoformat()

    candidates = []
    for log_dir in LOG_DIRS:
        if not log_dir.exists():
            continue
        for log in log_dir.rglob("*.log"):
            # Skip already-rotated files (*.log.1.gz, *.log.2.gz, etc.)
            if any(suffix in log.name for suffix in [".log.1", ".log.2", ".log.3", ".log.4", ".log.5"]):
                continue
            if should_rotate(log, threshold_bytes):
                candidates.append(log)

    if not args.quiet:
        print(f"[rotate-logs] {len(candidates)} file(s) exceed {args.threshold_mb}MB threshold")
        for log in candidates:
            size_mb = log.stat().st_size / 1024 / 1024
            print(f"  {log}: {size_mb:.2f} MB")

    if not candidates:
        return 0

    rotated_count = 0
    total_freed_bytes = 0
    audit_lines = []
    for log in candidates:
        result = rotate_one(log, args.max_keep, args.dry_run)
        result["ts"] = now_iso
        audit_lines.append(json.dumps(result))
        if result.get("rotated"):
            rotated_count += 1
            saved = result["size_before"] - result["size_after_compression"]
            if saved > 0:
                total_freed_bytes += saved

    if not args.dry_run:
        with AUDIT_LOG.open("a") as f:
            for line in audit_lines:
                f.write(line + "\n")

    if not args.quiet:
        print(f"[rotate-logs] rotated {rotated_count} file(s), freed ~{total_freed_bytes / 1024 / 1024:.1f} MB")

    return 0


if __name__ == "__main__":
    sys.exit(main())