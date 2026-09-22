"""Tests for prismatic.review_factory.failure_diagnosis (Chunk 6, shadow mode).

Conventions: every diagnose() writes exactly one JSONL audit row to a tmp
audit path; DecisionClient is injected via client_factory stubs so no
network or credentials are ever touched.
"""

import json
import sys
from pathlib import Path

import pytest
import yaml

from prismatic.jev import DecisionError
from prismatic.review_factory.failure_diagnosis import (
    POLICY_VERSION,
    ROUTES,
    AttemptOutcome,
    Diagnosis,
    DiagnosisConfigError,
    FailureDiagnoser,
    diagnose_ci_failure,
    diagnose_deploy_failure,
    diagnose_watchdog_trip,
)

SPEC = Path(__file__).resolve().parent.parent / "spec" / "diagnosis_signatures_v1.yaml"

MASTER_ENV = "SWARMJEV_ENABLED"
SITE_ENV = "SWARMJEV_CALLSITE_FAILURE_DIAGNOSIS_ENABLED"


class FakeResult:
    def __init__(self, payload):
        self._payload = payload

    def to_audit_dict(self):
        return dict(self._payload)


class StubClient:
    """Records decide() calls; returns canned advice."""

    def __init__(self, advice_choice="infra", probs=None, confidence=0.9):
        self.calls = []
        probs = probs or {
            "infra": 0.9,
            "code": 0.05,
            "flake": 0.03,
            "deploy": 0.02,
        }
        self.payload = {
            "backend": "stub",
            "latency_ms": 1.0,
            "answers": {
                "diagnosis_advice": {
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


def _diagnoser(tmp_path, client=None):
    audit = tmp_path / "audit.jsonl"
    return FailureDiagnoser(
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


def test_infra_signature_short_circuits_jev(tmp_path, monkeypatch):
    _gate_on(monkeypatch)  # gate open: Jev must STILL not be consulted
    client = ExplodingClient()
    d, audit = _diagnoser(tmp_path, client)
    result = diagnose_ci_failure(
        d, error_text="Connection reset by peer during pip install"
    )
    assert result.diagnosis is Diagnosis.INFRA
    assert result.matched_rule == "connection-reset"
    assert result.route == "infra-owner"
    assert result.retry_safe is True
    assert result.jev_status == "not_consulted"
    assert client.calls == []


def test_code_signature(tmp_path):
    d, _ = _diagnoser(tmp_path)
    result = diagnose_ci_failure(d, error_text="AssertionError: expected 200, got 500")
    assert result.diagnosis is Diagnosis.CODE
    assert result.matched_rule == "assertion-failure"
    assert result.route == "author"
    assert result.retry_safe is False


def test_deploy_source_without_infra_text(tmp_path):
    d, _ = _diagnoser(tmp_path)
    result = diagnose_deploy_failure(
        d, error_text="container exited 1", deploy_stage="hook"
    )
    assert result.diagnosis is Diagnosis.DEPLOY
    assert result.matched_rule is None
    assert result.route == "deploy-freeze"
    assert result.page is True


def test_deploy_source_with_infra_text_prefers_infra(tmp_path):
    d, _ = _diagnoser(tmp_path)
    result = diagnose_deploy_failure(
        d, error_text="no space left on device writing layer", deploy_stage="push"
    )
    assert result.diagnosis is Diagnosis.INFRA
    assert result.matched_rule == "disk-full"


def test_watchdog_metric_map(tmp_path):
    d, _ = _diagnoser(tmp_path)
    cases = {
        "jev_backend_error_rate": Diagnosis.INFRA,
        "rollback_rate": Diagnosis.CODE,
        "ci_failure_rate": Diagnosis.CODE,
        "auto_merge_volume": Diagnosis.INFRA,
        "escalation_rate": Diagnosis.UNKNOWN,
        "band_change_velocity": Diagnosis.UNKNOWN,
    }
    for metric, expected in cases.items():
        result = diagnose_watchdog_trip(d, metric_name=metric)
        assert result.diagnosis is expected, metric
        assert result.matched_rule == f"watchdog-metric:{metric}"


def test_watchdog_unknown_metric_goes_human(tmp_path):
    d, _ = _diagnoser(tmp_path)
    result = diagnose_watchdog_trip(d, metric_name="something-brand-new")
    assert result.diagnosis is Diagnosis.UNKNOWN
    assert result.route == "human"
    assert result.page is True


def test_flaky_statistics(tmp_path):
    d, _ = _diagnoser(tmp_path)
    history = (
        AttemptOutcome(commit_sha="abc123", outcome="fail"),
        AttemptOutcome(commit_sha="abc123", outcome="pass"),
    )
    result = diagnose_ci_failure(
        d,
        error_text="test_login timed out",
        commit_sha="abc123",
        test_history=history,
    )
    assert result.diagnosis is Diagnosis.FLAKE
    assert result.matched_rule == "flaky-statistics"
    assert result.jev_status == "not_consulted"


def test_flaky_statistics_control_different_shas(tmp_path, monkeypatch):
    _gate_off(monkeypatch)
    d, _ = _diagnoser(tmp_path)
    history = (
        AttemptOutcome(commit_sha="abc123", outcome="fail"),
        AttemptOutcome(commit_sha="def456", outcome="pass"),
    )
    result = diagnose_ci_failure(
        d,
        error_text="test_login timed out",
        commit_sha="abc123",
        test_history=history,
    )
    # "test_login timed out" matches no signature; different SHAs => not flaky
    assert result.diagnosis is Diagnosis.UNKNOWN
    assert result.jev_status == "gate_closed"


def test_routing_table_complete():
    for diagnosis in Diagnosis:
        assert diagnosis in ROUTES, diagnosis
        info = ROUTES[diagnosis]
        assert "route" in info and "retry_safe" in info and "page" in info


# -- Jev exception path -------------------------------------------------


def test_jev_advice_on_unknown_is_recorded_not_applied(tmp_path, monkeypatch):
    _gate_on(monkeypatch)
    client = StubClient(advice_choice="infra")
    d, audit = _diagnoser(tmp_path, client)
    result = diagnose_ci_failure(d, error_text="weird novel failure xyz")
    assert result.diagnosis is Diagnosis.UNKNOWN  # deterministic answer stands
    assert result.route == "human"
    assert result.jev_status == "advised"
    assert result.jev_advice is not None
    assert result.jev_advice["answers"]["diagnosis_advice"]["choice"] == "infra"
    assert len(client.calls) == 1
    row = _rows(audit)[0]
    assert row["diagnosis"] == "unknown"
    assert row["jev_status"] == "advised"
    assert row["action_taken"] is False
    assert row["shadow"] == "shadow — no action taken"


def test_deterministic_diagnosis_immutable_even_with_gate_open(tmp_path, monkeypatch):
    """Adversarial: gate open + backend eager to say 'code' on an infra
    signature — the deterministic INFRA must survive untouched."""
    _gate_on(monkeypatch)
    client = StubClient(advice_choice="code")
    d, _ = _diagnoser(tmp_path, client)
    result = diagnose_ci_failure(d, error_text="connection reset by peer")
    assert result.diagnosis is Diagnosis.INFRA
    assert client.calls == []  # short-circuit: never consulted


def test_choice_options_exactly_four(tmp_path, monkeypatch):
    _gate_on(monkeypatch)
    client = StubClient()
    d, _ = _diagnoser(tmp_path, client)
    diagnose_ci_failure(d, error_text="unclassifiable novel failure")
    questions = client.calls[0]["questions"]
    choice = next(q for q in questions if q.name == "diagnosis_advice")
    assert list(choice.options) == ["infra", "code", "flake", "deploy"]


def test_gate_default_off(tmp_path, monkeypatch):
    _gate_off(monkeypatch)
    client = ExplodingClient()
    d, audit = _diagnoser(tmp_path, client)
    result = diagnose_ci_failure(d, error_text="unclassifiable novel failure")
    assert result.diagnosis is Diagnosis.UNKNOWN
    assert result.jev_status == "gate_closed"
    assert result.jev_advice is None
    assert client.calls == []
    assert _rows(audit)[0]["jev_status"] == "gate_closed"


def test_jev_error_fail_closed(tmp_path, monkeypatch):
    _gate_on(monkeypatch)
    d, audit = _diagnoser(tmp_path, ErrorClient())
    result = diagnose_ci_failure(d, error_text="unclassifiable novel failure")
    assert result.diagnosis is Diagnosis.UNKNOWN
    assert result.jev_status == "errored"
    assert result.action_taken is False
    row = _rows(audit)[0]
    assert row["jev_status"] == "errored"
    assert row["shadow"] == "shadow — no action taken"


# -- config discipline --------------------------------------------------


def test_missing_config_invalid(tmp_path):
    with pytest.raises(DiagnosisConfigError):
        FailureDiagnoser(spec_path=tmp_path / "nope.yaml")


def test_malformed_config_fail_closed(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: diagnosis-signatures-v1\nsignatures: [oops\n")
    with pytest.raises(DiagnosisConfigError):
        FailureDiagnoser(spec_path=bad)


def test_wrong_version_rejected(tmp_path):
    bad = tmp_path / "v2.yaml"
    bad.write_text("version: diagnosis-signatures-v2\nsignatures: []\n")
    with pytest.raises(DiagnosisConfigError):
        FailureDiagnoser(spec_path=bad)


def test_bad_regex_rejected(tmp_path):
    bad = tmp_path / "regex.yaml"
    bad.write_text(
        "version: diagnosis-signatures-v1\n"
        "signatures:\n"
        "  - name: bad\n"
        "    diagnosis: infra\n"
        "    patterns: ['([unclosed']\n"
    )
    with pytest.raises(DiagnosisConfigError):
        FailureDiagnoser(spec_path=bad)


def test_unknown_diagnosis_name_rejected(tmp_path):
    bad = tmp_path / "diag.yaml"
    bad.write_text(
        "version: diagnosis-signatures-v1\n"
        "signatures:\n"
        "  - name: bad\n"
        "    diagnosis: haunted\n"
        "    patterns: ['x']\n"
    )
    with pytest.raises(DiagnosisConfigError):
        FailureDiagnoser(spec_path=bad)


def test_shipped_policy_loads_and_versioned():
    data = yaml.safe_load(SPEC.read_text())
    assert data["version"] == POLICY_VERSION
    assert len(data["signatures"]) >= 10
    names = [s["name"] for s in data["signatures"]]
    assert len(names) == len(set(names))


# -- audit discipline ---------------------------------------------------


def test_exactly_one_audit_signal_per_diagnosis(tmp_path):
    d, audit = _diagnoser(tmp_path)
    for _ in range(3):
        diagnose_ci_failure(d, error_text="connection reset by peer")
    rows = _rows(audit)
    assert len(rows) == 3
    for row in rows:
        assert row["component"] == "failure-diagnosis"
        assert row["policy_version"] == POLICY_VERSION
        assert row["action_taken"] is False
        assert "timestamp" in row


def test_audit_write_failure_does_not_change_diagnosis(tmp_path):
    audit_dir = tmp_path / "is-a-directory"
    audit_dir.mkdir()
    d = FailureDiagnoser(spec_path=SPEC, audit_path=str(audit_dir))
    result = diagnose_ci_failure(d, error_text="connection reset by peer")
    assert result.diagnosis is Diagnosis.INFRA  # verdict stands


def test_never_imports_action_paths():
    import prismatic.review_factory.failure_diagnosis  # noqa: F401

    forbidden = [
        "prismatic.review_factory.queue",
        "prismatic.review_factory.merge_executor",
        "prismatic.deploy",
        "prismatic.distributed_watchdog",
    ]
    for mod in forbidden:
        assert mod not in sys.modules, mod


def test_error_text_truncated_in_jev_state(tmp_path, monkeypatch):
    _gate_on(monkeypatch)
    client = StubClient()
    d, _ = _diagnoser(tmp_path, client)
    diagnose_ci_failure(d, error_text="x" * 100_000)
    state = client.calls[0]["state"]
    assert len(state["error_text"]) <= 4000
    assert "SWARMJEV" not in json.dumps(state)  # no credential-shaped keys
