"""Tests for Phase 3: the optional LLM deep-review stage.

Covers: adapter schema validation, timeout/failure fallback,
repair-packet compilation, re-review bounds, escalation routing,
and the never-downgrade rule (the LLM can never turn a deterministic
REPAIR_REQUIRED/REJECTED into CLEAN).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.llm_deep_review import (
    LLMDeepReviewAdapter,
    LLMFinding,
    LLMReviewConfig,
    LLMReviewResult,
    compile_repair_packet,
    escalate_to_human,
    render_work_order_text,
    rereview_budget_remaining,
    resolve_llm_outcome,
    summarize_for_human,
    validate_rereview_output,
    validate_review_output,
)


@pytest.fixture
def tmp_db(tmp_path):
    db_path = tmp_path / "test_phase3_llm.db"
    db = ReviewFactoryDB(db_path=db_path)
    db.ensure_tables()
    return db


def _config(**overrides):
    base = dict(
        enabled=True,
        endpoint="http://localhost:11434",
        model_full="ned-test",
        model_bounded="george-test",
        timeout_seconds=5.0,
        max_diff_chars=1000,
        max_rereviews=2,
        temperature=0.0,
    )
    base.update(overrides)
    return LLMReviewConfig(**base)


class FakeOllamaClient:
    """Scripted stand-in for OllamaClient.chat / health / model checks."""

    def __init__(
        self,
        chat_response=None,
        chat_exc=None,
        healthy=True,
        models=(),
    ):
        self.chat_response = chat_response
        self.chat_exc = chat_exc
        self.healthy = healthy
        self.models = set(models)
        self.chat_calls = []

    def check_health(self):
        return self.healthy

    def model_available(self, name):
        return name in self.models

    def chat(self, model, messages, **kwargs):
        self.chat_calls.append((model, messages, kwargs))
        if self.chat_exc is not None:
            raise self.chat_exc
        return self.chat_response


def _chat_envelope(payload_obj) -> dict:
    return {"message": {"role": "assistant", "content": json.dumps(payload_obj)}}


def _job():
    return SimpleNamespace(
        review_job_id="job-123",
        task_id="",
        repository="mbgulden/prismatic-engine",
        candidate_commit="abc123def456",
        candidate_tree="tree999",
    )


def _valid_payload():
    return {
        "findings": [
            {
                "severity": "high",
                "file": "prismatic/x.py",
                "lines": "10-20",
                "category": "security",
                "explanation": "Unsanitized input reaches the shell.",
            },
            {
                "severity": "low",
                "file": "prismatic/y.py",
                "lines": 42,
                "category": "maintainability",
                "explanation": "Nit: rename for clarity.",
            },
        ],
        "verdict": "repair_required",
        "confidence": 0.8,
        "rationale": "One high-severity security finding needs a repair round.",
    }


# ── Schema validation ──────────────────────────────────────────────────


class TestSchemaValidation:
    def test_valid_output_passes(self):
        result = validate_review_output(_valid_payload(), model="ned-test")
        assert result.verdict == "repair_required"
        assert result.confidence == 0.8
        assert result.model == "ned-test"
        assert len(result.findings) == 2
        # severity normalization: high -> error, low -> info
        assert result.findings[0].severity == "error"
        assert result.findings[0].lines == "10-20"
        assert result.findings[1].severity == "info"
        assert result.findings[1].lines == "42"

    def test_findings_not_list_rejected(self):
        bad = _valid_payload()
        bad["findings"] = {"severity": "high"}
        with pytest.raises(ValueError):
            validate_review_output(bad)

    def test_bad_verdict_rejected(self):
        bad = _valid_payload()
        bad["verdict"] = "maybe"
        with pytest.raises(ValueError):
            validate_review_output(bad)

    def test_bad_severity_rejected(self):
        bad = _valid_payload()
        bad["findings"][0]["severity"] = "extreme"
        with pytest.raises(ValueError):
            validate_review_output(bad)

    def test_confidence_out_of_range_rejected(self):
        bad = _valid_payload()
        bad["confidence"] = 1.5
        with pytest.raises(ValueError):
            validate_review_output(bad)

    def test_missing_file_rejected(self):
        bad = _valid_payload()
        del bad["findings"][0]["file"]
        with pytest.raises(ValueError):
            validate_review_output(bad)

    def test_missing_explanation_rejected(self):
        bad = _valid_payload()
        del bad["findings"][0]["explanation"]
        with pytest.raises(ValueError):
            validate_review_output(bad)

    def test_non_object_rejected(self):
        with pytest.raises(ValueError):
            validate_review_output([1, 2, 3])

    def test_unknown_category_defaults(self):
        payload = _valid_payload()
        payload["findings"][0]["category"] = "vibes"
        result = validate_review_output(payload)
        assert result.findings[0].category == "correctness"

    def test_finding_to_factory_finding(self):
        result = validate_review_output(_valid_payload())
        f = result.findings[0].to_finding()
        assert f.path == "prismatic/x.py"
        assert f.line == 10
        assert f.severity == "error"
        assert "security" in f.invariant


class TestReReviewSchema:
    def test_valid_rereview_passes(self):
        payload = {
            "reassessments": [
                {
                    "finding_index": 0,
                    "status": "fixed",
                    "why": "Input is now sanitized.",
                },
                {
                    "finding_index": 1,
                    "status": "still_broken",
                    "why": "Rename not done.",
                },
            ],
            "verdict": "repair_required",
            "confidence": 0.7,
            "rationale": "One finding fixed, one still broken.",
        }
        result = validate_rereview_output(payload, model="ned-test")
        assert len(result.reassessments) == 2
        assert result.reassessments[0].status == "fixed"
        assert result.reassessments[1].status == "still_broken"

    def test_bad_status_rejected(self):
        payload = {
            "reassessments": [{"finding_index": 0, "status": "kinda", "why": "x"}],
            "verdict": "clean",
            "confidence": 0.5,
            "rationale": "x",
        }
        with pytest.raises(ValueError):
            validate_rereview_output(payload)


# ── Timeout / failure fallback ─────────────────────────────────────────


class TestFailureFallback:
    def _adapter(self, **client_kwargs):
        client_kwargs.setdefault("models", {"ned-test", "george-test"})
        client = FakeOllamaClient(**client_kwargs)
        return LLMDeepReviewAdapter(_config(), client=client), client

    def test_timeout_returns_none(self):
        adapter, _ = self._adapter(chat_exc=TimeoutError("timed out"))
        result = adapter.review(
            _job(),
            diff_text="diff --git a/x b/x",
            deterministic_verdict="clean",
            deterministic_findings=[],
        )
        assert result is None

    def test_none_response_returns_none(self):
        adapter, _ = self._adapter(chat_response=None)
        assert (
            adapter.review(
                _job(),
                diff_text="diff",
                deterministic_verdict="clean",
                deterministic_findings=[],
            )
            is None
        )

    def test_non_json_content_returns_none(self):
        client_resp = {"message": {"role": "assistant", "content": "not json at all"}}
        adapter, _ = self._adapter(chat_response=client_resp)
        assert (
            adapter.review(
                _job(),
                diff_text="diff",
                deterministic_verdict="clean",
                deterministic_findings=[],
            )
            is None
        )

    def test_fenced_json_is_accepted(self):
        payload = _valid_payload()
        fenced = {
            "message": {
                "role": "assistant",
                "content": "```json\n" + json.dumps(payload) + "\n```",
            }
        }
        adapter, _ = self._adapter(chat_response=fenced)
        result = adapter.review(
            _job(),
            diff_text="diff",
            deterministic_verdict="clean",
            deterministic_findings=[],
        )
        assert result is not None
        assert result.verdict == "repair_required"

    def test_disabled_config_returns_none(self):
        client = FakeOllamaClient(models={"ned-test"})
        adapter = LLMDeepReviewAdapter(_config(enabled=False), client=client)
        assert (
            adapter.review(
                _job(),
                diff_text="diff",
                deterministic_verdict="clean",
                deterministic_findings=[],
            )
            is None
        )
        assert client.chat_calls == []

    def test_unhealthy_endpoint_returns_none(self):
        adapter, client = self._adapter(healthy=False)
        ok, reason = adapter.gates_pass()
        assert ok is False
        assert "unreachable" in reason

    def test_no_model_available_returns_none(self):
        adapter, _ = self._adapter(models=set())
        ok, reason = adapter.gates_pass()
        assert ok is False
        assert "not available" in reason

    def test_chat_exception_never_raises(self):
        adapter, _ = self._adapter(chat_exc=RuntimeError("boom"))
        # must not raise — the failure gate is "skip, deterministic stands"
        assert (
            adapter.review(
                _job(),
                diff_text="diff",
                deterministic_verdict="repair_required",
                deterministic_findings=[],
            )
            is None
        )

    def test_model_selection_prefers_bounded_for_large_diff(self):
        adapter, client = self._adapter()
        small = adapter._select_model(100)
        assert small == "ned-test"
        large = adapter._select_model(100_000)
        assert large == "george-test"

    def test_model_selection_falls_back(self):
        adapter, _ = self._adapter(models={"george-test"})
        assert adapter._select_model(100) == "george-test"


# ── Repair-packet compilation ──────────────────────────────────────────


class TestRepairPacketCompilation:
    def _findings(self):
        result = validate_review_output(_valid_payload(), model="ned-test")
        return result.findings

    def test_packet_has_concrete_work_items(self):
        packet = compile_repair_packet(
            job_id="job-123",
            candidate_tree="tree999",
            candidate_commit="abc123",
            model="ned-test",
            deterministic_verdict="repair_required",
            findings=self._findings(),
        )
        assert packet["kind"] == "llm-repair-work-order"
        assert len(packet["work_items"]) == 2
        item = packet["work_items"][0]
        # what to change: concrete file + lines + explanation
        assert "prismatic/x.py" in item["what_to_change"]
        assert "10-20" in item["what_to_change"]
        assert "Unsanitized input" in item["what_to_change"]
        # patch sketch: anchored diff-style sketch, not a fabricated patch
        assert "a/prismatic/x.py" in item["patch_sketch"]
        assert "10-20" in item["patch_sketch"]
        # tests to add: non-empty, category-driven
        assert item["tests_to_add"]
        assert any("hostile input" in t for t in item["tests_to_add"])

    def test_rendered_work_order_is_plain_text(self):
        packet = compile_repair_packet(
            job_id="job-123",
            candidate_tree="tree999",
            candidate_commit="abc123",
            model="ned-test",
            deterministic_verdict="repair_required",
            findings=self._findings(),
        )
        text = render_work_order_text(packet)
        assert "What to change" in text
        assert "Patch sketch" in text
        assert "Tests to add" in text
        assert "prismatic/x.py:10-20" in text

    def test_packet_carries_rereview_status(self):
        from prismatic.review_factory.llm_deep_review import LLMReassessment

        packet = compile_repair_packet(
            job_id="job-123",
            candidate_tree="tree999",
            candidate_commit="abc123",
            model="ned-test",
            deterministic_verdict="repair_required",
            findings=self._findings(),
            reassessments=[
                LLMReassessment(
                    finding_index=0, status="still_broken", why="Not fixed yet."
                )
            ],
        )
        assert "still_broken" in packet["work_items"][0]["what_to_change"]


# ── Re-review bounds ───────────────────────────────────────────────────


class TestReReviewBounds:
    def test_budget_counts_completed_rereviews(self, tmp_db):
        assert rereview_budget_remaining(tmp_db, "job-1", 2) == 2
        tmp_db.insert_audit_entry(
            actor="t",
            action="llm_rereview_completed",
            review_job_id="job-1",
            details={},
        )
        assert rereview_budget_remaining(tmp_db, "job-1", 2) == 1
        tmp_db.insert_audit_entry(
            actor="t",
            action="llm_rereview_completed",
            review_job_id="job-1",
            details={},
        )
        assert rereview_budget_remaining(tmp_db, "job-1", 2) == 0
        # never goes negative
        tmp_db.insert_audit_entry(
            actor="t",
            action="llm_rereview_completed",
            review_job_id="job-1",
            details={},
        )
        assert rereview_budget_remaining(tmp_db, "job-1", 2) == 0

    def test_budget_is_per_job(self, tmp_db):
        tmp_db.insert_audit_entry(
            actor="t",
            action="llm_rereview_completed",
            review_job_id="job-1",
            details={},
        )
        assert rereview_budget_remaining(tmp_db, "job-2", 2) == 2

    def test_count_audit_entries_helper(self, tmp_db):
        assert tmp_db.count_audit_entries("job-9", "llm_review_completed") == 0
        tmp_db.insert_audit_entry(
            actor="t", action="llm_review_completed", review_job_id="job-9", details={}
        )
        tmp_db.insert_audit_entry(
            actor="t", action="llm_review_completed", review_job_id="job-9", details={}
        )
        assert tmp_db.count_audit_entries("job-9", "llm_review_completed") == 2


# ── Escalation routing ─────────────────────────────────────────────────


class TestEscalationRouting:
    def test_escalate_audits_and_skips_linear_without_issue(self, tmp_db):
        job = _job()
        findings = validate_review_output(_valid_payload()).findings
        summary = summarize_for_human(
            job_id=job.review_job_id,
            task_id="",
            candidate_commit=job.candidate_commit,
            model="ned-test",
            deterministic_verdict="clean",
            findings=findings,
        )
        result = escalate_to_human(tmp_db, job, summary=summary, evidence={"a": 1})
        assert result["linear_comment"] == "skipped_no_issue"
        entry = tmp_db.find_audit_entry(job.review_job_id, "llm_escalated")
        assert entry is not None
        details = json.loads(entry["details_json"])
        assert details["linear_comment"] == "skipped_no_issue"
        assert "will NOT auto-merge" in details["summary"]

    def test_summary_is_plain_language_with_evidence(self):
        findings = validate_review_output(_valid_payload()).findings
        summary = summarize_for_human(
            job_id="job-123",
            task_id="GRO-9999",
            candidate_commit="abc123def456",
            model="ned-test",
            deterministic_verdict="clean",
            findings=findings,
        )
        assert "prismatic/x.py:10-20" in summary
        assert "security" in summary
        assert "HELD for human review" in summary
        assert "NOT auto-merge" in summary


# ── The never-downgrade rule ───────────────────────────────────────────


class TestNeverDowngrade:
    def _llm_clean(self):
        return LLMReviewResult(
            findings=[],
            verdict="clean",
            confidence=0.95,
            rationale="Looks fine.",
            model="ned",
        )

    def _llm_findings(self):
        return LLMReviewResult(
            findings=validate_review_output(_valid_payload()).findings,
            verdict="repair_required",
            confidence=0.8,
            rationale="r",
            model="ned",
        )

    def test_repair_required_never_downgraded_even_when_llm_says_clean(self):
        # The core authority rule: deterministic REPAIR_REQUIRED stands.
        assert resolve_llm_outcome("repair_required", self._llm_clean()) == "packet"

    def test_rejected_never_downgraded_even_when_llm_says_clean(self):
        assert resolve_llm_outcome("rejected", self._llm_clean()) == "packet"

    def test_repair_required_with_findings_compiles_packet(self):
        assert resolve_llm_outcome("repair_required", self._llm_findings()) == "packet"

    def test_clean_with_critical_llm_findings_escalates(self):
        assert resolve_llm_outcome("clean", self._llm_findings()) == "escalate"

    def test_clean_with_only_low_findings_is_advisory(self):
        llm = LLMReviewResult(
            findings=[
                LLMFinding(
                    severity="info",
                    file="a.py",
                    lines="1",
                    category="maintainability",
                    explanation="nit",
                )
            ],
            verdict="clean",
            confidence=0.9,
            rationale="only nits",
            model="ned",
        )
        assert resolve_llm_outcome("clean", llm) == "advisory"

    def test_no_llm_result_means_no_action(self):
        assert resolve_llm_outcome("clean", None) == "none"
        assert resolve_llm_outcome("repair_required", None) == "none"


# ── Config ─────────────────────────────────────────────────────────────


class TestConfig:
    def test_from_env_defaults_off(self, monkeypatch, tmp_path):
        monkeypatch.delenv("PRISMATIC_REVIEW_LLM", raising=False)
        monkeypatch.delenv("PRISMATIC_REVIEW_LLM_MODEL", raising=False)
        monkeypatch.delenv("PRISMATIC_REVIEW_LLM_MODEL_BOUNDED", raising=False)
        # keep the real toggle file out of the picture
        import prismatic.review_factory.llm_deep_review as mod

        monkeypatch.setattr(mod, "_TOGGLE_FILE", tmp_path / "toggle.json")
        cfg = LLMReviewConfig.from_env()
        assert cfg.enabled is False
        assert cfg.timeout_seconds == 180.0
        # no hardcoded model names in defaults
        assert LLMReviewConfig().model_full == ""
        assert LLMReviewConfig().model_bounded == ""

    def test_from_env_reads_model_names(self, monkeypatch):
        monkeypatch.setenv("PRISMATIC_REVIEW_LLM", "1")
        monkeypatch.setenv("PRISMATIC_REVIEW_LLM_MODEL", "ned-72b-q8")
        monkeypatch.setenv("PRISMATIC_REVIEW_LLM_MODEL_BOUNDED", "george-32b")
        monkeypatch.setenv("PRISMATIC_REVIEW_LLM_ENDPOINT", "http://hermes:11434")
        cfg = LLMReviewConfig.from_env()
        assert cfg.enabled is True
        assert cfg.model_full == "ned-72b-q8"
        assert cfg.model_bounded == "george-32b"
        assert cfg.endpoint == "http://hermes:11434"
