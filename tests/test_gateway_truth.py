"""Tests for the read-only dashboard truth panels (GET /api/gateway/truth).

Covers the six panels (trust ledger, merge receipts, T1 status, deployed
SHA, consumer lag, run receipts), per-panel fail-open behavior, the pristine/inert state,
the T1 adversarial case (signed t1_armed record must surface as read-only —
the arming path must never be invoked), and the HTTP route.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from prismatic.gateway import server
from prismatic.gateway.truth import _PROBES, get_truth_snapshot

PANEL_NAMES = {
    "trust_ledger",
    "merge_receipts",
    "t1",
    "deployed_sha",
    "consumer_lag",
    "run_receipts",
}


def _scope_state(monkeypatch, tmp_path):
    """Point every state-bearing probe at tmp fixtures. Returns paths."""
    db_path = tmp_path / "state" / "agy_completed_work.db"
    audit_dir = tmp_path / "audit"
    receipts_path = tmp_path / "receipts" / "merge-receipts.jsonl"
    bus_db = tmp_path / "bus" / "event_log.sqlite"
    monkeypatch.setenv("PRISMATIC_AGY_COMPLETED_WORK_DB", str(db_path))
    monkeypatch.setenv("PRISMATIC_AUDIT_DIR", str(audit_dir))
    monkeypatch.setenv("PRISMATIC_MERGE_RECEIPTS", str(receipts_path))
    monkeypatch.setenv("PRISMATIC_BUS_DB", str(bus_db))
    monkeypatch.setenv(
        "PRISMATIC_T1_ARMING_PUBKEY_FILE", str(tmp_path / "keys" / "t1.pub")
    )
    monkeypatch.delenv("PRISMATIC_RELEASE_SHA", raising=False)
    return {
        "db_path": db_path,
        "audit_dir": audit_dir,
        "receipts_path": receipts_path,
        "bus_db": bus_db,
    }


def test_snapshot_has_six_panels(monkeypatch, tmp_path):
    _scope_state(monkeypatch, tmp_path)
    snapshot = get_truth_snapshot()
    assert snapshot["ok"] is True
    assert set(snapshot["panels"]) == PANEL_NAMES
    assert {name for name, probe in _PROBES} == PANEL_NAMES
    for panel in snapshot["panels"].values():
        assert "ok" in panel


def test_trust_panel_reports_tier_and_events(monkeypatch, tmp_path):
    from prismatic.review_factory.trust import TrustLedger

    paths = _scope_state(monkeypatch, tmp_path)
    ledger = TrustLedger(db_path=paths["db_path"], audit_dir=paths["audit_dir"])
    ledger.record_event("merge_completed", change_class="docs", artifact_id="PR-1")
    ledger.record_event("merge_completed", change_class="docs", artifact_id="PR-2")
    ledger.record_tier_promoted(to_tier=1, approver="michael", rationale="earned")

    panel = get_truth_snapshot()["panels"]["trust_ledger"]
    assert panel["ok"] is True
    assert panel["current_tier"] == 1
    assert panel["event_count"] >= 3
    assert panel["recent_events"]
    assert panel["consecutive_clean_by_class"]["docs"] >= 2


def test_trust_panel_pristine_reports_no_data(monkeypatch, tmp_path):
    _scope_state(monkeypatch, tmp_path)
    panel = get_truth_snapshot()["panels"]["trust_ledger"]
    assert panel["ok"] is False
    assert panel["reason"] == "no data"


def test_merge_receipts_pristine_reports_no_data(monkeypatch, tmp_path):
    _scope_state(monkeypatch, tmp_path)
    panel = get_truth_snapshot()["panels"]["merge_receipts"]
    assert panel["ok"] is False
    assert panel["reason"] == "no data"


def test_merge_receipts_coverage_counts_and_latest(monkeypatch, tmp_path):
    paths = _scope_state(monkeypatch, tmp_path)
    receipts = [
        {
            "marker": "MERGE_RECEIPT_V1",
            "receipt_id": "r-1",
            "merge_sha": "aaa111",
            "candidate_sha": "ccc111",
            "emitted_at": "2026-09-28T18:00:00+00:00",
            "actor": "michael",
            "change_class": "docs",
            "signature_or_attestation": None,
        },
        {
            "marker": "MERGE_RECEIPT_V1",
            "receipt_id": "r-2",
            "merge_sha": "bbb222",
            "candidate_sha": "ccc222",
            "emitted_at": "2026-09-28T19:00:00+00:00",
            "actor": "michael",
            "change_class": "chore",
            "signature_or_attestation": {"type": "attestation", "value": "sig"},
        },
    ]
    paths["receipts_path"].parent.mkdir(parents=True, exist_ok=True)
    paths["receipts_path"].write_text(
        "\n".join(json.dumps(r) for r in receipts) + "\n", encoding="utf-8"
    )

    panel = get_truth_snapshot()["panels"]["merge_receipts"]
    assert panel["ok"] is True
    assert panel["receipt_count"] == 2
    assert panel["signed_count"] == 1
    assert panel["unsigned_count"] == 1
    assert panel["latest"]["merge_sha"] == "bbb222"
    assert panel["latest"]["signed"] is True


def test_t1_panel_pristine_is_inert(monkeypatch, tmp_path):
    _scope_state(monkeypatch, tmp_path)
    panel = get_truth_snapshot()["panels"]["t1"]
    assert panel["ok"] is True
    assert panel["armed"] is False
    assert panel["reason"] == "t1_never_armed"
    assert panel["record"] is None
    assert panel["read_only"] is True


def test_t1_adversarial_signed_armed_record_stays_read_only(monkeypatch, tmp_path):
    """A signed t1_armed record surfaces as armed — with no arming path run.

    The arming entrypoints are monkeypatched to raise so any accidental
    invocation fails the test loudly.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    import prismatic.review_factory.arming as arming
    from prismatic.review_factory.trust import TrustLedger

    paths = _scope_state(monkeypatch, tmp_path)

    def _must_not_run(*args, **kwargs):
        raise AssertionError("arming path invoked during read-only truth probe")

    monkeypatch.setattr(arming, "cmd_arm", _must_not_run)
    monkeypatch.setattr(arming, "main", _must_not_run)

    key = Ed25519PrivateKey.generate()
    arming.cache_public_key(key)
    doc = arming.build_arming_document(
        approver="michael", rationale="adversarial fixture"
    )
    arming.sign_arming_document(doc, private_key=key, key_id="test-key")
    ledger = TrustLedger(db_path=paths["db_path"], audit_dir=paths["audit_dir"])
    ledger.record_t1_armed(document=doc)

    # Any ledger write attempted by the probe must fail loudly.
    monkeypatch.setattr(
        TrustLedger,
        "record_event",
        _raise_on_record,
    )

    panel = get_truth_snapshot()["panels"]["t1"]
    assert panel["ok"] is True
    assert panel["armed"] is True
    assert panel["reason"] == "t1_armed"
    assert panel["record"]["approver"] == "michael"


def _raise_on_record(self, *args, **kwargs):
    raise AssertionError("ledger write during read-only truth probe")


def test_truth_module_never_references_arming_paths():
    # Boundary guard: the docstring documents the restriction; the code
    # itself must never import or call an arming write path.
    source = (
        Path(__file__)
        .resolve()
        .parents[1]
        .joinpath("prismatic", "gateway", "truth.py")
        .read_text(encoding="utf-8")
    )
    for forbidden in (
        "cmd_arm(",
        "arming.cmd_arm",
        "sign_arming_document(",
        "build_arming_document(",
        "import cmd_arm",
    ):
        assert forbidden not in source
    assert "from prismatic.review_factory.arming import t1_arming_status" in source


def test_deployed_sha_from_env(monkeypatch, tmp_path):
    _scope_state(monkeypatch, tmp_path)
    monkeypatch.setenv("PRISMATIC_RELEASE_SHA", "72669f7357db")
    panel = get_truth_snapshot()["panels"]["deployed_sha"]
    assert panel["ok"] is True
    assert panel["sha"] == "72669f7357db"


def test_deployed_sha_pristine_reports_no_data(monkeypatch, tmp_path):
    _scope_state(monkeypatch, tmp_path)
    monkeypatch.delenv("PRISMATIC_RELEASE_SHA", raising=False)
    # Test checkout is not a release dir, so the SHA cannot be determined.
    panel = get_truth_snapshot()["panels"]["deployed_sha"]
    assert panel["ok"] is False
    assert panel["reason"] == "no data"


def _make_bus_db(bus_db: Path):
    bus_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(bus_db))
    conn.execute(
        "CREATE TABLE task_admission_outbox ("
        " event_id TEXT PRIMARY KEY, task_id TEXT, status TEXT, created_at TEXT)"
    )
    conn.executemany(
        "INSERT INTO task_admission_outbox(event_id, task_id, status, created_at)"
        " VALUES (?,?,?,?)",
        [
            ("e-1", "t-1", "pending", "2026-09-28T20:00:00+00:00"),
            ("e-2", "t-2", "pending", "2026-09-28T20:05:00+00:00"),
            ("e-3", "t-3", "launched", "2026-09-28T19:00:00+00:00"),
            ("e-4", "t-4", "failed", "2026-09-28T18:00:00+00:00"),
        ],
    )
    conn.commit()
    conn.close()


def test_consumer_lag_counts_backlog(monkeypatch, tmp_path):
    paths = _scope_state(monkeypatch, tmp_path)
    _make_bus_db(paths["bus_db"])
    panel = get_truth_snapshot()["panels"]["consumer_lag"]
    assert panel["ok"] is True
    assert panel["pending"] == 2
    assert panel["claimed"] == 0
    assert panel["launched"] == 1
    assert panel["failed"] == 1
    assert panel["oldest_pending"] == "2026-09-28T20:00:00+00:00"


def test_consumer_lag_pristine_reports_no_data(monkeypatch, tmp_path):
    _scope_state(monkeypatch, tmp_path)
    panel = get_truth_snapshot()["panels"]["consumer_lag"]
    assert panel["ok"] is False
    assert panel["reason"] == "no data"


def test_probe_failure_is_fail_open_per_panel(monkeypatch, tmp_path):
    import prismatic.gateway.truth as truth

    _scope_state(monkeypatch, tmp_path)
    probes = dict(truth._PROBES)
    probes["trust_ledger"] = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    monkeypatch.setattr(truth, "_PROBES", tuple(probes.items()))
    snapshot = get_truth_snapshot()
    assert snapshot["ok"] is True
    failed = snapshot["panels"]["trust_ledger"]
    assert failed["ok"] is False
    assert failed["reason"] == "probe_failed"
    assert "RuntimeError" in failed["error"]
    # The rest of the document still completes.
    assert set(snapshot["panels"]) == PANEL_NAMES


def test_inert_state_snapshot_shape(monkeypatch, tmp_path):
    """Pristine fixtures: data panels report no data, T1 reports inert."""
    _scope_state(monkeypatch, tmp_path)
    panels = get_truth_snapshot()["panels"]
    assert panels["trust_ledger"] == {"ok": False, "reason": "no data"}
    assert panels["merge_receipts"] == {"ok": False, "reason": "no data"}
    assert panels["consumer_lag"] == {"ok": False, "reason": "no data"}
    assert panels["deployed_sha"] == {"ok": False, "reason": "no data"}
    assert panels["run_receipts"] == {"ok": False, "reason": "no data"}
    assert panels["t1"]["armed"] is False
    assert panels["t1"]["reason"] == "t1_never_armed"


def test_route_truth_returns_200(monkeypatch, tmp_path):
    _scope_state(monkeypatch, tmp_path)
    response = TestClient(server.app).get("/api/gateway/truth")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert set(body["panels"]) == PANEL_NAMES
