"""Tests for the learn-loop wiring (record_outcome at the rollback site).

The hook is wiring-only: the learn-loop policy stays HARD-DISABLED, so the
calls produce refused/disabled audit rows — the invocation evidence. None of
these tests enable the policy or alter any deterministic verdict.
"""

import json
from pathlib import Path

import yaml

from prismatic.review_factory import merge_executor
from prismatic.review_factory.learn_loop import LearnLoop

HERE = Path(__file__).resolve()
SPEC_LEARN = HERE.parent.parent / "spec" / "learn_loop_policy_v1.yaml"
SPEC_BANDS = HERE.parent.parent / "spec" / "auto_merge_bands_v1.yaml"


def _disabled_loop(tmp_path):
    data = yaml.safe_load(SPEC_LEARN.read_text(encoding="utf-8"))
    data["enabled"] = False
    policy = tmp_path / "learn_loop_policy_test.yaml"
    policy.write_text(yaml.safe_dump(data), encoding="utf-8")
    bands = tmp_path / "auto_merge_bands_test.yaml"
    bands.write_text(
        yaml.safe_dump(yaml.safe_load(SPEC_BANDS.read_text(encoding="utf-8"))),
        encoding="utf-8",
    )
    return LearnLoop(
        policy,
        bands,
        decision_log=tmp_path / "decisions.jsonl",
        outcome_log=tmp_path / "outcomes.jsonl",
        band_change_log=tmp_path / "band-changes.jsonl",
        audit_log=tmp_path / "learn-audit.jsonl",
    )


def test_rollback_site_invokes_record_outcome(monkeypatch):
    calls = []

    class StubLoop:
        def record_outcome(self, job_id, outcome):
            calls.append((job_id, outcome))
            return {"status": "ok"}

    monkeypatch.setattr(
        "prismatic.review_factory.learn_loop.LearnLoop", StubLoop
    )
    merge_executor._record_learn_loop_rollback_outcome("job-abc")
    assert calls == [("job-abc", "rolled_back")]


def test_rollback_site_swallows_learn_loop_failures(monkeypatch):
    class FailingLoop:
        def record_outcome(self, job_id, outcome):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        "prismatic.review_factory.learn_loop.LearnLoop", FailingLoop
    )
    # Must not raise: the hot path never breaks on a learn-loop failure.
    assert merge_executor._record_learn_loop_rollback_outcome("job-abc") is None


def test_record_outcome_refused_while_disabled_writes_no_outcome_row(tmp_path):
    loop = _disabled_loop(tmp_path)
    result = loop.record_outcome("job-xyz", "rolled_back")
    assert result["status"] == "refused"
    assert not (tmp_path / "outcomes.jsonl").exists()
    # the refused call is still audited: the invocation evidence
    rows = [
        json.loads(line)
        for line in (tmp_path / "learn-audit.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 1
