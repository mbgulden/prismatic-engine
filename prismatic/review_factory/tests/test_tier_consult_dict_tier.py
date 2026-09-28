"""R-2: the tier consult must read the real ``tier_status()`` dict contract.

Regression test for the swallowed-TypeError bug in
``prismatic/review_factory/merge_stage.py`` (``_autonomy_consult``):

    tier = int(ledger.tier_status())   # pre-fix: TypeError every time

The real ``TrustLedger.tier_status()`` returns a ``dict`` with a
``"current_tier"`` key (see ``trust.py``), so ``int(...)`` raised
``TypeError`` on every consult. The surrounding ``except`` swallowed it
and forced ``tier = 0`` -- fail-closed, but permanently inert: even with
the ledger promoted to T1, ``can_auto_merge`` always saw tier 0 and
refused, so T1 could never activate.

These tests pin the PRODUCTION contract:

- the ledger used here is the REAL ``TrustLedger`` (sqlite in a tmp
  dir), promoted to tier 1 via ``record_tier_promoted`` -- no stub whose
  ``tier_status()`` returns an int and hides the mismatch (that is
  exactly how this bug slipped past the existing wiring tests);
- one test drives the consult through the REAL ``autonomy.can_auto_merge``
  to prove the T1 path actually opens end-to-end post-fix.

Fail-first: the tier-1 assertions fail on the pre-fix code (the consult
swallows the TypeError and sees tier 0) and pass post-fix.
"""

import sys
import types
from pathlib import Path
from types import SimpleNamespace

import prismatic.review_factory as rf_pkg
from prismatic.review_factory import autonomy as autonomy_module
from prismatic.review_factory import trust as trust_module
from prismatic.review_factory.merge_stage import MergeStage, MergeStageConfig

_AUTONOMY_MODULE = "prismatic.review_factory.autonomy"
_TRUST_MODULE = "prismatic.review_factory.trust"


def _make_t1_ledger(tmp_path: Path):
    """A REAL TrustLedger promoted to tier 1 (the production contract)."""
    ledger = trust_module.TrustLedger(
        db_path=tmp_path / "trust.db",
        audit_dir=tmp_path / "audit",
    )
    ledger.record_tier_promoted(
        to_tier=1, approver="r2-test", rationale="fail-first regression test"
    )
    status = ledger.tier_status()
    assert isinstance(status, dict), "tier_status() must return a dict"
    assert status["current_tier"] == 1
    return ledger


def _install_trust_ledger(monkeypatch, ledger):
    """Point the lazy ``from prismatic.review_factory import trust`` at our ledger."""

    def _factory(*args, **kwargs):
        return ledger

    trust_mod = types.ModuleType(_TRUST_MODULE)
    trust_mod.TrustLedger = _factory
    monkeypatch.setitem(sys.modules, _TRUST_MODULE, trust_mod)
    monkeypatch.setattr(rf_pkg, "trust", trust_mod, raising=False)


def _install_recording_autonomy(monkeypatch):
    """Stub autonomy with a recording fake that captures can_auto_merge kwargs."""
    seen = {}

    def fake_can_auto_merge(**kwargs):
        seen.clear()
        seen.update(kwargs)
        return SimpleNamespace(allowed=kwargs["tier"] != 0, reason="recorded")

    autonomy_mod = types.ModuleType(_AUTONOMY_MODULE)
    autonomy_mod.brake_status = lambda: {"engaged": False}
    autonomy_mod.can_auto_merge = fake_can_auto_merge
    monkeypatch.setitem(sys.modules, _AUTONOMY_MODULE, autonomy_mod)
    monkeypatch.setattr(rf_pkg, "autonomy", autonomy_mod, raising=False)
    return seen


class _FakeStageDB:
    def insert_audit_entry(self, **kwargs):
        return None

    def find_audit_entry(self, job_id, action):
        return None

    def update_review_job_state(self, job_id, state):
        return True

    def consume_authorization(self, auth_id):
        return None


class _FakeStageQueue:
    def __init__(self):
        self.db = _FakeStageDB()
        self.authorize_calls = []

    def authorize_merge(self, job_id, actor=None):
        self.authorize_calls.append((job_id, actor))
        return "auth-1"


def _stage(monkeypatch):
    queue = _FakeStageQueue()
    cfg = MergeStageConfig(enabled=True, dry_run=True, live_tiers=frozenset({0}))
    stage = MergeStage(queue, cfg, novelty_detector=None)
    # Pin the L2 judgment layer off: this test is about the tier consult only.
    monkeypatch.setattr(
        MergeStage,
        "_judgment_holds_for_human",
        lambda self, job, job_id, tier: False,
    )
    return stage, queue


def _docs_job():
    # Classification is evidence-based (changed paths), never the
    # self-attested change_class attribute: real docs path evidence.
    return SimpleNamespace(
        review_job_id="job-r2",
        risk_tier=0,
        change_class="docs",
        changed_paths_json='["docs/guide.md"]',
        deterministic_verdict="CLEAN",
    )


# ── fail-first: tier 1 must reach can_auto_merge ──────────────────────


def test_consult_sees_real_tier_one_from_promoted_ledger(monkeypatch, tmp_path):
    """The consult must pass tier=1 to can_auto_merge with a T1 ledger.

    FAILS pre-fix: int(dict) raises TypeError, the except swallows it,
    tier is forced to 0, and seen["tier"] == 0.
    """
    ledger = _make_t1_ledger(tmp_path)
    _install_trust_ledger(monkeypatch, ledger)
    seen = _install_recording_autonomy(monkeypatch)
    stage, _queue = _stage(monkeypatch)

    allowed, _reason = stage._autonomy_consult(_docs_job())

    assert seen["tier"] == 1, (
        f"consult saw tier={seen.get('tier')!r}; the real tier_status() dict "
        "was not read (TypeError swallowed -> tier forced to 0)"
    )
    assert seen["change_class"] == "docs"
    assert seen["deterministic_verdict"] == "CLEAN"
    assert seen["brake_engaged"] is False
    assert allowed is True


def test_consult_t1_activates_end_to_end_with_real_autonomy(monkeypatch, tmp_path):
    """Real ledger (T1) + REAL autonomy.can_auto_merge: consult must allow.

    Pre-fix this refuses with "t0_no_auto_merge" (tier forced to 0).
    Post-fix it returns (True, "auto_merge_allowed") -- the T1 path that
    the bug kept permanently inert.
    """
    ledger = _make_t1_ledger(tmp_path)
    _install_trust_ledger(monkeypatch, ledger)
    # Use the real autonomy module: consult does `from ... import autonomy`,
    # so make sure sys.modules holds the genuine module and rf_pkg does too.
    monkeypatch.setitem(sys.modules, _AUTONOMY_MODULE, autonomy_module)
    monkeypatch.setattr(rf_pkg, "autonomy", autonomy_module, raising=False)
    monkeypatch.delenv("PRISMATIC_AUTONOMY_ENABLED", raising=False)
    stage, _queue = _stage(monkeypatch)

    allowed, reason = stage._autonomy_consult(_docs_job())

    assert allowed is True, f"expected T1 consult to allow, got {reason!r}"
    assert reason == "auto_merge_allowed"


# ── negative paths: still fail closed ────────────────────────────────


def test_consult_missing_tier_key_degrades_to_tier_zero(monkeypatch):
    """A ledger whose status dict lacks 'current_tier' reads as tier 0."""

    class _KeylessLedger:
        def tier_status(self):
            return {}  # malformed: no current_tier

    _install_trust_ledger(monkeypatch, _KeylessLedger())
    seen = _install_recording_autonomy(monkeypatch)
    stage, _queue = _stage(monkeypatch)

    allowed, _reason = stage._autonomy_consult(_docs_job())

    assert seen["tier"] == 0
    assert allowed is False  # tier 0 never auto-merges


def test_consult_unparseable_tier_value_degrades_to_tier_zero(monkeypatch):
    """A non-numeric tier value must not escape the fail-closed except."""

    class _GarbageLedger:
        def tier_status(self):
            return {"current_tier": "not-a-tier"}

    _install_trust_ledger(monkeypatch, _GarbageLedger())
    seen = _install_recording_autonomy(monkeypatch)
    stage, _queue = _stage(monkeypatch)

    allowed, _reason = stage._autonomy_consult(_docs_job())

    assert seen["tier"] == 0
    assert allowed is False


def test_consult_string_tier_value_is_accepted(monkeypatch):
    """'1' parses to 1 -- .get() + int() keeps working for str values."""

    class _StringTierLedger:
        def tier_status(self):
            return {"current_tier": "1"}

    _install_trust_ledger(monkeypatch, _StringTierLedger())
    seen = _install_recording_autonomy(monkeypatch)
    stage, _queue = _stage(monkeypatch)

    allowed, _reason = stage._autonomy_consult(_docs_job())

    assert seen["tier"] == 1
    assert allowed is True
