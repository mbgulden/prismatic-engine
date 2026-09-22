"""Tests for typed failure diagnosis (Jev #28) — shadow mode.

Every test asserts a safety property the diagnoser claims:
- deterministic rules short-circuit Jev (never consulted);
- Jev advice is logged but never acted on and never overrides a
  deterministic verdict;
- gates default off and fail closed;
- config is versioned and disciplined;
- exactly one audit signal per diagnose(), marked shadow, action_taken=False.
"""

import json
from pathlib import Path

import pytest

from prismatic.jev import DecisionClient
from prismatic.jev.backends import BackendResult
from prismatic.jev.errors import DecisionError
from prismatic.jev.questions import ChoiceAnswer, NoulAnswer
from prismatic.review_factory import failure_diagnosis
from prismatic.review_factory.failure_diagnosis import (
    CHANNEL_FOR_VERDICT,
    ROOT_CAUSE_OPTIONS,
    DiagnosisInput,
    FailureDiagnosis,
    FailureDiagnosisConfigError,
)

SPEC = (
    Path(__file__).resolve().parent.parent / "spec" / "failure_diagnosis_rules_v1.yaml"
)

MASTER_ENV = "SWARMJEV_ENABLED"
SITE_ENV = "SWARMJEV_CALLSITE_FAILURE_DIAGNOSIS_ENABLED"


# ── fixtures & doubles ────────────────────────────────────────────────


@pytest.fixture
def clean_jev_env(monkeypatch):
    for var in (MASTER_ENV, SITE_ENV):
        monkeypatch.delenv(var, raising=False)


def gate_on(monkeypatch):
    monkeypatch.setenv(MASTER_ENV, "1")
    monkeypatch.setenv(SITE_ENV, "1")


class StubBackend:
    """Test double for the Jev backend: scripted answers, call counting."""

    backend_name = "stub"

    def __init__(self, *, answers=None, error=None):
        self._answers = answers or {}
        self._error = error
        self.calls = 0
        self.last_questions = None

    def decide(self, state, questions):
        self.calls += 1
        self.last_questions = list(questions)
        if self._error is not None:
            raise self._error
        return BackendResult(
            backend=self.backend_name,
            answers={q.name: self._answers[q.name] for q in questions},
            latency_ms=1.5,
        )


def code_advice():
    return {
        "root_cause": ChoiceAnswer(
            choice="code",
            probabilities={"infra": 0.03, "code": 0.90, "flake": 0.02, "unknown": 0.05},
            confidence=0.9,
        ),
        "confident": NoulAnswer(probability=0.9, confidence=0.9),
    }


def make_diag(tmp_path, monkeypatch, *, backend=None, rules_path=None):
    client = DecisionClient(backend=backend) if backend is not None else None
    return FailureDiagnosis(
        rules_path=str(rules_path or SPEC),
        audit_path=str(tmp_path / "audit.jsonl"),
        client=client,
    )


def audit_rows(tmp_path):
    path = tmp_path / "audit.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# ── deterministic rules ───────────────────────────────────────────────


def test_infra_signature_match_short_circuits_jev(tmp_path, monkeypatch, clean_jev_env):
    gate_on(monkeypatch)

    def raising_factory(*args, **kwargs):
        raise AssertionError("DecisionClient must never be constructed here")

    monkeypatch.setattr(failure_diagnosis, "DecisionClient", raising_factory)
    diag = make_diag(tmp_path, monkeypatch)  # no injected client -> lazy DecisionClient

    result = diag.diagnose(
        DiagnosisInput(
            failure_kind="ci-failure",
            error_text="The runner was lost mid-job; cancelling the workflow run.",
            source="test run 42",
        )
    )
    assert result.deterministic_verdict == "infra"
    assert result.deterministic_evidence == "infra_signature:runner-lost"
    assert result.jev_status == "not_consulted"
    assert result.jev_advice is None
    assert result.final_verdict == "infra"
    assert result.recommended_channel == "infra-response"
    assert result.action_taken is False


def test_flaky_test_classification(tmp_path, monkeypatch, clean_jev_env):
    gate_on(monkeypatch)

    def raising_factory(*args, **kwargs):
        raise AssertionError("DecisionClient must never be constructed here")

    monkeypatch.setattr(failure_diagnosis, "DecisionClient", raising_factory)
    diag = make_diag(tmp_path, monkeypatch)
    sha = "abc123"

    result = diag.diagnose(
        DiagnosisInput(
            failure_kind="ci-failure",
            error_text="test_login failed",
            commit_sha=sha,
            test_history=((sha, "fail"), (sha, "pass")),
        )
    )
    assert result.deterministic_verdict == "flake"
    assert result.deterministic_evidence == f"flake_history:sha={sha}"
    assert result.jev_status == "not_consulted"
    assert result.final_verdict == "flake"
    assert result.recommended_channel == "retry-flake"


def test_flaky_control_different_shas_goes_to_jev(tmp_path, monkeypatch, clean_jev_env):
    gate_on(monkeypatch)
    backend = StubBackend(answers=code_advice())
    diag = make_diag(tmp_path, monkeypatch, backend=backend)

    result = diag.diagnose(
        DiagnosisInput(
            failure_kind="ci-failure",
            error_text="test_login failed",
            commit_sha="def456",
            test_history=(("abc123", "fail"), ("def456", "fail")),
        )
    )
    assert result.deterministic_verdict is None
    assert backend.calls == 1
    assert result.jev_status == "advised"
    assert result.jev_advice is not None
    assert result.jev_advice["choice"] == "code"
    assert result.final_verdict == "code"


def test_error_class_match(tmp_path, monkeypatch, clean_jev_env):
    diag = make_diag(tmp_path, monkeypatch)
    result = diag.diagnose(
        DiagnosisInput(
            failure_kind="ci-failure",
            error_text="job exited with code 137",
            error_classes=("OOM_Killed",),
        )
    )
    assert result.deterministic_verdict == "infra"
    assert result.deterministic_evidence == "infra_signature:runner-oom"
    assert result.jev_status == "not_consulted"


# ── Jev shadow advice ─────────────────────────────────────────────────


def test_jev_shadow_advice_logged_not_acted_on(tmp_path, monkeypatch, clean_jev_env):
    gate_on(monkeypatch)
    backend = StubBackend(answers=code_advice())
    diag = make_diag(tmp_path, monkeypatch, backend=backend)

    result = diag.diagnose(
        DiagnosisInput(failure_kind="ci-failure", error_text="mysterious crash")
    )
    assert result.jev_status == "advised"
    assert result.jev_advice["choice"] == "code"
    assert result.jev_advice["probabilities"] == {
        "infra": 0.03,
        "code": 0.90,
        "flake": 0.02,
        "unknown": 0.05,
    }
    assert result.jev_advice["confidence"] == 0.9
    assert result.jev_backend == "stub"
    assert result.action_taken is False

    rows = audit_rows(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["component"] == "failure-diagnosis"
    assert row["jev_status"] == "advised"
    assert row["jev_advice"]["choice"] == "code"
    assert row["action_taken"] is False
    assert row["shadow"] == "shadow — no action taken"
    assert row["final_verdict"] == "code"  # shadow advice, recorded as ignored

    # Mechanical proof: the module has no response-action path to call.
    source = Path(failure_diagnosis.__file__).read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert "watchdog" not in stripped
            assert "dispatcher" not in stripped
            assert "receiver" not in stripped
    assert "def halt" not in source
    assert "def fire" not in source
    assert "def dispatch" not in source
    assert "def execute" not in source


def test_no_downgrade_deterministic_wins(tmp_path, monkeypatch, clean_jev_env):
    gate_on(monkeypatch)
    backend = StubBackend(answers=code_advice())
    diag = make_diag(tmp_path, monkeypatch, backend=backend)

    result = diag.diagnose(
        DiagnosisInput(
            failure_kind="deploy-failure",
            error_text="No space left on device while pulling the image",
        )
    )
    assert result.deterministic_verdict == "infra"
    assert backend.calls == 0  # short-circuit: Jev never consulted
    assert result.jev_status == "not_consulted"
    assert result.final_verdict == "infra"
    assert result.recommended_channel == "infra-response"


def test_deterministic_verdict_recorded_in_audit_row(
    tmp_path, monkeypatch, clean_jev_env
):
    # A deterministic verdict carries its evidence into the audit row;
    # nothing in the pipeline can downgrade or replace it.
    gate_on(monkeypatch)
    backend = StubBackend(answers=code_advice())
    diag = make_diag(tmp_path, monkeypatch, backend=backend)

    result = diag.diagnose(
        DiagnosisInput(
            failure_kind="ci-failure", error_text="could not resolve registry host"
        )
    )
    assert result.deterministic_verdict == "infra"
    assert result.final_verdict == "infra"
    rows = audit_rows(tmp_path)
    assert rows[0]["final_verdict"] == "infra"
    assert rows[0]["deterministic_verdict"] == "infra"
    assert rows[0]["deterministic_evidence"] == "infra_signature:dns-failure"


# ── gates & error paths ───────────────────────────────────────────────


def test_gate_default_off(tmp_path, clean_jev_env):
    from prismatic.jev import CallSiteGate

    assert CallSiteGate("failure-diagnosis").allow() is False
    diag = FailureDiagnosis(
        rules_path=str(SPEC), audit_path=str(tmp_path / "audit.jsonl")
    )
    result = diag.diagnose(
        DiagnosisInput(failure_kind="ci-failure", error_text="mysterious crash")
    )
    assert result.deterministic_verdict is None
    assert result.jev_status == "gate_closed"
    assert result.jev_advice is None
    assert result.final_verdict == "unknown"
    assert result.recommended_channel == "needs-human"


def test_jev_error_fail_closed(tmp_path, monkeypatch, clean_jev_env):
    gate_on(monkeypatch)
    backend = StubBackend(error=DecisionError("backend exploded"))
    diag = make_diag(tmp_path, monkeypatch, backend=backend)

    result = diag.diagnose(
        DiagnosisInput(failure_kind="ci-failure", error_text="mysterious crash")
    )
    assert result.jev_status == "errored"
    assert result.jev_advice is None
    assert result.final_verdict == "unknown"
    assert result.recommended_channel == "needs-human"
    assert result.action_taken is False
    rows = audit_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["jev_status"] == "errored"


# ── config discipline ─────────────────────────────────────────────────


def test_malformed_config_fail_closed(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("infra_signatures: [unclosed\n", encoding="utf-8")
    with pytest.raises(FailureDiagnosisConfigError):
        FailureDiagnosis(rules_path=str(bad), audit_path=str(tmp_path / "audit.jsonl"))

    not_a_mapping = tmp_path / "notmap.yaml"
    not_a_mapping.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(FailureDiagnosisConfigError):
        FailureDiagnosis(
            rules_path=str(not_a_mapping), audit_path=str(tmp_path / "audit.jsonl")
        )


def test_missing_config_empty_rules(tmp_path, monkeypatch, clean_jev_env):
    diag = FailureDiagnosis(
        rules_path=str(tmp_path / "does-not-exist.yaml"),
        audit_path=str(tmp_path / "audit.jsonl"),
    )
    result = diag.diagnose(
        DiagnosisInput(failure_kind="ci-failure", error_text="runner was lost")
    )
    # No rules loaded: no deterministic verdict, gate closed -> unknown.
    assert result.deterministic_verdict is None
    assert result.final_verdict == "unknown"
    assert result.action_taken is False


def test_bad_regex_rejected(tmp_path, monkeypatch):
    cfg = tmp_path / "rules.yaml"
    cfg.write_text(
        "version: test-v1\n"
        "infra_signatures:\n"
        "  - name: good-rule\n"
        '    error_text_regex: "runner lost"\n'
        "    error_classes: []\n"
        "  - name: bad-rule\n"
        '    error_text_regex: "[unclosed"\n'
        "    error_classes: []\n",
        encoding="utf-8",
    )
    diag = FailureDiagnosis(
        rules_path=str(cfg), audit_path=str(tmp_path / "audit.jsonl")
    )
    assert diag._invalid_rules == ["bad-rule"]
    result = diag.diagnose(
        DiagnosisInput(failure_kind="ci-failure", error_text="the runner lost contact")
    )
    assert result.deterministic_verdict == "infra"
    assert result.deterministic_evidence == "infra_signature:good-rule"
    rows = audit_rows(tmp_path)
    assert rows[0]["invalid_rules"] == ["bad-rule"]


# ── audit & question shape ────────────────────────────────────────────


def test_exactly_one_audit_signal_per_diagnosis(tmp_path, monkeypatch, clean_jev_env):
    diag = make_diag(tmp_path, monkeypatch)
    for i in range(3):
        diag.diagnose(
            DiagnosisInput(failure_kind="ci-failure", error_text=f"crash {i}")
        )
    rows = audit_rows(tmp_path)
    assert len(rows) == 3
    for row in rows:
        assert row["component"] == "failure-diagnosis"
        assert row["policy_version"] == "failure-diagnosis-rules-v1"
        assert row["shadow"] == "shadow — no action taken"
        assert row["action_taken"] is False


def test_choice_options_exactly_four(tmp_path, monkeypatch, clean_jev_env):
    assert ROOT_CAUSE_OPTIONS == ("infra", "code", "flake", "unknown")
    gate_on(monkeypatch)
    backend = StubBackend(answers=code_advice())
    diag = make_diag(tmp_path, monkeypatch, backend=backend)
    diag.diagnose(DiagnosisInput(failure_kind="ci-failure", error_text="mystery"))
    choice_q = backend.last_questions[0]
    assert list(choice_q.options) == ["infra", "code", "flake", "unknown"]


def test_recommended_channel_never_fires(tmp_path, monkeypatch, clean_jev_env):
    assert set(CHANNEL_FOR_VERDICT.values()) == {
        "infra-response",
        "code-repair",
        "retry-flake",
        "needs-human",
    }
    # Every path returns a channel and takes no action.
    gate_on(monkeypatch)
    backend = StubBackend(answers=code_advice())
    diag = make_diag(tmp_path, monkeypatch, backend=backend)
    cases = [
        DiagnosisInput(failure_kind="ci-failure", error_text="runner was lost"),
        DiagnosisInput(failure_kind="ci-failure", error_text="plain mystery"),
        DiagnosisInput(
            failure_kind="ci-failure",
            error_text="test_x failed",
            test_history=(("s1", "fail"), ("s1", "pass")),
        ),
    ]
    for failure in cases:
        result = diag.diagnose(failure)
        assert result.recommended_channel in CHANNEL_FOR_VERDICT.values()
        assert result.action_taken is False


def test_audit_write_failure_never_changes_verdict(tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    diag = FailureDiagnosis(
        rules_path=str(SPEC), audit_path=str(blocker / "audit.jsonl")
    )
    result = diag.diagnose(
        DiagnosisInput(failure_kind="ci-failure", error_text="runner was lost")
    )
    assert result.final_verdict == "infra"  # verdict intact, no exception


def test_deploy_failure_entry_point(tmp_path, monkeypatch):
    diag = make_diag(tmp_path, monkeypatch)
    result = diag.diagnose_deploy_failure(
        workflow_name="Post-Merge Production Deploy Hook (WB-1)",
        run_id="12345",
        conclusion="failure",
        error_text="temporary failure in name resolution",
    )
    assert result.failure_kind == "deploy-failure"
    assert result.deterministic_verdict == "infra"
    assert result.deterministic_evidence == "infra_signature:dns-failure"
    rows = audit_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["failure_kind"] == "deploy-failure"
