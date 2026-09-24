"""Tests for the RF-6 trust ledger (earned autonomy, Phase 1).

Uses a throwaway SQLite DB and audit dir per test (tmp_path). The ledger
is data-only: graduation checks return proposals, never change tiers.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.trust import (
    CHANGE_CLASSES,
    EVENT_TYPES,
    TIERS,
    TrustLedger,
)


def make_ledger(tmp_path: Path, **kwargs) -> TrustLedger:
    audit_dir = tmp_path / "audit"
    db = ReviewFactoryDB(db_path=tmp_path / "t.db")
    return TrustLedger(db=db, audit_dir=audit_dir, **kwargs)


def merge(ledger: TrustLedger, artifact_id: str, change_class: str = "docs") -> dict:
    return ledger.record_merge_outcome(
        artifact_id=artifact_id,
        change_class=change_class,
        merged_by="auto",
    )


# ── (a) clean streak builds, tier stays 0, T1 proposal is data-only ──


def test_twenty_clean_docs_merges_propose_t1(tmp_path):
    ledger = make_ledger(tmp_path)
    for i in range(20):
        merge(ledger, f"doc-{i}")

    status = ledger.tier_status()
    assert status["consecutive_clean_by_class"]["docs"] == 20
    assert status["current_tier"] == 0  # proposals never change tiers

    proposal = ledger.check_graduation()
    assert proposal is not None
    assert proposal["proposal"] == "tier_promotion"
    assert proposal["from_tier"] == 0
    assert proposal["to_tier"] == 1
    assert proposal["requires_michael"] is True
    assert proposal["evidence"]["clean_streak"] == 20
    assert proposal["evidence"]["threshold"] == 20


def test_union_streak_across_tier_classes(tmp_path):
    """The T1 streak is a union across docs/chore/dep_bump (10+10=20)."""
    ledger = make_ledger(tmp_path)
    for i in range(10):
        merge(ledger, f"doc-{i}", change_class="docs")
        merge(ledger, f"chore-{i}", change_class="chore")

    proposal = ledger.check_graduation()
    assert proposal is not None
    assert proposal["to_tier"] == 1
    assert proposal["evidence"]["clean_streak"] == 20


# ── (b) explicit promotion moves the tier ──


def test_tier_promoted_moves_tier(tmp_path):
    ledger = make_ledger(tmp_path)
    for i in range(20):
        merge(ledger, f"doc-{i}")

    ev = ledger.record_tier_promoted(
        to_tier=1, approver="mbgulden", rationale="streak met"
    )
    assert ev["event_type"] == "tier_promoted"
    assert ledger.tier_status()["current_tier"] == 1
    # After promotion the fold uses the new tier; T2 needs agent_standard work.
    assert ledger.check_graduation() is None


# ── (c) rollback at T1 -> mechanical revocation + 7d freeze ──


def test_rollback_revokes_tier_and_freezes_promotion(tmp_path):
    ledger = make_ledger(tmp_path)
    ledger.record_tier_promoted(to_tier=1, approver="mbgulden")
    assert ledger.tier_status()["current_tier"] == 1

    for i in range(5):
        merge(ledger, f"agent-{i}", change_class="agent_standard")

    before = datetime.now(timezone.utc)
    ledger.record_rollback(
        artifact_id="agent-4", change_class="agent_standard", notes="bad merge"
    )

    types = [e["event_type"] for e in ledger.events()]
    assert "rollback_detected" in types
    assert "tier_revoked" in types

    status = ledger.tier_status()
    assert status["current_tier"] == 0
    assert status["consecutive_clean_by_class"]["agent_standard"] == 0  # streak reset
    freeze = status["promotion_freeze_until"]
    assert freeze is not None
    freeze_dt = datetime.fromisoformat(freeze)
    assert before + timedelta(days=7) - timedelta(minutes=1) <= freeze_dt
    assert freeze_dt <= datetime.now(timezone.utc) + timedelta(days=7, minutes=1)

    # Even a fresh 20-clean streak cannot propose while the freeze is active.
    for i in range(20):
        merge(ledger, f"doc2-{i}")
    assert ledger.tier_status()["consecutive_clean_by_class"]["docs"] == 20
    assert ledger.check_graduation() is None


# ── (d) manual revoke to an explicit tier ──


def test_manual_revoke_to_explicit_tier(tmp_path):
    ledger = make_ledger(tmp_path)
    ledger.record_tier_promoted(to_tier=1, approver="mbgulden")

    ev = ledger.revoke(to_tier=0, reason="human overrode REPAIR")
    assert ev["event_type"] == "tier_revoked"
    assert ev["tier_at_event"] == 0
    assert ev["judgment"]["from_tier"] == 1
    assert ledger.tier_status()["current_tier"] == 0


def test_revoke_defaults_to_one_step_down(tmp_path):
    ledger = make_ledger(tmp_path)
    ledger.record_tier_promoted(to_tier=2, approver="mbgulden")
    ledger.revoke(reason="caution")
    assert ledger.tier_status()["current_tier"] == 1


# ── (e) pause precision ──


def test_pause_precision_eight_of_ten(tmp_path):
    ledger = make_ledger(tmp_path)
    for i in range(8):
        ledger.record_pause_resolution(artifact_id=f"p-{i}", agreed=True)
    for i in range(2):
        ledger.record_pause_resolution(artifact_id=f"d-{i}", agreed=False)

    status = ledger.tier_status()
    assert status["pause_precision"] == 0.8
    assert status["pauses_trailing_30"] == 10


def test_pause_precision_none_without_pauses(tmp_path):
    assert make_ledger(tmp_path).tier_status()["pause_precision"] is None


# ── (f) invalid event_type: ValueError, zero rows written ──


def test_bad_event_type_raises_and_writes_nothing(tmp_path):
    ledger = make_ledger(tmp_path)
    with pytest.raises(ValueError):
        ledger.record_event("not_a_real_event", artifact_id="x")
    assert ledger.events() == []
    mirror = tmp_path / "audit" / "trust-ledger.jsonl"
    assert not mirror.exists() or mirror.read_text() == ""


def test_bad_change_class_and_merged_by_raise(tmp_path):
    ledger = make_ledger(tmp_path)
    with pytest.raises(ValueError):
        ledger.record_merge_outcome(
            artifact_id="x", change_class="bogus", merged_by="auto"
        )
    with pytest.raises(ValueError):
        ledger.record_merge_outcome(
            artifact_id="x", change_class="docs", merged_by="skynet"
        )
    with pytest.raises(ValueError):
        ledger.record_event("merge_completed", tier_at_event=9)
    assert ledger.events() == []


# ── (g) JSONL mirror: one valid JSON line per event ──


def test_mirror_has_one_json_line_per_event(tmp_path):
    ledger = make_ledger(tmp_path)
    merge(ledger, "a1")
    ledger.record_pause_resolution(artifact_id="a1", agreed=True)
    ledger.record_brake_pulled(notes="manual stop")

    events = ledger.events()
    mirror = tmp_path / "audit" / "trust-ledger.jsonl"
    lines = mirror.read_text().splitlines()
    assert len(lines) == len(events) == 3
    for line in lines:
        obj = json.loads(line)  # raises if not valid JSON
        assert obj["event_id"]
        assert obj["event_type"] in EVENT_TYPES
    assert {o["event_id"] for o in map(json.loads, lines)} == {
        e["event_id"] for e in events
    }


def test_mirror_failure_keeps_db_authoritative(tmp_path):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    db = ReviewFactoryDB(db_path=tmp_path / "t.db")
    ledger = TrustLedger(db=db, audit_dir=audit_dir)
    # Block the mirror: a directory where the JSONL file would go makes the
    # append fail, while the DB row must still commit.
    (audit_dir / "trust-ledger.jsonl").mkdir()
    ev = merge(ledger, "a1")
    assert ev["mirror_ok"] is False
    assert len(ledger.events()) == 1  # DB row still committed


# ── (h) no bare-state tier setter ──


def test_no_bare_state_tier_setter(tmp_path):
    ledger = make_ledger(tmp_path)
    assert not hasattr(ledger, "set_tier")
    assert not hasattr(ledger, "set_current_tier")


# ── (i) promotion needs a named approver ──


def test_empty_approver_rejected(tmp_path):
    ledger = make_ledger(tmp_path)
    with pytest.raises(ValueError):
        ledger.record_tier_promoted(to_tier=1, approver="")
    with pytest.raises(ValueError):
        ledger.record_tier_promoted(to_tier=1, approver="   ")
    assert ledger.tier_status()["current_tier"] == 0
    assert ledger.events() == []


# ── constants sanity ──


def test_constants_match_spec():
    assert EVENT_TYPES == {
        "merge_completed",
        "rollback_detected",
        "human_revert",
        "pause_resolved",
        "tier_promoted",
        "tier_revoked",
        "brake_pulled",
    }
    assert CHANGE_CLASSES == {
        "docs",
        "chore",
        "dep_bump",
        "agent_standard",
        "sensitive",
        "production",
    }
    assert TIERS == (0, 1, 2, 3)


# ── subscribe: gateway unavailable -> False no-op ──


def test_subscribe_noop_when_gateway_missing(tmp_path, monkeypatch):
    ledger = make_ledger(tmp_path)
    monkeypatch.setitem(sys.modules, "prismatic.gateway.event_bus", None)
    assert ledger.subscribe() is False
    assert ledger.events() == []


# ── brake pull records; human_revert revokes like rollback ──


def test_brake_pulled_records_event(tmp_path):
    ledger = make_ledger(tmp_path)
    ev = ledger.record_brake_pulled(notes="operator stop")
    assert ev["event_type"] == "brake_pulled"
    assert ev["notes"] == "operator stop"


def test_human_revert_revokes_auto_merged_work(tmp_path):
    ledger = make_ledger(tmp_path)
    ledger.record_tier_promoted(to_tier=1, approver="mbgulden")
    ledger.record_human_revert(
        artifact_id="a1", change_class="agent_standard", notes="wrong call"
    )
    assert ledger.tier_status()["current_tier"] == 0
    assert ledger.tier_status()["rollback_count_30d"] == 1
