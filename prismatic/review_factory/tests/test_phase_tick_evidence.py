"""Tests for the phase-advancement evidence adapter (scripts/).

The adapter bridges the daily shadow-agreement JSON (an object with a
``records`` array) into the evidence-pointers JSON the tick consumes. It is
wiring-only: the tick files requests, never executes — ``execute()`` stays
behind the master switch + Michael's approval.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ADAPTER = REPO_ROOT / "scripts" / "phase_advancement_evidence_tick.py"


def _load_adapter():
    spec = importlib.util.spec_from_file_location(
        "phase_advancement_evidence_tick", ADAPTER
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sample_agreement():
    return {
        "date": "2026-09-23",
        "n_decided": 2,
        "bad_merge_calls": 0,
        "records": [
            {
                "pr_number": 1,
                "system_call": "merge",
                "actual_outcome": "merged",
                "head_sha": "abc",
            },
            {
                "pr_number": 2,
                "system_call": "skip",
                "actual_outcome": "pending",
                "head_sha": "def",
            },
        ],
    }


def _audit_dir(tmp_path, with_signal=False):
    audit = tmp_path / "audit"
    audit.mkdir()
    (audit / "shadow-agreement-2026-09-23.json").write_text(
        json.dumps(_sample_agreement()), encoding="utf-8"
    )
    signal = json.dumps({"signal": "x"}) + "\n" if with_signal else ""
    (audit / "shadow-decisions.jsonl").write_text(signal, encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    return audit, work


def test_adapter_extracts_records_to_jsonl_and_builds_pointers(tmp_path):
    mod = _load_adapter()
    audit, work = _audit_dir(tmp_path)

    pointers = mod.build_pointers(audit, work)
    assert set(pointers) == {
        "shadow_records",
        "shadow_signals",
        "bad_merge_calls",
    }
    assert pointers["bad_merge_calls"] == 0
    assert pointers["shadow_signals"].endswith("shadow-decisions.jsonl")

    rows = [
        json.loads(line)
        for line in Path(pointers["shadow_records"]).read_text().splitlines()
    ]
    assert len(rows) == 2
    assert rows[0]["system_call"] == "merge"
    assert rows[0]["actual_outcome"] == "merged"


def test_adapter_missing_agreement_fails_closed(tmp_path):
    mod = _load_adapter()
    audit = tmp_path / "audit"
    audit.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    try:
        mod.build_pointers(audit, work)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("expected FileNotFoundError")


def test_tick_consumes_adapter_pointers(tmp_path):
    from prismatic.review_factory.phase_advancement import load_evidence

    mod = _load_adapter()
    audit, work = _audit_dir(tmp_path, with_signal=True)

    evidence = load_evidence(mod.build_pointers(audit, work))
    assert evidence["bad_merge_calls"] == 0
    assert isinstance(evidence["bad_merge_calls"], int)
    assert [r["system_call"] for r in evidence["shadow_records"]] == [
        "merge",
        "skip",
    ]
    assert evidence["shadow_signals"] == [{"signal": "x"}]
