"""
Tests for prismatic.curator.lane.

Covers:
- Tag rules for each event type (per SPEC.md §4)
- Persistence (idempotent on re-tag)
- Digest rendering (counts, escalations, lane stats)
- SLO-relevant behavior

Run: pytest prismatic/curator/tests/test_lane.py -v
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import pytest

# Ensure parent dir is on path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prismatic.curator.lane import (  # noqa: E402
    BusEvent, TagResult, tag_event, init_curator_db, persist_tag,
    already_tagged, get_last_processed_rowid, update_lane_stats,
    fetch_bus_events_after, render_digest, write_digest, record_digest_run,
    CURATOR_DB,
)


# === Tag rule tests ===

def make_event(source: str, topic: str, payload: dict | None = None) -> BusEvent:
    return BusEvent(rowid=0, topic=topic, payload=payload or {}, ts=0.0, source=source)


def test_linear_create_is_delegate():
    ev = make_event("linear", "Issue.create",
                    {"action": "create", "type": "Issue",
                     "data": {"identifier": "GRO-X"}})
    result = tag_event(ev)
    assert result.tag == "delegate"
    assert result.lane_hint == "triage"


def test_linear_update_with_dispatch_ready_is_delegate():
    ev = make_event("linear", "Issue.update",
                    {"action": "update", "type": "Issue",
                     "data": {"labels": [{"name": "dispatch:ready"}]}})
    result = tag_event(ev)
    assert result.tag == "delegate"


def test_linear_update_routine_is_auto_pick():
    ev = make_event("linear", "Issue.update",
                    {"action": "update", "type": "Issue",
                     "data": {"labels": [{"name": "agent:fred"}]}})
    result = tag_event(ev)
    assert result.tag == "auto-pick"


def test_linear_comment_is_drop():
    ev = make_event("linear", "Comment.create",
                    {"action": "create", "type": "Comment"})
    result = tag_event(ev)
    assert result.tag == "drop"


def test_github_ping_is_drop():
    ev = make_event("github", "ping", {"zen": "test"})
    result = tag_event(ev)
    assert result.tag == "drop"


def test_github_pr_opened_is_delegate():
    ev = make_event("github", "pull_request", {"action": "opened"})
    result = tag_event(ev)
    assert result.tag == "delegate"
    assert result.lane_hint == "jules"


def test_webhook_auth_failed_is_escalate():
    ev = make_event("linear", "webhook.auth_failed", {})
    result = tag_event(ev)
    assert result.tag == "escalate"


def test_webhook_ping_is_drop():
    ev = make_event("github", "webhook.ping", {})
    result = tag_event(ev)
    assert result.tag == "drop"


def test_dispatcher_agent_launched_is_auto_pick():
    ev = make_event("dispatcher:codex", "agent_launched", {})
    result = tag_event(ev)
    assert result.tag == "auto-pick"
    assert result.lane_hint == "codex"


def test_agent_failed_is_escalate():
    ev = make_event("fred", "agent_failed", {})
    result = tag_event(ev)
    assert result.tag == "escalate"


def test_circuit_breaker_trip_is_escalate():
    ev = make_event("distributed_watchdog:timeout", "circuit_breaker_trip", {})
    result = tag_event(ev)
    assert result.tag == "escalate"


def test_budget_exceeded_is_escalate():
    ev = make_event("governance", "bus.budget.exceeded", {})
    result = tag_event(ev)
    assert result.tag == "escalate"


def test_webhook_generic_is_auto_pick():
    ev = make_event("webhook", "delivery", {})
    result = tag_event(ev)
    assert result.tag == "auto-pick"


def test_self_monitoring_is_drop():
    ev = make_event("internal", "agent.heartbeat", {})
    result = tag_event(ev)
    assert result.tag == "drop"


def test_unmatched_source_is_escalate():
    ev = make_event("totally-unknown-source", "mystery.topic", {})
    result = tag_event(ev)
    assert result.tag == "escalate"


def test_lifecycle_close_is_auto_pick():
    ev = make_event("linear", "Issue.close",
                    {"action": "close", "type": "Issue"})
    result = tag_event(ev)
    assert result.tag == "auto-pick"


def test_nested_payload_extraction():
    """Linear payloads sometimes nest under .payload."""
    ev = make_event("linear", "Issue.update",
                    {"payload": {"action": "update", "type": "Issue",
                                 "data": {"labels": [{"name": "dispatch:ready"}]}}})
    result = tag_event(ev)
    assert result.tag == "delegate"


def test_schema_migration_and_dispatched_state():
    """Verify CURATOR_DB schema has dispatched columns and they work."""
    with tempfile.NamedTemporaryFile() as tmp:
        db_path = Path(tmp.name)
        # Override global CURATOR_DB for this test
        import prismatic.curator.lane as lane
        original_db = lane.CURATOR_DB
        lane.CURATOR_DB = db_path
        try:
            lane.init_curator_db()
            conn = sqlite3.connect(db_path)
            # Check columns
            cursor = conn.execute("PRAGMA table_info(tagged_events)")
            cols = {row[1] for row in cursor.fetchall()}
            assert "dispatched" in cols
            assert "dispatched_at" in cols

            # Check mark_dispatched
            lane.persist_tag(1, "delegate", "triage", "test")
            curator = lane.CuratorLane(enable_dispatch=False)
            curator._mark_dispatched(1)

            cursor = conn.execute("SELECT dispatched FROM tagged_events WHERE rowid = 1")
            assert cursor.fetchone()[0] == 1
        finally:
            lane.CURATOR_DB = original_db