"""R-2 (post-ceremony): the tier consult takes its tier from the arming record.

The single T1 arming ceremony replaced the ledger-``current_tier`` consult:
``MergeStage.process()`` consults the signed ``t1_armed`` record once and
passes the record's tier into ``_autonomy_consult`` as ``arming_tier``.
The old contract — consult reads ``TrustLedger().tier_status()`` and the
``PRISMATIC_AUTONOMY_ENABLED`` brake — is retired:

- the consult NEVER reads the trust ledger's ``current_tier`` anymore;
- the brake is no longer part of merge authorization (emergency stop is
  the disarm ceremony), so ``can_auto_merge`` always sees
  ``brake_engaged=False``.

These tests pin the new contract with a recording fake for
``autonomy.can_auto_merge``.
"""

import sys
import types
from types import SimpleNamespace

import prismatic.review_factory as rf_pkg
from prismatic.review_factory.merge_stage import MergeStage, MergeStageConfig

_AUTONOMY_MODULE = "prismatic.review_factory.autonomy"


def _use_real_autonomy():
    """Point the lazy autonomy import at the REAL module, restoring state after.

    ``import_module`` sets the ``autonomy`` attribute on the
    ``prismatic.review_factory`` package as a side effect; this context
    manager removes/restores both the attribute and the sys.modules entry
    so later tests are unaffected.
    """
    import contextlib
    import importlib

    @contextlib.contextmanager
    def _ctx():
        attr_sentinel = object()
        prev_attr = getattr(rf_pkg, "autonomy", attr_sentinel)
        prev_mod = sys.modules.get(_AUTONOMY_MODULE, attr_sentinel)
        real = importlib.import_module(_AUTONOMY_MODULE)
        sys.modules[_AUTONOMY_MODULE] = real
        rf_pkg.autonomy = real
        try:
            yield real
        finally:
            if prev_attr is attr_sentinel:
                try:
                    delattr(rf_pkg, "autonomy")
                except AttributeError:
                    pass
            else:
                rf_pkg.autonomy = prev_attr
            if prev_mod is attr_sentinel:
                sys.modules.pop(_AUTONOMY_MODULE, None)
            else:
                sys.modules[_AUTONOMY_MODULE] = prev_mod

    return _ctx()


def _install_recording_autonomy(monkeypatch):
    """Stub autonomy with a recording fake that captures can_auto_merge kwargs."""
    seen = {}

    def fake_can_auto_merge(**kwargs):
        seen.clear()
        seen.update(kwargs)
        return SimpleNamespace(allowed=kwargs["tier"] != 0, reason="recorded")

    autonomy_mod = types.ModuleType(_AUTONOMY_MODULE)
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
    monkeypatch.setattr(
        MergeStage,
        "_judgment_holds_for_human",
        lambda self, job, job_id, tier: False,
    )
    return stage, queue


def _docs_job():
    return SimpleNamespace(
        review_job_id="job-r2",
        risk_tier=0,
        change_class="docs",
        changed_paths_json='["docs/guide.md"]',
        deterministic_verdict="CLEAN",
    )


# ── the consult takes its tier from the arming record ─────────────────


def test_consult_passes_arming_tier_to_can_auto_merge(monkeypatch):
    """The tier can_auto_merge sees is the arming record's tier."""
    seen = _install_recording_autonomy(monkeypatch)
    stage, _queue = _stage(monkeypatch)

    allowed, _reason = stage._autonomy_consult(_docs_job(), arming_tier=1)

    assert seen["tier"] == 1
    assert seen["change_class"] == "docs"
    assert seen["deterministic_verdict"] == "CLEAN"
    assert allowed is True


def test_consult_tier_zero_refuses(monkeypatch):
    """arming_tier=0 (no record / bad record) never auto-merges."""
    seen = _install_recording_autonomy(monkeypatch)
    stage, _queue = _stage(monkeypatch)

    allowed, _reason = stage._autonomy_consult(_docs_job(), arming_tier=0)

    assert seen["tier"] == 0
    assert allowed is False


def test_consult_ignores_ledger_current_tier(monkeypatch):
    """The consult never reads TrustLedger().tier_status() anymore.

    A ledger promoted to T1 must NOT arm the consult by itself: with
    arming_tier=0 the consult refuses even when current_tier is 1.
    """
    seen = _install_recording_autonomy(monkeypatch)

    import prismatic.review_factory.trust as trust_mod

    class _PromotedLedger:
        def tier_status(self):
            return {"current_tier": 1}

    monkeypatch.setattr(trust_mod, "TrustLedger", lambda *a, **k: _PromotedLedger())
    stage, _queue = _stage(monkeypatch)

    allowed, _reason = stage._autonomy_consult(_docs_job(), arming_tier=0)
    assert seen["tier"] == 0
    assert allowed is False


def test_consult_brake_retired_from_merge_authorization(monkeypatch):
    """PRISMATIC_AUTONOMY_ENABLED no longer gates merges.

    The emergency stop is the disarm ceremony; the consult always passes
    brake_engaged=False to can_auto_merge even with the brake env var set
    to its engaged value.
    """
    monkeypatch.setenv("PRISMATIC_AUTONOMY_ENABLED", "0")
    seen = _install_recording_autonomy(monkeypatch)
    stage, _queue = _stage(monkeypatch)

    allowed, _reason = stage._autonomy_consult(_docs_job(), arming_tier=1)

    assert seen["brake_engaged"] is False
    assert allowed is True


def test_consult_with_real_autonomy_allows_t1(monkeypatch):
    """Real autonomy.can_auto_merge + arming_tier=1: the T1 path opens."""
    monkeypatch.delenv("PRISMATIC_AUTONOMY_ENABLED", raising=False)
    with _use_real_autonomy():
        stage, _queue = _stage(monkeypatch)

        allowed, reason = stage._autonomy_consult(_docs_job(), arming_tier=1)

    assert allowed is True, f"expected T1 consult to allow, got {reason!r}"
    assert reason == "auto_merge_allowed"


def test_consult_with_real_autonomy_refuses_tier_zero(monkeypatch):
    """Real autonomy.can_auto_merge + arming_tier=0: tier 0 never merges."""
    monkeypatch.delenv("PRISMATIC_AUTONOMY_ENABLED", raising=False)
    with _use_real_autonomy():
        stage, _queue = _stage(monkeypatch)

        allowed, _reason = stage._autonomy_consult(_docs_job(), arming_tier=0)

    assert allowed is False
