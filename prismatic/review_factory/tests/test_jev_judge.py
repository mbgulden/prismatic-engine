"""Tests for the L2 judgment layer (plan §9, workstream B).

Covers the hard invariants: the judge never approves, Jev never sees red,
the zero-AI path, the remit prompt snapshot, tiered invocation, and the
default-off gate. All tests run with no credentials and no network.
"""

from __future__ import annotations

import os
import re
import socket
from dataclasses import replace
from types import SimpleNamespace

import pytest

from prismatic.jev.client import DecisionResult
from prismatic.jev.errors import DecisionError
from prismatic.jev.gates import CallSiteGate, apply_jev_advice
from prismatic.review_factory import judge as judge_module
from prismatic.review_factory.judge import (
    JUDGMENT_CALL_SITE,
    VERDICT_QUESTION_NAME,
    JevJudge,
    Judgment,
    NullJudge,
    artifact_from_review_job,
    build_judge,
    judgment_advice,
    judgment_earned,
    maybe_evaluate,
)
from prismatic.review_factory.review_questions import (
    QUESTION_BLOCKS,
    render_review_prompt,
)


@pytest.fixture(autouse=True)
def clear_jev_env(monkeypatch):
    """Every test starts with no SWARMJEV_* configuration."""
    for name in list(os.environ):
        if name.startswith("SWARMJEV_"):
            monkeypatch.delenv(name, raising=False)


def _env_with_backend(monkeypatch):
    # A named (non-fallback) backend so the judge reaches the client.
    # The fake client needs no credentials and no network.
    monkeypatch.setenv("SWARMJEV_BACKEND", "openrouter")


class FakeClient:
    """Stand-in DecisionClient: no network, canned typed answers."""

    def __init__(self, *, verdict="CLEAR", abstain=False, **kwargs):
        self.calls = 0
        self.verdict = verdict
        self.abstain = abstain
        self.last_state = None
        self.extra_kwargs = kwargs

    def decide(self, state, questions, **kwargs):
        self.calls += 1
        self.last_state = state
        answers = {}
        for q in questions:
            if q.name == VERDICT_QUESTION_NAME:
                answer = q.default_answer(self.verdict)
            else:
                answer = q.default_answer("clear")
            if self.abstain:
                answer = replace(answer, abstained=True, abstain_reason="test abstain")
            answers[q.name] = answer
        return DecisionResult(
            answers=answers,
            latency_ms=1.0,
            backend="fake",
            deterministic=False,
            trace_id="trace-test-1",
        )


def _make_judge(fake):
    return JevJudge(client_factory=lambda **kwargs: fake)


# -- invariant: the judge never approves ---------------------------------------


def test_judge_never_approves():
    # Jev CLEAR advice on deterministic REPAIR/REJECT -> final stays.
    assert apply_jev_advice("REPAIR", "CLEAR") == "REPAIR"
    assert apply_jev_advice("REJECT", "CLEAR") == "REJECT"
    # Even Jev's strongest upward advice cannot launder a deterministic failure.
    assert apply_jev_advice("REPAIR", "ESCALATE") == "REPAIR"
    assert apply_jev_advice("REJECT", "ESCALATE") == "REJECT"
    # The only upward move: the judge's PAUSE maps to ESCALATE advice on CLEAN.
    assert apply_jev_advice("CLEAN", judgment_advice(_judgment("PAUSE"))) == "ESCALATE"
    assert apply_jev_advice("CLEAN", judgment_advice(_judgment("CLEAR"))) == "CLEAN"
    assert apply_jev_advice("CLEAN", judgment_advice(_judgment(None))) == "CLEAN"


def _judgment(decision):
    return Judgment(judge="jev", decision=decision, confidence=0.9)


def test_judgment_advice_vocabulary():
    # The judge speaks {CLEAR, PAUSE}; the enforcer speaks
    # {CLEAN, REPAIR, REJECT, ESCALATE}. A null judgment carries no advice.
    assert judgment_advice(_judgment("PAUSE")) == "ESCALATE"
    assert judgment_advice(_judgment("CLEAR")) == "CLEAR"
    assert judgment_advice(_judgment(None)) is None


# -- invariant: Jev never sees red ----------------------------------------------


def test_jev_never_sees_red(clear_jev_env):
    constructed = []

    def factory(**kwargs):
        fake = FakeClient(**kwargs)
        constructed.append(fake)
        return fake

    judge = JevJudge(client_factory=factory)
    artifact = {"artifact_id": "red-1"}
    for verdict in ("REPAIR", "REJECT", "bogus", ""):
        with pytest.raises(DecisionError):
            judge.evaluate(artifact, verdict)
    # The client was never even constructed: zero Jev invocations.
    assert constructed == []


# -- zero-AI path ----------------------------------------------------------------


def test_zero_ai_full_pipeline(monkeypatch, clear_jev_env):
    # No creds, no network (socket blocked): the pipeline still produces a
    # verdict record that explicitly says no judgment was applied.
    def _blocked(*args, **kwargs):
        raise OSError("network disabled in test")

    monkeypatch.setattr(socket, "socket", _blocked)

    judge = build_judge()
    assert isinstance(judge, JevJudge)
    judgment = judge.evaluate({"artifact_id": "zero-ai-1"}, "CLEAN")
    assert judgment.decision is None
    assert judgment.judge == "null"
    assert "no judgment applied" in judgment.explicit_non_claims

    # NullJudge directly: the same contract.
    null_judgment = NullJudge().evaluate({"artifact_id": "zero-ai-2"}, "CLEAN")
    assert null_judgment.decision is None
    assert "no judgment applied" in null_judgment.explicit_non_claims


# -- remit: prompt snapshot -------------------------------------------------------


def test_judge_remit():
    prompt = render_review_prompt()
    # The 5 questions appear verbatim.
    assert len(QUESTION_BLOCKS) == 5
    for block in QUESTION_BLOCKS:
        assert block in prompt
    # None of {lint, test, re-run, check} may appear as instructions:
    # strip the verbatim question blocks, then scan the remaining prose.
    remainder = prompt
    for block in QUESTION_BLOCKS:
        remainder = remainder.replace(block, "")
    assert not re.search(
        r"\b(lint|re-run|rerun|tests?|checks?)\b", remainder, re.IGNORECASE
    )


# -- tiered invocation --------------------------------------------------------------


def test_judgment_earned_rules():
    assert judgment_earned(tier=0) == (False, "tier-0")
    assert judgment_earned(tier=1) == (True, None)
    assert judgment_earned(tier=2) == (True, None)
    assert judgment_earned(tier=0, novelty_flagged=True) == (True, None)
    assert judgment_earned(tier=0, first_time_author=True) == (True, None)


def test_tiering_skips_tier0(monkeypatch, clear_jev_env):
    _env_with_backend(monkeypatch)
    fake = FakeClient()
    judge = _make_judge(fake)
    judgment = maybe_evaluate(judge, {"artifact_id": "tier0-1"}, "CLEAN", tier=0)
    assert fake.calls == 0
    assert judgment.skipped == "tier-0"
    assert judgment.decision is None


def test_tiering_invokes_tier2(monkeypatch, clear_jev_env):
    _env_with_backend(monkeypatch)
    fake = FakeClient()
    judge = _make_judge(fake)
    judgment = maybe_evaluate(judge, {"artifact_id": "tier2-1"}, "CLEAN", tier=2)
    assert fake.calls == 1
    assert judgment.decision == "CLEAR"
    assert judgment.skipped is None


# -- gate: default-off ----------------------------------------------------------------


def test_gate_default_off(monkeypatch, clear_jev_env):
    gate = CallSiteGate(JUDGMENT_CALL_SITE)
    assert gate.site_env == "SWARMJEV_CALLSITE_REVIEW_JUDGMENT_ENABLED"
    assert not gate.allow()
    judge = JevJudge()
    assert not judge.gate.allow()
    # No env -> NullJudge path inside JevJudge: zero client calls.
    fake = FakeClient()
    judge2 = _make_judge(fake)
    judgment = judge2.evaluate({"artifact_id": "gate-1"}, "CLEAN")
    assert fake.calls == 0
    assert judgment.decision is None
    assert "no judgment applied" in judgment.explicit_non_claims


# -- abstain -> ESCALATE; PAUSE -> ESCALATE ---------------------------------------------


def test_abstain_escalates(monkeypatch, clear_jev_env):
    _env_with_backend(monkeypatch)
    fake = FakeClient(abstain=True)
    judge = _make_judge(fake)
    judgment = judge.evaluate({"artifact_id": "abstain-1"}, "CLEAN")
    assert judgment.decision == "PAUSE"
    assert apply_jev_advice("CLEAN", judgment_advice(judgment)) == "ESCALATE"
    assert any(r.severity == "high" for r in judgment.reasons)


def test_pause_verdict_escalates(monkeypatch, clear_jev_env):
    _env_with_backend(monkeypatch)
    fake = FakeClient(verdict="PAUSE")
    judge = _make_judge(fake)
    judgment = judge.evaluate({"artifact_id": "pause-1"}, "CLEAN")
    assert judgment.decision == "PAUSE"
    assert judgment.trace_id == "trace-test-1"
    assert len(judgment.reasons) == 5
    assert apply_jev_advice("CLEAN", judgment_advice(judgment)) == "ESCALATE"


# -- memoization + untrusted boundary ---------------------------------------------------


def test_memoizes_on_artifact_id(monkeypatch, clear_jev_env):
    _env_with_backend(monkeypatch)
    fake = FakeClient()
    judge = _make_judge(fake)
    artifact = {"artifact_id": "memo-1"}
    first = judge.evaluate(artifact, "CLEAN")
    second = judge.evaluate(artifact, "CLEAN")
    assert fake.calls == 1
    assert first.trace_id == second.trace_id


def test_state_is_untrusted_serialized(monkeypatch, clear_jev_env):
    _env_with_backend(monkeypatch)
    fake = FakeClient()
    judge = _make_judge(fake)
    artifact = {
        "artifact_id": "untrusted-1",
        "intent": {"plan_ref": None, "brief": "do the thing", "goals": ["g1"]},
        "diff": {"unified": "diff --git a/x b/x\n+evil("},
    }
    judge.evaluate(artifact, "CLEAN")
    state = fake.last_state
    # Diff and brief cross as Untrusted spans: nonce-delimited data, never
    # bare instructions.
    assert state["diff"]["unified"].startswith("<data_")
    assert state["intent"]["brief"].startswith("<data_")
    assert state["intent"]["goals"][0].startswith("<data_")


def test_artifact_from_review_job():
    job = SimpleNamespace(
        review_job_id="job-123",
        risk_tier=2,
        base_tree="aaa",
        candidate_commit="bbb",
    )
    artifact = artifact_from_review_job(job)
    assert artifact["artifact_id"] == "job-123"
    assert artifact["tier"] == 2
    assert artifact["diff"]["base_tree"] == "aaa"
    assert artifact["diff"]["head_tree"] == "bbb"
    # Missing fields come through empty, never fabricated.
    assert artifact["intent"]["brief"] == ""
    assert artifact["diff"]["files"] == []


# -- merge_stage wiring ------------------------------------------------------------------


class _FakeDB:
    def __init__(self):
        self.audits = []

    def insert_audit_entry(self, *, actor, action, review_job_id, details):
        self.audits.append((action, details))

    def find_audit_entry(self, job_id, action):
        return None


class _FakeQueue:
    def __init__(self):
        self.db = _FakeDB()
        self.authorize_calls = 0

    def authorize_merge(self, job_id, actor=None):
        self.authorize_calls += 1
        return "auth-1"


class _StubJudge:
    name = "jev"

    def __init__(self, decision):
        self._decision = decision

    def evaluate(self, artifact, deterministic_verdict):
        assert deterministic_verdict == "CLEAN"
        return Judgment(
            judge="jev",
            decision=self._decision,
            confidence=0.9,
            trace_id="t-stub",
        )


def _with_judgment_env(monkeypatch):
    monkeypatch.setenv("SWARMJEV_ENABLED", "1")
    monkeypatch.setenv("SWARMJEV_CALLSITE_REVIEW_JUDGMENT_ENABLED", "1")


def test_merge_stage_withholds_merge_on_escalation(monkeypatch, clear_jev_env):
    from prismatic.review_factory.merge_stage import MergeStage, MergeStageConfig

    _with_judgment_env(monkeypatch)
    monkeypatch.setattr(
        judge_module, "build_judge", lambda **kwargs: _StubJudge("PAUSE")
    )
    queue = _FakeQueue()
    stage = MergeStage(
        queue=queue,
        config=MergeStageConfig(enabled=True, dry_run=True, live_tiers=frozenset({1})),
        # Screen off: the real detector would import
        # prismatic.review_factory.novelty into sys.modules and break
        # test_quarantine_routing's test_never_imports_action_paths.
        novelty_detector=None,
    )
    job = SimpleNamespace(review_job_id="job-esc-1", risk_tier=1)
    result = stage.process(job)
    assert result.action == "judgment_escalated"
    assert queue.authorize_calls == 0  # merge never authorized
    actions = [action for action, _ in queue.db.audits]
    assert "merge_stage_judgment_escalated" in actions


@pytest.fixture
def stub_merge_executor(monkeypatch):
    """Stub the merge-executor module WITHOUT importing the real one.

    Importing the real module would pull prismatic.review_factory.queue
    into sys.modules and break test_quarantine_routing's
    test_never_imports_action_paths (which asserts the shadow router never
    imports action paths). The stub only needs MergeExecutor.
    """
    import sys
    import types

    stub = types.ModuleType("prismatic.review_factory.merge_executor")

    class _FakeMergeResult:
        success = True
        merge_sha = "sha-stub"
        error = ""

    class _FakeExecutor:
        def __init__(self, **kwargs):
            pass

        def execute(self, job_id):
            return _FakeMergeResult()

    def _classify_change_class(job):
        value = getattr(job, "change_class", None)
        if isinstance(value, str) and value.strip():
            return value
        return "sensitive"

    stub.MergeExecutor = _FakeExecutor
    # Phase-3 wiring: MergeStage._autonomy_consult lazy-imports
    # classify_change_class from this module; the stub must carry it.
    stub.classify_change_class = _classify_change_class
    monkeypatch.setitem(sys.modules, "prismatic.review_factory.merge_executor", stub)
    return stub


@pytest.fixture
def stub_autonomy_allowed(monkeypatch):
    """Stub the phase-2 earned-autonomy module as allowing.

    Phase-3 wiring gates MergeStage.process() on the autonomy consult,
    which fails closed while the phase-2 module is absent. Tests that
    exercise behavior downstream of the consult opt into an allowing
    stub; the consult's own fail-closed posture is covered in
    test_earned_autonomy_wiring.py.
    """
    import sys
    import types

    autonomy = types.ModuleType("prismatic.review_factory.autonomy")
    autonomy.brake_status = lambda: {"engaged": False}
    autonomy.can_auto_merge = lambda **kwargs: SimpleNamespace(
        allowed=True, reason="auto_merge_allowed"
    )
    monkeypatch.setitem(sys.modules, "prismatic.review_factory.autonomy", autonomy)
    return autonomy


def test_merge_stage_proceeds_on_clear(
    monkeypatch, clear_jev_env, stub_merge_executor, stub_autonomy_allowed
):
    from prismatic.review_factory.merge_stage import MergeStage, MergeStageConfig

    _with_judgment_env(monkeypatch)
    monkeypatch.setattr(
        judge_module, "build_judge", lambda **kwargs: _StubJudge("CLEAR")
    )
    monkeypatch.setattr(
        MergeStage, "_stand_down_after_dry_run", lambda self, jid, aid: True
    )

    queue = _FakeQueue()
    stage = MergeStage(
        queue=queue,
        config=MergeStageConfig(enabled=True, dry_run=True, live_tiers=frozenset({1})),
        # Screen off: the real detector would import
        # prismatic.review_factory.novelty into sys.modules and break
        # test_quarantine_routing's test_never_imports_action_paths.
        novelty_detector=None,
    )
    job = SimpleNamespace(review_job_id="job-clear-1", risk_tier=1)
    result = stage.process(job)
    assert result.action == "dry_run_ok"
    assert queue.authorize_calls == 1  # CLEAR judgment lets the merge proceed


def test_merge_stage_judgment_fail_open(
    monkeypatch, clear_jev_env, stub_merge_executor, stub_autonomy_allowed
):
    # A judge failure must never break the hot path: the deterministic
    # verdict stands and the merge proceeds.
    from prismatic.review_factory.merge_stage import MergeStage, MergeStageConfig

    _with_judgment_env(monkeypatch)

    class _BrokenJudge:
        name = "jev"

        def evaluate(self, artifact, deterministic_verdict):
            raise RuntimeError("boom")

    monkeypatch.setattr(judge_module, "build_judge", lambda **kwargs: _BrokenJudge())
    monkeypatch.setattr(
        MergeStage, "_stand_down_after_dry_run", lambda self, jid, aid: True
    )

    queue = _FakeQueue()
    stage = MergeStage(
        queue=queue,
        config=MergeStageConfig(enabled=True, dry_run=True, live_tiers=frozenset({1})),
        # Screen off: the real detector would import
        # prismatic.review_factory.novelty into sys.modules and break
        # test_quarantine_routing's test_never_imports_action_paths.
        novelty_detector=None,
    )
    job = SimpleNamespace(review_job_id="job-failopen-1", risk_tier=1)
    result = stage.process(job)
    assert result.action == "dry_run_ok"
    assert queue.authorize_calls == 1
