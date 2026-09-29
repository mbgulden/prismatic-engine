"""T1 single arming ceremony: the signed record is the only authority.

These tests pin the post-ceremony contract:

- a fresh ledger with no arming record leaves T1 inert (``t1_never_armed``);
- ``MergeStage.process()`` refuses ``refused_not_armed`` without a record;
- the ceremony arms with a throwaway Ed25519 key and the record verifies;
- tampered / expired / disarmed / keyless states all fail closed;
- ``arm`` with no signing key raises and writes nothing;
- the old env-var authority is retired.

Throwaway keys and temp ledger DBs only. Never the production DB, never the
real signing key.
"""

from types import SimpleNamespace

import sys
import types

import pytest

from prismatic.review_factory import arming
from prismatic.review_factory.merge_stage import MergeStage, MergeStageConfig


def _throwaway_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    return Ed25519PrivateKey.generate()


def _ledger(tmp_path):
    # Function-local import: a top-level import would set the ``trust``
    # attribute on the ``prismatic.review_factory`` package at pytest
    # collection time and shadow sys.modules-only trust stubs in test
    # files that run earlier (e.g. test_autonomy.py).
    from prismatic.review_factory import trust as trust_module

    return trust_module.TrustLedger(
        db_path=tmp_path / "trust.db", audit_dir=tmp_path / "audit"
    )


@pytest.fixture
def key_env(monkeypatch, tmp_path):
    """Isolate the pubkey cache to a temp dir for every test."""
    monkeypatch.setenv(arming.PUBKEY_CACHE_ENV, str(tmp_path / "keys" / "t1.pub"))
    return tmp_path


def _armed_ledger(tmp_path, key, expires_at=None):
    ledger = _ledger(tmp_path)
    doc = arming.build_arming_document(
        approver="test-approver", rationale="test", expires_at=expires_at
    )
    arming.sign_arming_document(doc, private_key=key, key_id="test-key")
    # Mirror the ceremony: arm caches the public key alongside the record.
    arming.cache_public_key(key)
    ledger.record_t1_armed(document=doc)
    return ledger, doc


# ── status: the single consult ──────────────────────────────────────────


def test_no_record_leaves_t1_inert(tmp_path, key_env):
    status = arming.t1_arming_status(_ledger(tmp_path))
    assert status["armed"] is False
    assert status["reason"] == "t1_never_armed"
    assert status["record"] is None


def test_arm_then_status_armed(tmp_path, key_env):
    ledger, _doc = _armed_ledger(tmp_path, _throwaway_key())
    status = arming.t1_arming_status(ledger)
    assert status["armed"] is True
    assert status["reason"] == "t1_armed"
    assert status["record"]["approver"] == "test-approver"
    assert status["record"]["tier"] == 1


def test_disarm_supersedes_arm(tmp_path, key_env):
    ledger, _doc = _armed_ledger(tmp_path, _throwaway_key())
    ledger.record_t1_disarmed(approver="test-approver", rationale="test over")
    status = arming.t1_arming_status(ledger)
    assert status["armed"] is False
    assert status["reason"] == "t1_disarmed"


def test_tampered_record_fails_closed(tmp_path, key_env):
    ledger, doc = _armed_ledger(tmp_path, _throwaway_key())
    # Tamper with the signed document post-signing: flip the approver.
    tampered = dict(doc)
    tampered["approver"] = "mallory"
    ledger2 = _ledger(tmp_path / "t2")
    ledger2.record_t1_armed(document=tampered)
    status = arming.t1_arming_status(ledger2)
    assert status["armed"] is False
    assert status["reason"] == "t1_arming_signature_invalid"


def test_forged_unsigned_record_cannot_arm(tmp_path, key_env):
    """A raw record_event (the 32-synthetic-events precedent) can't arm."""
    ledger = _ledger(tmp_path)
    ledger.record_event(
        "t1_armed",
        tier_at_event=1,
        judgment={"marker": "t1-arming-record", "approver": "mallory"},
        notes="forged",
    )
    status = arming.t1_arming_status(ledger)
    assert status["armed"] is False
    # Full schema validation runs before signature checks: the forged doc
    # is missing most schema fields.
    assert status["reason"] == "t1_arming_schema_invalid"


def test_expired_record_fails_closed(tmp_path, key_env):
    ledger, _doc = _armed_ledger(
        tmp_path, _throwaway_key(), expires_at="2020-01-01T00:00:00+00:00"
    )
    status = arming.t1_arming_status(ledger)
    assert status["armed"] is False
    assert status["reason"] == "t1_arming_expired"


def test_malformed_expires_at_fails_closed(tmp_path, key_env):
    """A non-empty but unparseable expires_at cannot arm."""
    ledger, _doc = _armed_ledger(tmp_path, _throwaway_key(), expires_at="not-a-date")
    status = arming.t1_arming_status(ledger)
    assert status["armed"] is False
    assert status["reason"] == "t1_arming_expired"


def test_missing_pubkey_cache_fails_closed(tmp_path, monkeypatch):
    """No cached public key (e.g. deleted) -> unverifiable -> disarmed."""
    key = _throwaway_key()
    ledger = _ledger(tmp_path)
    doc = arming.build_arming_document(approver="a", rationale="r")
    arming.sign_arming_document(doc, private_key=key, key_id="test-key")
    # Point the cache somewhere the ceremony never wrote.
    monkeypatch.setenv(arming.PUBKEY_CACHE_ENV, str(tmp_path / "empty" / "nope.pub"))
    ledger.record_t1_armed(document=doc)
    status = arming.t1_arming_status(ledger)
    assert status["armed"] is False
    assert status["reason"] == "t1_arming_key_unavailable"


def test_ledger_read_failure_fails_closed():
    class _BrokenLedger:
        def events(self):
            raise RuntimeError("db gone")

    status = arming.t1_arming_status(_BrokenLedger())
    assert status["armed"] is False
    assert status["reason"] == "t1_arming_check_failed"


# ── ceremony: signing discipline ────────────────────────────────────────


def test_arm_with_no_key_raises_and_writes_nothing(tmp_path, key_env, monkeypatch):
    monkeypatch.setattr(arming, "_load_signing_key", lambda: (None, "none"))
    ledger = _ledger(tmp_path)
    doc = arming.build_arming_document(approver="a", rationale="r")
    with pytest.raises(RuntimeError):
        arming.sign_arming_document(doc)
    assert ledger.events() == []


def test_record_t1_armed_rejects_unsigned_document(tmp_path):
    ledger = _ledger(tmp_path)
    with pytest.raises(ValueError):
        ledger.record_t1_armed(document={"marker": "t1-arming-record", "approver": "a"})
    assert ledger.events() == []


def test_build_arming_document_requires_approver_and_rationale():
    with pytest.raises(ValueError):
        arming.build_arming_document(approver="", rationale="r")
    with pytest.raises(ValueError):
        arming.build_arming_document(approver="a", rationale="  ")


def test_cli_arm_and_status_roundtrip(tmp_path, key_env, monkeypatch):
    """The CLI arm writes a verifiable record; status reports armed."""
    from prismatic.review_factory import arming as arming_mod

    key = _throwaway_key()
    monkeypatch.setattr(arming_mod, "_load_signing_key", lambda: (key, "test-key"))

    state_dir = tmp_path / "state"
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(state_dir))

    rc = arming_mod.main(["arm", "--approver", "cli-test", "--rationale", "roundtrip"])
    assert rc == 0
    # The pubkey cache was written where the ceremony looks.
    assert arming_mod._pubkey_cache_path().exists()

    rc = arming_mod.main(["status"])
    assert rc == 0

    rc = arming_mod.main(["disarm", "--approver", "cli-test", "--rationale", "done"])
    assert rc == 0
    assert arming_mod.main(["status"]) == 1


# ── merge stage: the gate ───────────────────────────────────────────────


def _wired_stage(monkeypatch):
    queue = SimpleNamespace()
    cfg = MergeStageConfig(enabled=True, dry_run=True, live_tiers=frozenset({1}))
    stage = MergeStage(queue, cfg, novelty_detector=None)
    monkeypatch.setattr(
        MergeStage,
        "_judgment_holds_for_human",
        lambda self, job, job_id, tier: False,
    )
    return stage


def _job():
    return SimpleNamespace(
        review_job_id="job-t1",
        risk_tier=1,
        change_class="docs",
        changed_paths_json='["docs/guide.md"]',
        deterministic_verdict="CLEAN",
    )


def test_process_refuses_when_not_armed(tmp_path, key_env, monkeypatch):
    """Fresh checkout, no arming record: the stage refuses, nothing merges."""
    monkeypatch.setattr(
        arming,
        "t1_arming_status",
        lambda ledger=None: {
            "armed": False,
            "reason": "t1_never_armed",
            "record": None,
        },
    )
    stage = _wired_stage(monkeypatch)
    result = stage.process(_job())
    assert result.action == "refused_not_armed"


def test_process_reaches_consult_when_armed(tmp_path, key_env, monkeypatch):
    """With a valid record the gate opens and the autonomy consult runs."""
    key = _throwaway_key()
    _ledger, doc = _armed_ledger(tmp_path, key)
    monkeypatch.setattr(
        arming,
        "t1_arming_status",
        lambda ledger=None: {"armed": True, "reason": "t1_armed", "record": doc},
    )
    seen = {}

    def fake_can_auto_merge(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(allowed=True, reason="auto_merge_allowed")

    import prismatic.review_factory as rf_pkg

    autonomy = types.ModuleType("prismatic.review_factory.autonomy")
    autonomy.can_auto_merge = fake_can_auto_merge
    monkeypatch.setitem(sys.modules, "prismatic.review_factory.autonomy", autonomy)
    monkeypatch.setattr(rf_pkg, "autonomy", autonomy, raising=False)
    stage = _wired_stage(monkeypatch)
    allowed, reason = stage._autonomy_consult(_job(), arming_tier=1)
    assert allowed is True
    assert seen["tier"] == 1
    assert seen["brake_engaged"] is False  # brake retired from the decision path


# ── retirement: old env authority ───────────────────────────────────────


def test_from_env_retires_old_vars(monkeypatch):
    monkeypatch.setenv("PRISMATIC_RF_MERGE_AUTHORITY", "1")
    monkeypatch.setenv("PRISMATIC_RF_MERGE_DRY_RUN", "0")
    monkeypatch.setenv("PRISMATIC_RF_MERGE_LIVE_TIERS", "0,1")
    cfg = MergeStageConfig.from_env()
    assert cfg.enabled is False
    assert cfg.dry_run is True
    assert cfg.live_tiers == frozenset()


def test_from_env_defaults_inert(monkeypatch):
    for name in (
        "PRISMATIC_RF_MERGE_AUTHORITY",
        "PRISMATIC_RF_MERGE_DRY_RUN",
        "PRISMATIC_RF_MERGE_LIVE_TIERS",
    ):
        monkeypatch.delenv(name, raising=False)
    cfg = MergeStageConfig.from_env()
    assert cfg.enabled is False
    assert cfg.live_tiers == frozenset()


# ── daemon wiring ───────────────────────────────────────────────────────


def test_build_merge_stage_from_arming_unwired_when_disarmed(
    tmp_path, key_env, monkeypatch
):
    from prismatic.gateway import verification_daemon as vd

    monkeypatch.setattr(
        arming,
        "t1_arming_status",
        lambda ledger=None: {
            "armed": False,
            "reason": "t1_never_armed",
            "record": None,
        },
    )
    assert vd.build_merge_stage_from_arming(queue=object()) is None


def test_build_merge_stage_from_arming_wired_when_armed(tmp_path, key_env, monkeypatch):
    from prismatic.gateway import verification_daemon as vd

    key = _throwaway_key()
    _ledger, doc = _armed_ledger(tmp_path, key)
    monkeypatch.setattr(
        arming,
        "t1_arming_status",
        lambda ledger=None: {"armed": True, "reason": "t1_armed", "record": doc},
    )
    stage = vd.build_merge_stage_from_arming(queue=object())
    assert stage is not None
    assert stage.config.enabled is True
    assert stage.config.dry_run is False
    assert stage.config.live_tiers == frozenset({1})
