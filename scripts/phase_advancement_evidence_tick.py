#!/usr/bin/env python3
"""Evidence adapter for the phase-advancement daily tick (review-factory wiring).

Bridges the daily shadow-agreement JSON (an object with a ``records`` array)
into the evidence-pointers JSON the tick consumes, then invokes the tick's
``main()``. The tick is fail-closed: unreadable evidence -> exit 1, never an
assumed verdict. Filing a request is NOT executing — ``execute()`` still
needs the master switch + Michael's approval record + live evidence.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from prismatic.review_factory.phase_advancement import main

DEFAULT_AUDIT_DIR = Path("/home/ubuntu/.prismatic/audit")


def latest_agreement(audit_dir: Path) -> Path | None:
    files = sorted(audit_dir.glob("shadow-agreement-*.json"))
    return files[-1] if files else None


def extract_records_jsonl(agreement_path: Path, tmpdir: Path) -> Path:
    """Write the agreement's ``records`` array to a temp JSONL file."""
    data = json.loads(agreement_path.read_text(encoding="utf-8"))
    out = tmpdir / "shadow-records.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for rec in data.get("records", []):
            f.write(json.dumps(rec) + "\n")
    return out


def build_pointers(audit_dir: Path, tmpdir: Path) -> dict:
    """Build the evidence-pointers dict the tick consumes."""
    agreement = latest_agreement(audit_dir)
    if agreement is None:
        raise FileNotFoundError(f"no shadow-agreement-*.json in {audit_dir}")
    data = json.loads(agreement.read_text(encoding="utf-8"))
    records_jsonl = extract_records_jsonl(agreement, tmpdir)
    return {
        "shadow_records": str(records_jsonl),
        "shadow_signals": str(audit_dir / "shadow-decisions.jsonl"),
        "bad_merge_calls": int(data.get("bad_merge_calls", 0)),
    }


def run(audit_dir: Path = DEFAULT_AUDIT_DIR) -> int:
    tmpdir = Path(tempfile.mkdtemp(prefix="phase-evidence-"))
    pointers = build_pointers(audit_dir, tmpdir)
    pointers_path = tmpdir / "evidence-pointers.json"
    pointers_path.write_text(json.dumps(pointers), encoding="utf-8")
    return main(["--evidence-pointers", str(pointers_path)])


if __name__ == "__main__":
    raise SystemExit(run())
