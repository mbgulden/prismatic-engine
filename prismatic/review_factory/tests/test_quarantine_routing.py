"""Tests for prismatic.review_factory.quarantine_routing (Jev #27, shadow mode).

Conventions: every route() writes exactly one JSONL audit row to a tmp
audit path; DecisionClient is injected via client_factory stubs so no
network or credentials are ever touched. action_taken is always False.
"""

import json
import sys
from pathlib import Path

import pytest
import yaml

from prismatic.jev import DecisionError
from prismatic.review_factory.quarantine_routing import (
    POLICY_VERSION,
    QuarantineRouter,
    QuarantineRoutingConfigError,
    RouteVerdict,
    route_candidate,
)

SPEC = (
    Path(__file__).resolve().parent.parent / "spec" / "quarantine_routing_rules_v1.yaml"
)

MASTER_ENV = "SWARMJEV_ENABLED"
SITE_ENV = "SWARMJEV_CALLSITE_NOVELTY_QUARANTINE_ROUTING_ENABLED"


class FakeResult:
    def __init__(self, payload):
        self._payload = payload

    def to_audit_dict(self):
        return dict(self._payload)


class StubClient:
    """Records decide() calls; returns canned advice."""

    def __init__(self, advice_choice="proceed", confidence=0.9):
        self.calls = []
        if advice_choice == "proceed":
            probs = {"proceed": 0.85, "quarantine": 0.15}
        else:
            probs = {"proceed": 0.1, "quarantine": 0.9}
        self.payload = {
            "backend": "stub",
            "latency_ms": 1.0,
            "deterministic": False,
            "answers": {
                "route": {
                    "type": "choice",
                    "choice": advice_choice,
                    "probabilities": probs,
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
    """Proves the backend is never reached: any call raises."""

    def __init__(self):
        self.calls = []

    def decide(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("Jev backend must not be called on this path")


class ErrorClient:
    def decide(self, *args, **kwargs):
        raise DecisionError("backend exploded")


class GarbageAdviceClient:
    """Returns advice whose route choice is unusable: must fail closed."""

    def __init__(self):
        self.calls = []

    def decide(self, state, questions, *, on_error="raise"):
        self.calls.append({"state": state, "questions": questions})
        return FakeResult({"backend": "stub", "answers": {"route": {"nope": 1}}})


def _ctx(**over):
    base = {
        "precedent_matches": 0,
        "first_time_event_types": [],
        "unknown_error_classes": [],
        "has_migration": False,
        "has_test_change": False,
    }
    base.update(over)
    return base


def _familiar_candidate():
    return {
        "candidate_id": "pr:501",
        "head_sha": "abc123",
        "changed_paths": ["docs/novelty.md", "tests/test_novelty.py"],
        "author_association": "MEMBER",
        "labels": [],
        "novelty_context": _ctx(precedent_matches=5, has_test_change=True),
    }


def _gray_candidate():
    return {
        "candidate_id": "pr:502",
        "head_sha": "def456",
        "changed_paths": ["prismatic/review_factory/novelty.py"],
        "author_association": "CONTRIBUTOR",
        "labels": ["dependencies"],
        "novelty_context": _ctx(has_test_change=True),
    }


def _router(tmp_path, client=None):
    audit = tmp_path / "audit.jsonl"
    return QuarantineRouter(
        spec_path=SPEC,
        audit_path=str(audit),
        client_factory=lambda: client if client is not None else ExplodingClient(),
    ), audit


def _gate_on(monkeypatch):
    monkeypatch.setenv(MASTER_ENV, "1")
    monkeypatch.setenv(SITE_ENV, "1")


def _gate_off(monkeypatch):
    monkeypatch.delenv(MASTER_ENV, raising=False)
    monkeypatch.delenv(SITE_ENV, raising=False)


def _rows(audit):
    return [json.loads(line) for line in Path(audit).read_text().splitlines()]


# -- deterministic rules ------------------------------------------------


def test_familiar_short_circuits_jev(tmp_path, monkeypatch):
    _gate_on(monkeypatch)  # gate open: Jev must STILL not be consulted
    client = ExplodingClient()
    router, audit = _router(tmp_path, client)
    result = router.route(_familiar_candidate())
    assert result.verdict is RouteVerdict.PROCEED
    assert result.deterministic_verdict is RouteVerdict.PROCEED
    assert result.matched_rule == "familiar-precedent"
    assert result.jev_status == "not_consulted"
    assert result.jev_advice is None
    assert client.calls == []
    row = _rows(audit)[0]
    assert row["verdict"] == "proceed"
    assert row["action_taken"] is False


def test_migration_without_tests_short_circuits_jev(tmp_path, monkeypatch):
    _gate_on(monkeypatch)
    client = ExplodingClient()
    router, _ = _router(tmp_path, client)
    candidate = _familiar_candidate()
    candidate["changed_paths"] = ["prismatic/db/migrations/0007_add_table.py"]
    candidate["novelty_context"] = _ctx(
        precedent_matches=9, has_migration=True, has_test_change=False
    )
    result = router.route(candidate)
    assert result.verdict is RouteVerdict.QUARANTINE
    assert result.matched_rule == "migration-without-tests"
    assert result.jev_status == "not_consulted"
    assert client.calls == []


def test_unknown_error_classes_short_circuits_jev(tmp_path, monkeypatch):
    _gate_on(monkeypatch)
    client = ExplodingClient()
    router, _ = _router(tmp_path, client)
    candidate = _familiar_candidate()
    candidate["novelty_context"] = _ctx(
        precedent_matches=9, unknown_error_classes=["WeirdError"]
    )
    result = router.route(candidate)
    assert result.verdict is RouteVerdict.QUARANTINE
    assert result.matched_rule == "unknown-error-classes"
    assert client.calls == []


def test_first_time_event_types_short_circuits_jev(tmp_path, monkeypatch):
    _gate_on(monkeypatch)
    client = ExplodingClient()
    router, _ = _router(tmp_path, client)
    candidate = _familiar_candidate()
    candidate["novelty_context"] = _ctx(
        precedent_matches=9, first_time_event_types=["workflow_run"]
    )
    result = router.route(candidate)
    assert result.verdict is RouteVerdict.QUARANTINE
    assert result.matched_rule == "first-time-event-types"
    assert client.calls == []


def test_quarantine_triggers_win_over_familiar(tmp_path, monkeypatch):
    """Adversarial: high precedent + familiar paths, but a migration with no
    tests — the quarantine trigger must win (fail-closed ordering)."""
    _gate_on(monkeypatch)
    client = ExplodingClient()
    router, _ = _router(tmp_path, client)
    candidate = _familiar_candidate()
    candidate["novelty_context"] = _ctx(
        precedent_matches=99, has_migration=True, has_test_change=False
    )
    result = router.route(candidate)
    assert result.verdict is RouteVerdict.QUARANTINE
    assert result.matched_rule == "migration-without-tests"
    assert client.calls == []


def test_malformed_candidate_fail_closed(tmp_path, monkeypatch):
    _gate_on(monkeypatch)
    router, audit = _router(tmp_path, ExplodingClient())
    bad_inputs = [
        None,
        {},
        {"candidate_id": ""},
        {"candidate_id": "pr:1"},  # missing changed_paths + novelty_context
        {
            "candidate_id": "pr:1",
            "changed_paths": ["docs/x.md"],
            "novelty_context": "not-a-dict",
        },
        {
            "candidate_id": "pr:1",
            "changed_paths": ["docs/x.md", 42],
            "novelty_context": _ctx(precedent_matches=5),
        },
        {
            "candidate_id": "pr:1",
            "changed_paths": ["docs/x.md"],
            "novelty_context": _ctx(precedent_matches=-1),
        },
        "just-a-string",
    ]
    for bad in bad_inputs:
        result = router.route(bad)
        assert result.verdict is RouteVerdict.QUARANTINE, bad
        assert result.matched_rule == "malformed-candidate", bad
        assert result.jev_status == "not_consulted", bad
        assert result.action_taken is False, bad
    rows = _rows(audit)
    assert len(rows) == len(bad_inputs)
    assert all(row["verdict"] == "quarantine" for row in rows)


# -- Jev gray zone --------------------------------------------------------


def test_gate_default_off_gray_fails_closed(tmp_path, monkeypatch):
    _gate_off(monkeypatch)
    client = ExplodingClient()
    router, audit = _router(tmp_path, client)
    result = router.route(_gray_candidate())
    assert result.verdict is RouteVerdict.QUARANTINE  # cannot prove familiar
    assert result.deterministic_verdict is None
    assert result.jev_status == "gate_closed"
    assert result.jev_advice is None
    assert result.action_taken is False
    assert client.calls == []
    row = _rows(audit)[0]
    assert row["jev_status"] == "gate_closed"
    assert row["gate"]["site_env"] == SITE_ENV
    assert row["gate"]["allowed"] is False


def test_jev_error_fail_closed(tmp_path, monkeypatch):
    _gate_on(monkeypatch)
    router, audit = _router(tmp_path, ErrorClient())
    result = router.route(_gray_candidate())
    assert result.verdict is RouteVerdict.QUARANTINE
    assert result.jev_status == "errored"
    assert result.action_taken is False
    row = _rows(audit)[0]
    assert row["jev_status"] == "errored"
    assert row["verdict"] == "quarantine"
    assert row["shadow"] == "shadow — no action taken"


def test_garbage_advice_fail_closed(tmp_path, monkeypatch):
    _gate_on(monkeypatch)
    router, _ = _router(tmp_path, GarbageAdviceClient())
    result = router.route(_gray_candidate())
    assert result.verdict is RouteVerdict.QUARANTINE
    assert result.jev_status == "errored"


def test_gray_zone_jev_consulted_exactly_once(tmp_path, monkeypatch):
    _gate_on(monkeypatch)
    client = StubClient(advice_choice="proceed")
    router, audit = _router(tmp_path, client)
    result = router.route(_gray_candidate())
    assert len(client.calls) == 1
    assert result.jev_status == "advised"
    assert result.verdict is RouteVerdict.PROCEED
    assert result.deterministic_verdict is None
    assert result.jev_advice is not None
    assert result.jev_advice["answers"]["route"]["choice"] == "proceed"
    assert result.jev_advice["answers"]["route"]["probabilities"] == {
        "proceed": 0.85,
        "quarantine": 0.15,
    }
    assert result.jev_advice["backend"] == "stub"
    assert "latency_ms" in result.jev_advice
    assert result.action_taken is False
    row = _rows(audit)[0]
    assert row["verdict"] == "proceed"
    assert row["matched_rule"] is None
    assert row["action_taken"] is False


def test_gray_zone_jev_advising_quarantine(tmp_path, monkeypatch):
    _gate_on(monkeypatch)
    client = StubClient(advice_choice="quarantine")
    router, _ = _router(tmp_path, client)
    result = router.route(_gray_candidate())
    assert result.jev_status == "advised"
    assert result.verdict is RouteVerdict.QUARANTINE


def test_choice_options_exactly_two_in_order(tmp_path, monkeypatch):
    _gate_on(monkeypatch)
    client = StubClient()
    router, _ = _router(tmp_path, client)
    router.route(_gray_candidate())
    questions = client.calls[0]["questions"]
    choice = next(q for q in questions if q.name == "route")
    assert list(choice.options) == ["proceed", "quarantine"]
    noul = next(q for q in questions if q.name == "confident")
    assert noul.name == "confident"


def test_no_downgrade_eager_jev_never_consulted_on_familiar(tmp_path, monkeypatch):
    """Adversarial: gate open + backend eager to say 'quarantine' on a
    familiar candidate — the deterministic PROCEED must survive untouched."""
    _gate_on(monkeypatch)
    client = StubClient(advice_choice="quarantine")
    router, _ = _router(tmp_path, client)
    result = router.route(_familiar_candidate())
    assert result.verdict is RouteVerdict.PROCEED
    assert result.matched_rule == "familiar-precedent"
    assert result.jev_status == "not_consulted"
    assert result.jev_advice is None
    assert client.calls == []  # short-circuit: never consulted


def test_state_bounds_and_no_credential_keys(tmp_path, monkeypatch):
    _gate_on(monkeypatch)
    client = StubClient()
    router, _ = _router(tmp_path, client)
    candidate = _gray_candidate()
    candidate["changed_paths"] = ["x" * 5000] * 250
    router.route(candidate)
    state = client.calls[0]["state"]
    assert len(state["changed_paths"]) == 200
    assert all(len(p) <= 2000 for p in state["changed_paths"])
    assert "SWARMJEV" not in json.dumps(state)  # no credential-shaped keys


# -- config discipline --------------------------------------------------


def test_missing_config_invalid(tmp_path):
    with pytest.raises(QuarantineRoutingConfigError):
        QuarantineRouter(spec_path=tmp_path / "nope.yaml")


def test_malformed_config_fail_closed(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "version: quarantine-routing-rules-v1\nfamiliar_precedent_min: [oops\n"
    )
    with pytest.raises(QuarantineRoutingConfigError):
        QuarantineRouter(spec_path=bad)


def test_wrong_version_rejected(tmp_path):
    bad = tmp_path / "v2.yaml"
    bad.write_text("version: quarantine-routing-rules-v2\n")
    with pytest.raises(QuarantineRoutingConfigError):
        QuarantineRouter(spec_path=bad)


def test_bad_regex_rejected(tmp_path):
    bad = tmp_path / "regex.yaml"
    bad.write_text(
        "version: quarantine-routing-rules-v1\n"
        "familiar_precedent_min: 3\n"
        "familiar_path_patterns: ['([unclosed']\n"
        "deterministic_quarantine_rules:\n"
        "  - name: migration-without-tests\n"
        "  - name: unknown-error-classes\n"
        "  - name: first-time-event-types\n"
    )
    with pytest.raises(QuarantineRoutingConfigError):
        QuarantineRouter(spec_path=bad)


def test_unknown_rule_name_rejected(tmp_path):
    bad = tmp_path / "rules.yaml"
    bad.write_text(
        "version: quarantine-routing-rules-v1\n"
        "familiar_precedent_min: 3\n"
        "familiar_path_patterns: ['^docs/']\n"
        "deterministic_quarantine_rules:\n"
        "  - name: migration-without-tests\n"
        "  - name: brand-new-trigger\n"
        "  - name: first-time-event-types\n"
    )
    with pytest.raises(QuarantineRoutingConfigError):
        QuarantineRouter(spec_path=bad)


def test_shipped_policy_loads_and_versioned():
    data = yaml.safe_load(SPEC.read_text())
    assert data["version"] == POLICY_VERSION
    assert data["familiar_precedent_min"] == 3
    names = [r["name"] for r in data["deterministic_quarantine_rules"]]
    assert len(names) == len(set(names))
    router = QuarantineRouter(spec_path=SPEC, client_factory=ExplodingClient)
    assert router._policy.version == POLICY_VERSION


# -- audit discipline ---------------------------------------------------


def test_exactly_one_audit_signal_per_route(tmp_path):
    router, audit = _router(tmp_path)
    router.route(_familiar_candidate())
    router.route(_gray_candidate())
    router.route(None)
    rows = _rows(audit)
    assert len(rows) == 3
    for row in rows:
        assert row["component"] == "novelty-quarantine-routing"
        assert row["policy_version"] == POLICY_VERSION
        assert row["action_taken"] is False
        assert row["shadow"] == "shadow — no action taken"
        assert "block" not in row and "halt" not in row
        assert "quarantine_executed" not in row
        assert "timestamp" in row


def test_audit_write_failure_does_not_change_answer(tmp_path):
    audit_dir = tmp_path / "is-a-directory"
    audit_dir.mkdir()
    router = QuarantineRouter(
        spec_path=SPEC, audit_path=str(audit_dir), client_factory=ExplodingClient
    )
    result = router.route(_familiar_candidate())
    assert result.verdict is RouteVerdict.PROCEED  # answer stands


def test_never_imports_action_paths():
    import prismatic.review_factory.quarantine_routing  # noqa: F401

    forbidden = [
        "prismatic.review_factory.novelty",  # novelty enforce path + registry
        "prismatic.review_factory.watchdog",  # watchdog halt path
        "prismatic.deploy.receiver",  # deploy receiver
        "prismatic.review_factory.merge_executor",
        "prismatic.review_factory.queue",
    ]
    for mod in forbidden:
        assert mod not in sys.modules, mod


def test_route_candidate_convenience_wrapper(tmp_path):
    audit = tmp_path / "audit.jsonl"
    result = route_candidate(
        _familiar_candidate(),
        spec_path=SPEC,
        audit_path=str(audit),
        client_factory=ExplodingClient,
    )
    assert result.verdict is RouteVerdict.PROCEED
    assert len(_rows(audit)) == 1
