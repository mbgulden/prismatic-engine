"""Tests for prismatic.review_factory.attention_routing (Chunk 6, shadow mode).

The router is advisory-only: scores are logged, never acted on. Jev is
consulted only on the deterministic novelty tripwire, through a
default-off gate, and its advice never rewrites the score or band.
"""

import json
from pathlib import Path

import pytest
import yaml

from prismatic.jev import DecisionError
from prismatic.review_factory.attention_routing import (
    WEIGHTS_VERSION,
    AttentionConfigError,
    AttentionRouter,
    PRInput,
)

SPEC = Path(__file__).resolve().parent.parent / "spec" / "attention_weights_v1.yaml"

MASTER_ENV = "SWARMJEV_ENABLED"
SITE_ENV = "SWARMJEV_CALLSITE_ATTENTION_ROUTING_ENABLED"


class FakeResult:
    def __init__(self, payload):
        self._payload = payload

    def to_audit_dict(self):
        return dict(self._payload)


class StubClient:
    def __init__(self, risk=95.0, confidence=0.8):
        self.calls = []
        self.payload = {
            "backend": "stub",
            "latency_ms": 1.0,
            "answers": {
                "risk_advice": {
                    "type": "score",
                    "score": risk,
                    "confidence": confidence,
                },
                "confident": {
                    "type": "noul",
                    "probability": confidence,
                    "confidence": confidence,
                },
            },
        }

    def decide(self, state, questions, *, on_error="raise"):
        self.calls.append({"state": state, "questions": questions})
        return FakeResult(self.payload)


class ExplodingClient:
    def __init__(self):
        self.calls = []

    def decide(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("Jev backend must not be called on this path")


class ErrorClient:
    def decide(self, *args, **kwargs):
        raise DecisionError("backend exploded")


def _router(tmp_path, client=None, known_paths=None):
    audit = tmp_path / "audit.jsonl"
    return AttentionRouter(
        spec_path=SPEC,
        audit_path=str(audit),
        client_factory=lambda: client if client is not None else ExplodingClient(),
        known_paths=set() if known_paths is None else set(known_paths),
    ), audit


def _gate_on(monkeypatch):
    monkeypatch.setenv(MASTER_ENV, "1")
    monkeypatch.setenv(SITE_ENV, "1")


def _gate_off(monkeypatch):
    monkeypatch.delenv(MASTER_ENV, raising=False)
    monkeypatch.delenv(SITE_ENV, raising=False)


def _rows(audit):
    return [json.loads(line) for line in Path(audit).read_text().splitlines()]


def _pr(**kw):
    base = dict(number=1, head_sha="abc")
    base.update(kw)
    return PRInput(**base)


# -- deterministic scoring ----------------------------------------------


def test_size_scaling_saturates(tmp_path):
    r, _ = _router(tmp_path, known_paths={"a.py"})
    small = r.score_pr(_pr(additions=25, deletions=25, files=("a.py",)))
    med = r.score_pr(_pr(additions=250, deletions=250, files=("a.py",)))
    huge = r.score_pr(_pr(additions=2500, deletions=2500, files=("a.py",)))
    assert small.score < med.score < huge.score
    # diminishing: 10x lines from med->huge gains less than small->med
    assert (huge.score - med.score) < (med.score - small.score)


def test_blast_radius_critical_paths(tmp_path):
    r, _ = _router(tmp_path, known_paths={"docs/x.md", "prismatic/deploy/y.py"})
    docs = r.score_pr(_pr(additions=100, deletions=0, files=("docs/x.md",)))
    deploy = r.score_pr(
        _pr(additions=100, deletions=0, files=("prismatic/deploy/y.py",))
    )
    assert deploy.subscores["blast_radius"] > docs.subscores["blast_radius"]
    assert deploy.score > docs.score


def test_first_time_contributor_novelty(tmp_path):
    r, _ = _router(tmp_path, known_paths={"a.py"})
    regular = r.score_pr(_pr(additions=50, files=("a.py",)))
    newcomer = r.score_pr(
        _pr(additions=50, files=("a.py",), is_first_time_contributor=True)
    )
    assert newcomer.subscores["novelty"] > regular.subscores["novelty"]


def test_unknown_error_class_trips_novelty(tmp_path, monkeypatch):
    _gate_off(monkeypatch)
    r, _ = _router(tmp_path, known_paths={"a.py"})
    known = r.score_pr(_pr(files=("a.py",), error_classes=("AssertionError",)))
    novel = r.score_pr(_pr(files=("a.py",), error_classes=("QuantumFluxError",)))
    assert novel.subscores["novelty"] >= known.subscores["novelty"]
    # tripwire fires -> gate closed is recorded (gate off here)
    assert novel.jev_status == "gate_closed"
    assert known.jev_status == "not_consulted"


def test_failing_checks_raise_score(tmp_path):
    r, _ = _router(tmp_path, known_paths={"a.py"})
    clean = r.score_pr(_pr(files=("a.py",)))
    failing = r.score_pr(_pr(files=("a.py",), failing_checks=3))
    assert failing.subscores["checks"] > clean.subscores["checks"]


def test_history_rollbacks_raise_score(tmp_path):
    r, _ = _router(tmp_path, known_paths={"a.py"})
    clean = r.score_pr(_pr(files=("a.py",)))
    risky = r.score_pr(_pr(files=("a.py",), prior_rollbacks_by_author=4))
    assert risky.subscores["history"] > clean.subscores["history"]


def test_band_thresholds(tmp_path):
    r, _ = _router(tmp_path, known_paths={"a.py"})
    low = r.score_pr(_pr(files=("a.py",)))
    assert low.band == "low"
    # everything maxed: critical-path files, huge, failing, history, novelty
    crit = r.score_pr(
        _pr(
            additions=2000,
            deletions=2000,
            files=("prismatic/deploy/a.py", "prismatic/jev/b.py"),
            is_first_time_contributor=True,
            failing_checks=5,
            prior_rollbacks_by_author=4,
            recent_ci_failure_rate=1.0,
            error_classes=("QuantumFluxError",),
        )
    )
    assert crit.band == "critical"
    assert crit.score <= 100.0
    mid = r.score_pr(
        _pr(
            additions=400,
            deletions=100,
            files=("prismatic/review_factory/merge_executor.py",),
            failing_checks=2,
        )
    )
    assert mid.band in ("medium", "high")


def test_top_factors_explain_score(tmp_path):
    r, _ = _router(tmp_path, known_paths={"a.py"})
    result = r.score_pr(
        _pr(additions=1500, files=("prismatic/deploy/a.py",), failing_checks=1)
    )
    assert len(result.top_factors) == 3
    assert any("blast_radius" in f or "size" in f for f in result.top_factors)


def test_empty_pr_low_score(tmp_path):
    r, _ = _router(tmp_path)
    result = r.score_pr(_pr())
    assert result.score == 0.0
    assert result.band == "low"


def test_score_bounded(tmp_path):
    r, _ = _router(tmp_path)
    result = r.score_pr(
        _pr(
            additions=10**7,
            deletions=10**7,
            files=("x.py",),
            failing_checks=10**6,
            prior_rollbacks_by_author=10**6,
            recent_ci_failure_rate=5.0,  # out of range input: clamped
        )
    )
    assert 0.0 <= result.score <= 100.0


# -- Jev exception path -------------------------------------------------


def test_jev_novelty_advice_is_advisory_only(tmp_path, monkeypatch):
    _gate_on(monkeypatch)
    client = StubClient(risk=95.0)
    r, audit = _router(tmp_path, client)  # known_paths empty -> new path tripwire
    before = r.score_pr(
        _pr(additions=10, files=("brand/new/path.py",)),
    )
    assert before.jev_status == "advised"
    assert before.jev_advice is not None
    assert before.jev_advice["answers"]["risk_advice"]["score"] == 95.0
    # deterministic score/band computed without any Jev influence:
    r2, _ = _router(tmp_path, ExplodingClient())
    plain = r2.score_pr(_pr(additions=10, files=("brand/new/path.py",)))
    assert before.score == plain.score
    assert before.band == plain.band
    row = _rows(audit)[0]
    assert row["advisory_only"] is True
    assert "block" not in row and "gate" not in row and "merge_allowed" not in row


def test_jev_not_consulted_without_novelty(tmp_path, monkeypatch):
    _gate_on(monkeypatch)  # gate open, but no tripwire
    client = ExplodingClient()
    r, _ = _router(tmp_path, client, known_paths={"a.py"})
    result = r.score_pr(_pr(additions=50, files=("a.py",)))
    assert result.jev_status == "not_consulted"
    assert client.calls == []


def test_gate_default_off(tmp_path, monkeypatch):
    _gate_off(monkeypatch)
    client = ExplodingClient()
    r, audit = _router(tmp_path, client)  # tripwire fires (new path)
    result = r.score_pr(_pr(files=("brand/new.py",)))
    assert result.jev_status == "gate_closed"
    assert result.jev_advice is None
    assert client.calls == []
    assert _rows(audit)[0]["jev_status"] == "gate_closed"


def test_jev_error_fail_closed(tmp_path, monkeypatch):
    _gate_on(monkeypatch)
    r, audit = _router(tmp_path, ErrorClient())
    result = r.score_pr(_pr(files=("brand/new.py",)))
    assert result.jev_status == "errored"
    assert result.score >= 0.0  # deterministic score stands
    assert _rows(audit)[0]["jev_status"] == "errored"


def test_jev_score_question_scale(tmp_path, monkeypatch):
    _gate_on(monkeypatch)
    client = StubClient()
    r, _ = _router(tmp_path, client)
    r.score_pr(_pr(files=("brand/new.py",)))
    questions = client.calls[0]["questions"]
    score_q = next(q for q in questions if q.name == "risk_advice")
    assert (score_q.min, score_q.max) == (0.0, 100.0)


# -- config discipline --------------------------------------------------


def test_missing_weights_invalid(tmp_path):
    with pytest.raises(AttentionConfigError):
        AttentionRouter(spec_path=tmp_path / "nope.yaml")


def test_malformed_weights_fail_closed(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: attention-weights-v1\nweights: [oops\n")
    with pytest.raises(AttentionConfigError):
        AttentionRouter(spec_path=bad)


def test_wrong_version_rejected(tmp_path):
    bad = tmp_path / "v2.yaml"
    bad.write_text("version: attention-weights-v2\n")
    with pytest.raises(AttentionConfigError):
        AttentionRouter(spec_path=bad)


def test_incomplete_weights_rejected(tmp_path):
    data = yaml.safe_load(SPEC.read_text())
    del data["weights"]["checks"]
    bad = tmp_path / "partial.yaml"
    bad.write_text(yaml.safe_dump(data))
    with pytest.raises(AttentionConfigError):
        AttentionRouter(spec_path=bad)


def test_shipped_weights_load_and_versioned():
    data = yaml.safe_load(SPEC.read_text())
    assert data["version"] == WEIGHTS_VERSION
    assert set(data["weights"]) == {
        "size",
        "blast_radius",
        "novelty",
        "history",
        "checks",
    }
    assert set(data["bands"]) == {"low", "medium", "high"}


# -- audit discipline ---------------------------------------------------


def test_exactly_one_signal_per_score(tmp_path):
    r, audit = _router(tmp_path, known_paths={"a.py"})
    for _ in range(3):
        r.score_pr(_pr(files=("a.py",)))
    rows = _rows(audit)
    assert len(rows) == 3
    for row in rows:
        assert row["component"] == "attention-routing"
        assert row["weights_version"] == WEIGHTS_VERSION
        assert row["advisory_only"] is True
        assert row["action_taken"] is False
        assert "timestamp" in row


def test_audit_write_failure_does_not_change_score(tmp_path):
    audit_dir = tmp_path / "is-a-directory"
    audit_dir.mkdir()
    r = AttentionRouter(spec_path=SPEC, audit_path=str(audit_dir))
    result = r.score_pr(_pr(files=("a.py",)))
    assert result.band == "low"  # score stands


def test_advisory_only_no_gate_fields(tmp_path):
    r, audit = _router(tmp_path, known_paths={"a.py"})
    r.score_pr(_pr(files=("a.py",)))
    row = _rows(audit)[0]
    for forbidden in ("block", "gate", "merge_allowed", "should_merge", "verdict"):
        assert forbidden not in row, forbidden
    assert row["shadow"] == "shadow — no action taken"
