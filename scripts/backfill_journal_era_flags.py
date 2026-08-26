#!/usr/bin/env python3
"""One-shot backfill of the ``legacy`` era flag over the journal event index (G2).

Rows written before the incremental-cursor era (~2026-07-24) lack an
``idempotency_key`` and are not trustworthy as deduplicated evidence. This
script stamps ``"legacy": true`` onto those rows and ``"legacy": false`` onto
keyed rows, so readers can say "history, not proof" at the seam.

Idempotent: re-running is a no-op. Uses prismatic.journal._ensure_era_flag so
the backfill and the live ingest path share one definition.

Usage:
    python scripts/backfill_journal_era_flags.py            # in-place
    python scripts/backfill_journal_era_flags.py --dry-run  # report only
"""

from __future__ import annotations

import argparse
import json

from prismatic.journal import JournalConfig, _ensure_era_flag


def backfill_index(config: JournalConfig, dry_run: bool = False) -> dict:
    index_dir = config.journal_root / ".index"
    if not index_dir.exists():
        return {"files": 0, "rows": 0, "legacy_added": 0, "modern_added": 0}
    files = 0
    rows = 0
    legacy_added = 0
    modern_added = 0
    for path in sorted(index_dir.glob("events-*.json")):
        if path.name == "events.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        changed = False
        for row in data:
            if not isinstance(row, dict):
                continue
            rows += 1
            before = "legacy" in row
            _ensure_era_flag(row)
            if before:
                continue
            changed = True
            if row.get("legacy"):
                legacy_added += 1
            else:
                modern_added += 1
        files += 1
        if changed and not dry_run:
            path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return {
        "files": files,
        "rows": rows,
        "legacy_added": legacy_added,
        "modern_added": modern_added,
        "dry_run": dry_run,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="backfill-journal-era-flags")
    parser.add_argument(
        "--dry-run", action="store_true", help="Report counts without writing"
    )
    args = parser.parse_args()
    config = JournalConfig.from_env()
    result = backfill_index(config, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
