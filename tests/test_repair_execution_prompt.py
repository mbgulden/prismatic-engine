"""Phase B: repair execution prompt tests.

Covers:
- ``_repair_agent_execution_prompt`` renders the repair brief: findings text,
  review_job_id, receipt_id, and the literal ``requeue_repaired_candidate``
  call snippet.
- >10 findings truncate with a count note (prompt stays bounded).
- ``dispatch_assigned_agent_event`` uses the repair prompt for
  review-factory events and keeps the generic prompt for everything else
  (regression: no behavior change outside repairs).
- Repair payloads missing optional fields still build without raising.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _import_dispatcher():
    """Import prismatic.dispatcher, retrying once on the swarmlock quirk.

    Same workaround as tests/test_repair_dispatch_drain.py: in a fresh
    interpreter the first import can raise ModuleNotFoundError for the
    optional 'swarmlock' primitive while prismatic.core installs its
    fallback; the immediate retry succeeds.
    """
    try:
        from prismatic import dispatcher

        return dispatcher
    except ModuleNotFoundError:
        from prismatic import dispatcher

        return dispatcher


_dispatcher = _import_dispatcher()
_repair_prompt = _dispatcher._repair_agent_execution_prompt
_generic_prompt = _dispatcher._assigned_agent_execution_prompt


def _repair_payload(**overrides):
    payload = {
        "kind": "review-factory-repair",
        "review_job_id": "job-0001",
        "target_agent": "fred",
        "title": "REPAIR: fix rejected candidate for test",
        "verdict": "REPAIR_REQUIRED",
        "reviewer_id": "deterministic",
        "receipt_id": "rcpt-abc123",
        "failure_reason": "tests failed",
        "findings": [
            {
                "severity": "high",
                "check": "unit-tests",
                "message": "2 tests failing in auth module",
            },
            "missing null guard in login handler",
        ],
        "description": (
            "Repair instructions:\n"
            "1. Fix the findings above.\n"
            "2. Push a new commit.\n"
            "3. Re-queue the job."
        ),
        "requeue": {
            "method": "ReviewQueue.requeue_repaired_candidate",
            "review_job_id": "job-0001",
        },
    }
    payload.update(overrides)
    return payload


def test_repair_prompt_renders_brief():
    """Repair payload -> prompt carries findings, ids, and the requeue snippet."""
    prompt = _repair_prompt(_repair_payload())
    assert "2 tests failing in auth module" in prompt
    assert "missing null guard in login handler" in prompt
    assert "job-0001" in prompt
    assert "rcpt-abc123" in prompt
    assert "REPAIR_REQUIRED" in prompt
    assert "tests failed" in prompt
    assert "requeue_repaired_candidate" in prompt
    assert (
        "ReviewQueue().requeue_repaired_candidate("
        "'job-0001', new_candidate_commit, new_candidate_tree)"
    ) in prompt
    assert "You are FRED executing one Prismatic Engine repair task" in prompt


def test_repair_prompt_truncates_findings():
    """>10 findings -> first 10 shown, count note, prompt stays bounded."""
    findings = [
        {"severity": "low", "check": f"check-{i}", "message": f"finding number {i}"}
        for i in range(15)
    ]
    prompt = _repair_prompt(_repair_payload(findings=findings))
    assert "finding number 0" in prompt
    assert "finding number 9" in prompt
    assert "finding number 10" not in prompt
    assert "5 more finding(s)" in prompt
    assert len(prompt) < 6000


def _fake_dispatch(monkeypatch, captured):
    """Route dispatch_assigned_agent_event away from every side effect."""
    monkeypatch.setattr(
        _dispatcher,
        "launch_visible_hermes_agent",
        lambda agent, issue_id, **kw: captured.update(kw) or object(),
    )
    monkeypatch.setattr(
        _dispatcher,
        "preflight_assigned_agent",
        lambda row, resolution, launchers=None: _dispatcher.AssignedAgentPreflight(
            "passed", True, "ok"
        ),
    )
    monkeypatch.setattr(
        _dispatcher, "emit_visible_agent_stream_event", lambda *a, **k: {}
    )
    monkeypatch.setattr(
        "prismatic.ingestion_queue.update_assigned_dispatch_state",
        lambda *a, **k: None,
    )


def test_dispatch_uses_repair_prompt_for_repair_events(monkeypatch):
    """Repair event -> the woken agent receives the repair brief."""
    captured = {}
    _fake_dispatch(monkeypatch, captured)
    row = {
        "identifier": "RF-REPAIR-prompt1",
        "event_type": "task.review-factory",
        "raw_json": json.dumps(_repair_payload()),
    }
    result = _dispatcher.dispatch_assigned_agent_event(row, dry_run=False)
    assert result["status"] == "dispatched", result
    prompt = captured.get("prompt")
    assert prompt is not None, "repair event must pass a repair prompt to the launcher"
    assert "requeue_repaired_candidate" in prompt
    assert "job-0001" in prompt
    assert "2 tests failing in auth module" in prompt


def test_dispatch_detects_repair_via_event_type(monkeypatch):
    """event_type == task.review-factory selects the repair prompt even when
    the payload kind key is absent (defensive second detection path)."""
    captured = {}
    _fake_dispatch(monkeypatch, captured)
    payload = _repair_payload()
    del payload["kind"]
    row = {
        "identifier": "RF-REPAIR-prompt2",
        "event_type": "task.review-factory",
        "raw_json": json.dumps(payload),
    }
    result = _dispatcher.dispatch_assigned_agent_event(row, dry_run=False)
    assert result["status"] == "dispatched", result
    assert captured.get("prompt") is not None
    assert "requeue_repaired_candidate" in captured["prompt"]


def test_dispatch_keeps_generic_prompt_for_other_events(monkeypatch):
    """Non-repair event -> prompt=None, so the launcher falls back to the
    generic Linear-task prompt (regression: no behavior change)."""
    captured = {}
    _fake_dispatch(monkeypatch, captured)
    row = {
        "identifier": "LINEAR-99",
        "event_type": "task.linear",
        "raw_json": json.dumps(
            {
                "kind": "linear-task",
                "target_agent": "fred",
                "title": "Some linear task",
            }
        ),
    }
    result = _dispatcher.dispatch_assigned_agent_event(row, dry_run=False)
    assert result["status"] == "dispatched", result
    assert captured.get("prompt") is None
    # The generic prompt itself is unchanged: still the Linear-task template.
    generic = _generic_prompt("fred", "LINEAR-99", title="Some linear task", run_id="r1")
    assert generic.startswith(
        "You are FRED working one Prismatic Engine Linear task: LINEAR-99."
    )
    assert "Required final compact packet:" in generic
    assert "requeue_repaired_candidate" not in generic


def test_repair_prompt_missing_optional_fields():
    """Sparse repair payloads still build without raising."""
    prompt = _repair_prompt({})
    assert isinstance(prompt, str) and prompt
    assert "n/a" in prompt
    prompt2 = _repair_prompt(
        {"kind": "review-factory-repair", "review_job_id": "job-x", "findings": None}
    )
    assert "job-x" in prompt2
    assert "no structured findings recorded" in prompt2
