"""F3/F4: repair-dispatch regression tests (Phase A fix).

Covers:
- A synthetic ``task.review-factory`` event enqueued through the real producer
  intake is dispatched by the drainer's repair branch (row leaves ``pending``
  as ``dispatched`` — never ``no_op``), with the dispatcher resolving the
  agent from the repair payload (F1 + F2).
- The repair payload resolves via resolve_assigned_agent; a payload without
  agent metadata yields needs_manual_review (documents why F2 exists).
- Unknown event types still no_op (negative test).
- Exhaustion emits review_factory.repair_exhausted on the event bus (F4).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _import_dispatcher():
    """Import prismatic.dispatcher, retrying once on the swarmlock quirk.

    In a fresh interpreter the first import can raise ModuleNotFoundError for
    the optional 'swarmlock' primitive while prismatic.core installs its
    fallback; the immediate retry succeeds. (Pre-existing repo behavior —
    existing dispatcher tests work around it with importlib.reload.)
    """
    try:
        from prismatic import dispatcher

        return dispatcher
    except ModuleNotFoundError:
        from prismatic import dispatcher

        return dispatcher


_dispatcher = _import_dispatcher()
dispatch_repair_by_identifier = _dispatcher.dispatch_repair_by_identifier
resolve_assigned_agent = _dispatcher.resolve_assigned_agent


def _load_drainer():
    path = REPO_ROOT / "scripts" / "drain_webhook_queue.py"
    spec = importlib.util.spec_from_file_location("drain_webhook_queue", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _drain_args(**overrides):
    base = dict(
        dry_run=False, max=100, backfill=False, reset=False, since=None, until=None
    )
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.fixture()
def state_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PRISMATIC_LINEAR_RATE_LIMIT_STATE", str(tmp_path / "rl.json"))
    monkeypatch.setenv("PRISMATIC_ASSIGNED_AGENT_DRY_RUN", "1")
    monkeypatch.setenv("PRISMATIC_VISIBLE_AGENT_STREAM", "0")
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    return tmp_path


def _enqueue_repair(identifier="RF-REPAIR-test0001", target_agent="fred"):
    from prismatic.ingestion_queue import enqueue_multi_channel_task

    payload = {
        "kind": "review-factory-repair",
        "review_job_id": "test-job-0001",
        "task_id": "GRO-TEST",
        "target_agent": target_agent,
        "title": "REPAIR: fix rejected candidate for test",
        "requeue": {"method": "ReviewQueue.requeue_repaired_candidate"},
    }
    return enqueue_multi_channel_task(
        identifier=identifier,
        channel="review-factory",
        target_agent=target_agent,
        title=payload["title"],
        raw_payload=payload,
    )


def _queue_row(state_dir, identifier):
    db_path = Path(state_dir) / "linear_webhook_queue.db"
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT dispatch_status, resolver_status, target_agent "
            "FROM linear_webhook_queue WHERE identifier = ?",
            (identifier,),
        ).fetchone()
    finally:
        conn.close()


def test_repair_event_dispatched_not_noop(state_env):
    """F1+F2: the drainer's repair branch dispatches; the row is not no_op'd."""
    drainer = _load_drainer()
    _enqueue_repair()
    rc = drainer.drain(
        _drain_args(), repair_dispatch_fn=dispatch_repair_by_identifier
    )
    assert rc == 0
    status, resolver_status, target_agent = _queue_row(state_env, "RF-REPAIR-test0001")
    assert status == "dispatched", f"repair event ended as {status}, want dispatched"
    assert resolver_status == "resolved", f"resolver: {resolver_status}"
    assert target_agent == "fred"


def test_repair_dispatch_result_carries_agent(state_env):
    """F2: dispatch_repair_by_identifier resolves fred from the payload."""
    _enqueue_repair(identifier="RF-REPAIR-test0002")
    result = dispatch_repair_by_identifier("RF-REPAIR-test0002")
    assert isinstance(result, dict)
    assert result["status"] == "dispatched", result
    assert result["target_agent"] == "fred", result


def test_repair_payload_agent_resolution():
    """F2 unit: payload target_agent resolves; missing metadata does not."""
    ok = resolve_assigned_agent(
        {"raw_json": json.dumps({"kind": "review-factory-repair", "target_agent": "fred"})}
    )
    assert ok.status == "resolved"
    assert ok.target_agent == "fred"

    missing = resolve_assigned_agent(
        {"raw_json": json.dumps({"kind": "review-factory-repair"})}
    )
    assert missing.status == "needs_manual_review"


def test_unknown_event_type_still_noop(state_env):
    """Negative test: event types the drainer doesn't know stay no_op."""
    from prismatic.ingestion_queue import enqueue_multi_channel_task

    drainer = _load_drainer()
    enqueue_multi_channel_task(
        identifier="UNKNOWN-1",
        channel="mystery",
        target_agent="fred",
        title="unknown",
        raw_payload={"kind": "mystery"},
    )
    rc = drainer.drain(_drain_args())
    assert rc == 0
    status, _, _ = _queue_row(state_env, "UNKNOWN-1")
    assert status == "no_op", f"unknown event ended as {status}, want no_op"


def test_exhaustion_emits_bus_event(tmp_path, monkeypatch):
    """F4: repair_redispatch_exhausted emits review_factory.repair_exhausted."""
    import prismatic.review_factory.events as rf_events
    from prismatic.review_factory.db import ReviewFactoryDB
    from prismatic.review_factory.queue import ReviewQueue

    captured = []
    monkeypatch.setattr(
        rf_events,
        "emit_rf_event",
        lambda event_type, payload, source="review_factory": captured.append(
            (event_type, payload)
        ),
    )
    monkeypatch.setattr(
        "prismatic.review_factory.queue.candidate_merged_in_main",
        lambda *a, **k: None,
    )

    db = ReviewFactoryDB(db_path=tmp_path / "rf.db")
    try:
        queue = ReviewQueue(db=db)
        fake_job = SimpleNamespace(
            review_job_id="job-exhaust-1",
            task_id="GRO-TEST",
            repository="mbgulden/prismatic-engine",
            candidate_commit="deadbeef",
            repair_attempts=3,
            repair_last_dispatch_at="",
        )
        monkeypatch.setattr(
            db, "list_review_jobs", lambda state=None: [fake_job]
        )
        summary = queue.redispatch_stalled_repairs(max_attempts=3)
    finally:
        db.close()

    assert summary["exhausted"] == [{"job_id": "job-exhaust-1", "attempts": 3}]
    assert len(captured) == 1, f"expected 1 bus event, got {captured}"
    event_type, payload = captured[0]
    assert event_type == "review_factory.repair_exhausted"
    assert payload["review_job_id"] == "job-exhaust-1"
    assert payload["attempts"] == 3
    assert payload["max_attempts"] == 3
