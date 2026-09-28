"""Tests for the shadow-mode failure triage (roadmap chunk 5).

Every test asserts a safety property the triager claims:
- deterministic rules decide first and short-circuit Jev (backend never called);
- flaky-test statistics classify fail-then-pass with no code change;
- Jev advice is logged but never acted on (the repair dispatcher is untouched);
- Jev may advise escalate but never downgrades a deterministic verdict;
- the Jev call site is gated default-off;
- a Jev error fails closed (shadow record marked errored, no action);
- every triage emits exactly one audit signal with the shadow marker.
"""

import json
import os
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from prismatic.jev.backends import BackendResult
from prismatic.jev.errors import DecisionError
from prismatic.jev.gates import CallSiteGate
from prismatic.review_factory.failure_triage import (
    GATE_SITE,
    JEV_ADVISED,
    JEV_ERRORED,
    JEV_GATE_CLOSED,
    JEV_NOT_CONSULTED,
    SHADOW_MARKER,
    STATE_INVALID,
    STATE_TRIAGED,
    TRIAGE_ESCALATE,
    TRIAGE_REJECT,
    TRIAGE_REPAIR,
    TRIAGE_RETRY,
    TRIAGE_VERDICTS,
    AttemptOutcome,
    FailureInput,
    FailureTriage,
    TriageConfigError,
    combine_verdict,
    load_transient_policy,
)

MASTER_ENV = "SWARMJEV_ENABLED"
SITE_ENV = "SWARMJEV_CALLSITE_FAILURE_TRIAGE_ENABLED"

MINIMAL_POLICY = {
    "version": "test-v1",
    "transients": [
        {
            "name": "test-transient",
            "description": "test rule",
            "patterns": ["connection timed out while fetching"],
            "error_classes": ["test-transient-class"],
        }
    ],
}


@contextmanager
def gate_env(enabled: bool):
    """Force the Jev call-site gate on or off regardless of the real env."""
    with patch.dict(os.environ):
        os.environ.pop(MASTER_ENV, None)
        os.environ.pop(SITE_ENV, None)
        if enabled:
            os.environ[MASTER_ENV] = "1"
            os.environ[SITE_ENV] = "1"
        yield


@pytest.fixture()
def policy_file(tmp_path):
    path = tmp_path / "triage_transients_test.yaml"
    path.write_text(yaml.safe_dump(MINIMAL_POLICY), encoding="utf-8")
    return path


@pytest.fixture()
def audit_log(tmp_path):
    return tmp_path / "audit" / "triage.jsonl"


@pytest.fixture()
def triager(policy_file, audit_log):
    return FailureTriage(policy_path=policy_file, audit_log=audit_log)


def read_rows(audit_log):
    return [
        json.loads(line)
        for line in Path(audit_log).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class StubBackend:
    """Fake Jev backend: scripted answers, no network."""

    backend_name = "stub"

    def __init__(self, answers: dict, fail_with: Exception | None = None):
        self.answers = answers
        self.fail_with = fail_with
        self.calls = 0

    def decide(self, state, questions, **kwargs):
        # **kwargs: DecisionClient passes call=<call context> since #496;
        # the stub ignores it the way a recording stub should.
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        parsed = {}
        for q in questions:
            parsed[q.name] = q.default_answer(self.answers[q.name])
        return BackendResult(backend=self.backend_name, answers=parsed, latency_ms=3.0)


def stub_client(**answers):
    from prismatic.jev.client import DecisionClient

    return DecisionClient(backend=StubBackend(answers))


# ── deterministic rules first ────────────────────────────────────────


class TestTransientShortCircuit:
    def test_transient_match_short_circuits_jev(self, triager, audit_log):
        with patch(
            "prismatic.review_factory.failure_triage.DecisionClient"
        ) as mock_client:
            mock_client.side_effect = AssertionError("Jev backend must not be called")
            result = triager.triage(
                FailureInput(
                    failure_id="ci:1:2",
                    source="ci",
                    error_text="pip failed: connection timed out while fetching index",
                )
            )
        assert result.state == STATE_TRIAGED
        assert result.deterministic_verdict == TRIAGE_RETRY
        assert "test-transient" in result.deterministic_evidence
        assert result.jev.status == JEV_NOT_CONSULTED
        assert result.final_verdict == TRIAGE_RETRY
        mock_client.assert_not_called()
        (row,) = read_rows(audit_log)
        assert row["deterministic_verdict"] == TRIAGE_RETRY
        assert row["jev"]["status"] == JEV_NOT_CONSULTED

    def test_error_class_match_short_circuits_jev(self, triager):
        with patch(
            "prismatic.review_factory.failure_triage.DecisionClient"
        ) as mock_client:
            mock_client.side_effect = AssertionError("Jev backend must not be called")
            result = triager.triage(
                FailureInput(
                    failure_id="ci:1:3",
                    source="ci",
                    error_text="something unrelated broke",
                    error_classes=("test-transient-class",),
                )
            )
        assert result.deterministic_verdict == TRIAGE_RETRY
        mock_client.assert_not_called()

    def test_no_match_does_not_retry(self, triager):
        with gate_env(False):
            result = triager.triage(
                FailureInput(
                    failure_id="ci:1:4",
                    source="ci",
                    error_text="AssertionError: expected 200, got 500",
                )
            )
        assert result.deterministic_verdict is None
        assert result.jev.status == JEV_GATE_CLOSED


class TestFlakyClassification:
    def test_fail_then_pass_same_sha_is_flake(self, triager, audit_log):
        with patch(
            "prismatic.review_factory.failure_triage.DecisionClient"
        ) as mock_client:
            mock_client.side_effect = AssertionError("Jev backend must not be called")
            result = triager.triage(
                FailureInput(
                    failure_id="ci:2:1",
                    source="ci",
                    test_name="test_checkout_flow",
                    commit_sha="abc123",
                    error_text="flaky selenium timeout",
                    test_history=(
                        AttemptOutcome(commit_sha="abc123", outcome="fail"),
                        AttemptOutcome(commit_sha="abc123", outcome="pass"),
                    ),
                )
            )
        assert result.deterministic_verdict == TRIAGE_RETRY
        assert "flake" in result.deterministic_evidence
        mock_client.assert_not_called()

    def test_different_shas_are_not_a_flake(self, triager):
        with gate_env(False):
            result = triager.triage(
                FailureInput(
                    failure_id="ci:2:2",
                    source="ci",
                    test_name="test_checkout_flow",
                    commit_sha="def456",
                    error_text="real assertion failure",
                    test_history=(
                        AttemptOutcome(commit_sha="abc123", outcome="fail"),
                        AttemptOutcome(commit_sha="def456", outcome="pass"),
                    ),
                )
            )
        assert result.deterministic_verdict is None
        assert result.jev.status == JEV_GATE_CLOSED

    def test_missing_history_is_not_a_flake(self, triager):
        with gate_env(False):
            result = triager.triage(
                FailureInput(
                    failure_id="ci:2:3",
                    source="ci",
                    error_text="boom",
                    test_history=None,
                )
            )
        assert result.deterministic_verdict is None


# ── shadow Jev advice: logged, never acted on ────────────────────────


class TestTransientFalsePositives:
    """Regression: deterministic failures must not match transient rules."""

    def test_module_not_found_is_not_transient(self, tmp_path):
        # 2026-09-28: v1's bare ENOTFOUND pattern matched the substring
        # "eNotFound" inside "ModuleNotFoundError", burning automatic
        # reruns on deterministic import failures. v2 word-bounds it.
        triager = FailureTriage(audit_log=str(tmp_path / "audit.jsonl"))
        assert triager.policy.version == "triage-transients-v2"
        result = triager.triage(
            FailureInput(
                failure_id="ci:1:2",
                source="ci",
                error_text="ModuleNotFoundError: No module named 'prismatic.dispatcher'",
            )
        )
        assert result.deterministic_verdict is None

    def test_errno_tokens_still_match(self, tmp_path):
        triager = FailureTriage(audit_log=str(tmp_path / "audit.jsonl"))
        for text in (
            "Error: ECONNRESET",
            "connect ETIMEDOUT 93.184.216.34:443",
            "npm ERR! code ENOTFOUND",
        ):
            result = triager.triage(
                FailureInput(failure_id="ci:1:2", source="ci", error_text=text)
            )
            assert result.deterministic_verdict == "retry", text


class TestShadowAdvice:
    def test_jev_advice_logged_not_acted_on(self, policy_file, audit_log):
        triager = FailureTriage(
            policy_path=policy_file,
            audit_log=audit_log,
            client_factory=lambda: stub_client(triage_verdict="repair", confident=0.9),
        )
        with gate_env(True):
            with patch(
                "prismatic.review_factory.queue.ReviewQueue.dispatch_repair_task"
            ) as dispatch:
                result = triager.triage(
                    FailureInput(
                        failure_id="ci:3:1",
                        source="ci",
                        error_text="unclassified weird failure",
                    )
                )
        assert dispatch.call_count == 0  # dispatcher untouched
        assert result.jev.status == JEV_ADVISED
        assert result.jev.choice == TRIAGE_REPAIR
        assert result.jev.probabilities[TRIAGE_REPAIR] == pytest.approx(1.0)
        assert result.jev.confidence == pytest.approx(0.9)
        assert result.jev.backend == "stub"
        assert result.final_verdict == TRIAGE_REPAIR  # advice stands alone
        assert result.action_taken is False
        assert result.shadow is True
        (row,) = read_rows(audit_log)
        assert row["component"] == "failure-triage"
        assert row["jev"]["advice"] == TRIAGE_REPAIR
        assert row["jev"]["probabilities"][TRIAGE_REPAIR] == pytest.approx(1.0)
        assert row["action_taken"] is False
        assert row["shadow"] is True
        assert row["marker"] == SHADOW_MARKER

    def test_triage_module_never_references_dispatcher(self):
        # tests/test_failure_triage.py -> review_factory/failure_triage.py
        # The module docstring may NAME the dispatcher in prose; what must
        # not exist is a code-level reference: no import of it, no call.
        import ast

        source = Path(__file__).resolve().parent.parent / "failure_triage.py"
        text = source.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "dispatcher" not in (node.module or ""), node.module
                assert "ingestion" not in (node.module or ""), node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert "dispatcher" not in alias.name, alias.name
        assert "dispatch_repair_task(" not in text  # no call expression
        assert "prismatic.dispatcher" not in text

    def test_ask_confidence_false_asks_only_choice(self, policy_file, audit_log):
        seen = {}

        class CountingBackend(StubBackend):
            def decide(self, state, questions, **kwargs):
                seen["n_questions"] = len(questions)
                seen["names"] = [q.name for q in questions]
                return super().decide(state, questions)

        from prismatic.jev.client import DecisionClient

        triager = FailureTriage(
            policy_path=policy_file,
            audit_log=audit_log,
            client_factory=lambda: DecisionClient(
                backend=CountingBackend({"triage_verdict": "reject"})
            ),
        )
        with gate_env(True):
            result = triager.triage(
                FailureInput(
                    failure_id="ci:3:2", source="ci", error_text="unclassified"
                ),
                ask_confidence=False,
            )
        assert seen["n_questions"] == 1
        assert seen["names"] == ["triage_verdict"]
        assert result.jev.choice == TRIAGE_REJECT

    def test_choice_options_are_exactly_the_four(self):
        from prismatic.review_factory.failure_triage import _triage_questions

        choice, _noul = _triage_questions()
        assert tuple(choice.options) == TRIAGE_VERDICTS
        assert set(choice.options) == {"repair", "retry", "reject", "escalate"}


# ── no-downgrade: Jev may advise escalate, never downgrade ────────────


class TestNoDowngrade:
    def test_reject_plus_jev_retry_stays_reject(self):
        assert combine_verdict("reject", "retry") == TRIAGE_REJECT

    def test_reject_plus_jev_escalate_stays_reject(self):
        # Jev cannot move a deterministic REJECT anywhere, even upward.
        assert combine_verdict("reject", "escalate") == TRIAGE_REJECT

    def test_repair_plus_jev_retry_stays_repair(self):
        assert combine_verdict("repair", "retry") == TRIAGE_REPAIR

    def test_repair_plus_jev_escalate_stays_repair(self):
        assert combine_verdict("repair", "escalate") == TRIAGE_REPAIR

    def test_retry_plus_jev_reject_stays_retry(self):
        # The deterministic "retry" (transient/flake) is final.
        assert combine_verdict("retry", "reject") == TRIAGE_RETRY

    def test_retry_plus_jev_retry_agrees(self):
        assert combine_verdict("retry", "retry") == TRIAGE_RETRY

    def test_no_deterministic_jev_advice_stands_alone(self):
        assert combine_verdict(None, "escalate") == TRIAGE_ESCALATE
        assert combine_verdict(None, "repair") == TRIAGE_REPAIR

    def test_no_deterministic_no_advice_is_none(self):
        assert combine_verdict(None, None) is None

    def test_unrecognized_jev_advice_is_not_a_verdict(self):
        assert combine_verdict(None, "shrug") is None
        assert combine_verdict("reject", "shrug") == TRIAGE_REJECT

    def test_unknown_deterministic_verdict_fails_closed(self):
        with pytest.raises(DecisionError):
            combine_verdict("maybe", "retry")


# ── gating: default-off ─────────────────────────────────────────────


class TestGateDefaultOff:
    def test_gate_is_closed_by_default(self):
        with gate_env(False):
            assert CallSiteGate(GATE_SITE).allow() is False

    def test_gate_opens_when_both_switches_set(self):
        with gate_env(True):
            assert CallSiteGate(GATE_SITE).allow() is True

    def test_gate_closed_means_no_jev_call(self, policy_file, audit_log):
        triager = FailureTriage(
            policy_path=policy_file,
            audit_log=audit_log,
            client_factory=lambda: stub_client(triage_verdict="repair", confident=0.9),
        )
        with gate_env(False):
            with patch(
                "prismatic.review_factory.failure_triage.DecisionClient"
            ) as mock_client:
                mock_client.side_effect = AssertionError("gate closed: no Jev call")
                result = triager.triage(
                    FailureInput(
                        failure_id="ci:4:1",
                        source="ci",
                        error_text="unclassified failure",
                    )
                )
        assert result.jev.status == JEV_GATE_CLOSED
        assert result.final_verdict is None
        assert result.gate_allowed is False
        mock_client.assert_not_called()
        (row,) = read_rows(audit_log)
        assert row["gate"] == {"site": GATE_SITE, "allowed": False}
        assert row["marker"] == SHADOW_MARKER


# ── Jev error → fail-closed ──────────────────────────────────────────


class TestJevErrorFailClosed:
    def test_jev_error_marks_record_errored_no_action(self, policy_file, audit_log):
        from prismatic.jev.client import DecisionClient

        def failing_client():
            return DecisionClient(
                backend=StubBackend({}, fail_with=DecisionError("backend exploded"))
            )

        triager = FailureTriage(
            policy_path=policy_file, audit_log=audit_log, client_factory=failing_client
        )
        with gate_env(True):
            result = triager.triage(
                FailureInput(
                    failure_id="ci:5:1", source="ci", error_text="unclassified"
                )
            )
        assert result.state == STATE_TRIAGED
        assert result.jev.status == JEV_ERRORED
        assert result.jev.error == "DecisionError"  # type name only, no detail leak
        assert result.jev.choice is None
        assert result.final_verdict is None
        assert result.action_taken is False
        (row,) = read_rows(audit_log)
        assert row["jev"]["status"] == JEV_ERRORED
        assert row["marker"] == SHADOW_MARKER


# ── config discipline ───────────────────────────────────────────────


class TestConfigDiscipline:
    def test_missing_file_means_empty_list(self, tmp_path, audit_log):
        policy = load_transient_policy(tmp_path / "does-not-exist.yaml")
        assert policy.rules == ()
        assert policy.version == "missing"

    def test_malformed_yaml_raises(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("{ not: [valid", encoding="utf-8")
        with pytest.raises(TriageConfigError):
            load_transient_policy(bad)

    def test_bad_regex_rejected(self, tmp_path):
        bad = tmp_path / "bad-regex.yaml"
        bad.write_text(
            yaml.safe_dump(
                {
                    "version": "x",
                    "transients": [
                        {"name": "bad", "patterns": ["([unclosed"]},
                    ],
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(TriageConfigError):
            load_transient_policy(bad)

    def test_malformed_policy_makes_triage_invalid(self, tmp_path, audit_log):
        bad = tmp_path / "bad.yaml"
        bad.write_text("{ not: [valid", encoding="utf-8")
        triager = FailureTriage(policy_path=bad, audit_log=audit_log)
        result = triager.triage(
            FailureInput(failure_id="ci:6:1", source="ci", error_text="boom")
        )
        assert result.state == STATE_INVALID
        assert result.policy_version == "invalid"
        (row,) = read_rows(audit_log)
        assert row["state"] == STATE_INVALID
        assert "policy_error" in row["note"]

    def test_shipped_v1_config_loads(self):
        spec = (
            Path(__file__).resolve().parent.parent
            / "spec"
            / "triage_transients_v1.yaml"
        )
        policy = load_transient_policy(spec)
        assert policy.version == "triage-transients-v1"
        assert len(policy.rules) == 5
        names = [r.name for r in policy.rules]
        assert names == [
            "runner-lost",
            "network-timeout",
            "github-api-transient",
            "registry-transient",
            "connection-reset-midstream",
        ]


# ── audit discipline ────────────────────────────────────────────────


class TestAuditDiscipline:
    def test_exactly_one_signal_per_triage(self, triager, audit_log):
        with gate_env(False):
            for i in range(3):
                triager.triage(
                    FailureInput(failure_id=f"ci:7:{i}", source="ci", error_text="boom")
                )
        rows = read_rows(audit_log)
        assert len(rows) == 3
        assert all(r["component"] == "failure-triage" for r in rows)
        assert all(r["marker"] == SHADOW_MARKER for r in rows)

    def test_audit_write_failure_does_not_change_verdict(self, policy_file, tmp_path):
        triager = FailureTriage(
            policy_path=policy_file,
            audit_log=tmp_path,  # a directory: open() fails
        )
        with gate_env(False):
            result = triager.triage(
                FailureInput(failure_id="ci:7:9", source="ci", error_text="boom")
            )
        assert result.state == STATE_TRIAGED

    def test_invalid_input_gets_no_verdict(self, triager, audit_log):
        result = triager.triage(
            FailureInput(failure_id="", source="ci", error_text="boom")
        )
        assert result.state == STATE_INVALID
        (row,) = read_rows(audit_log)
        assert row["state"] == STATE_INVALID


# ── event entry points ──────────────────────────────────────────────


class TestEventEntryPoints:
    def test_triage_ci_failure_per_failed_job(self, policy_file, audit_log):
        from prismatic.review_factory.failure_triage import triage_ci_failure

        triager = FailureTriage(policy_path=policy_file, audit_log=audit_log)
        with gate_env(False):
            results = triage_ci_failure(
                workflow_name="test",
                run_id="123",
                conclusion="failure",
                failed_jobs=[
                    {
                        "job_id": "9",
                        "name": "smoke",
                        "head_sha": "abc",
                        "failed_steps": ["Run lint"],
                        "log_excerpt": "connection timed out while fetching",
                    },
                    {
                        "job_id": "10",
                        "name": "gate",
                        "head_sha": "abc",
                        "failed_steps": ["verify"],
                        "log_excerpt": "AssertionError: red",
                    },
                ],
                triager=triager,
            )
        assert len(results) == 2
        assert results[0].deterministic_verdict == TRIAGE_RETRY  # transient
        assert results[0].failure_id == "ci:123:9"
        assert results[1].deterministic_verdict is None  # unclassified
        assert results[1].jev.status == JEV_GATE_CLOSED
        assert len(read_rows(audit_log)) == 2

    def test_triage_deploy_failure_hook(self, policy_file, audit_log):
        from prismatic.review_factory.failure_triage import triage_deploy_failure

        triager = FailureTriage(policy_path=policy_file, audit_log=audit_log)
        with gate_env(False):
            result = triage_deploy_failure(
                deploy_id="d-1",
                error_text="deploy step failed: connection reset by peer",
                triager=triager,
            )
        assert result.failure_id == "deploy:d-1"
        # "connection reset by peer" is not in the minimal test policy,
        # so this failure is unclassified -> gate closed -> shadow record.
        assert result.state == STATE_TRIAGED
        assert result.action_taken is False
