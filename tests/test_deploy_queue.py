"""Unit tests for WebhookQueue (P1), DeployAuditLog (W4), and Linear State Machine (P3)."""

import tempfile
from pathlib import Path

from pe.deploy.audit import DeployAuditLog
from pe.deploy.linear_state import validate_linear_state_transition
from pe.deploy.queue import WebhookQueue


def test_webhook_queue_lifecycle():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        q_path = tmp_path / "queue.json"
        dl_path = tmp_path / "dead_letter.json"

        wq = WebhookQueue(queue_path=q_path, dead_letter_path=dl_path)

        # Enqueue item
        item = wq.enqueue({"pr_sha": "abc12345", "pr_number": 418})
        assert item.status == "pending"
        assert len(wq.get_pending()) == 1

        # Record failures until dead-letter
        for attempt in range(1, 6):
            res = wq.record_failure(item.item_id, f"Error attempt {attempt}")

        assert res.status == "dead_letter"
        assert len(wq.get_pending()) == 0
        assert len(wq.list_dead_letters()) == 1


def test_deploy_audit_log():
    with tempfile.TemporaryDirectory() as tmp_dir:
        log_file = Path(tmp_dir) / "deploy_audit.log"
        audit = DeployAuditLog(log_path=log_file)

        audit.record_entry(
            action="test_action",
            actor="test_actor",
            client_ip="127.0.0.1",
            hmac_sig="sha256=abcdef",
            status="success",
        )

        entries = audit.read_entries(limit=10)
        assert len(entries) == 1
        assert entries[0]["action"] == "test_action"
        assert entries[0]["actor"] == "test_actor"


def test_linear_state_validation():
    # Allowed transitions
    ok1, _ = validate_linear_state_transition("GRO-4188", "In Review")
    assert ok1 is True

    ok2, _ = validate_linear_state_transition("GRO-4188", "In Progress")
    assert ok2 is True

    # Blocked transitions
    blocked1, msg1 = validate_linear_state_transition("GRO-4188", "Backlog")
    assert blocked1 is False
    assert "Backlog" in msg1

    blocked2, msg2 = validate_linear_state_transition("GRO-4188", "Cancelled")
    assert blocked2 is False
    assert "Cancelled" in msg2
