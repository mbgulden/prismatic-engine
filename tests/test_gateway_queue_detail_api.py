from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from prismatic.run_records import AgentRunRecord, AgentRunRecordStore


def _iso(delta_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat()


def _seed_queue_state(tmp_path: Path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(state_dir))
    store = AgentRunRecordStore(str(state_dir / "run_records.json"))
    records = [
        AgentRunRecord("run-pending", "GRO-201", "agent:jules", status="pending", started_at=_iso(-600)),
        AgentRunRecord("run-processing", "GRO-202", "agent:agy", status="running", started_at=_iso(-500)),
        AgentRunRecord("run-completed", "GRO-203", "agent:kai", status="completed", started_at=_iso(-900), completed_at=_iso(-100)),
        AgentRunRecord("run-failed", "GRO-204", "agent:codex", status="failed", started_at=_iso(-800), completed_at=_iso(-200), error_message="unit test failure"),
        AgentRunRecord("run-dlq", "GRO-205", "agent:ned", status="failed", started_at=_iso(-800), completed_at=_iso(-300), error_message="dead_letter after max retries"),
        AgentRunRecord("run-skipped", "GRO-206", "agent:fred", status="skipped", started_at=_iso(-700), completed_at=_iso(-350), error_message="skipped by quarantine label"),
    ]
    store._records = {record.run_id: record for record in records}
    store._flush_to_disk()
    from prismatic.gateway import server

    server._run_store = store
    server._webhook_counters.update(
        {
            "github_received": 2,
            "github_auth_failed": 1,
            "github_published": 1,
            "linear_received": 3,
            "linear_auth_failed": 1,
            "linear_published": 2,
        }
    )
    return TestClient(server.app)


def test_queue_detail_normalized_shape_and_classifications(tmp_path, monkeypatch):
    client = _seed_queue_state(tmp_path, monkeypatch)

    response = client.get("/api/gateway/webhooks/queue/detail")
    assert response.status_code == 200, response.text
    data = response.json()

    for key in ["source", "generated_at", "empty", "queue_depths", "items", "retry_candidates", "dead_letter", "skipped", "processing", "completed_recently", "failed_recently", "webhook_counters", "dispatcher_context", "recovery_context", "recent_timeline", "evidence"]:
        assert key in data
    assert data["empty"] is False
    assert data["queue_depths"]["pending"] == 1
    assert data["queue_depths"]["processing"] == 1
    assert data["queue_depths"]["completed"] == 1
    assert data["queue_depths"]["failed"] >= 2
    assert data["queue_depths"]["dead_letter"] >= 1
    assert data["queue_depths"]["skipped"] >= 1
    retry_ids = {item["run_id"] for item in data["retry_candidates"]}
    assert "run-failed" in retry_ids
    assert "run-dlq" not in retry_ids
    assert data["dead_letter"][0]["dead_lettered"] is True
    assert data["skipped"][0]["skipped"] is True
    assert data["processing"][0]["processing"] is True
    assert data["webhook_counters"]["github_auth_failed"] == 1
    assert data["evidence"]["classification_rules"]["retryable"]


def test_queue_item_detail_shape(tmp_path, monkeypatch):
    client = _seed_queue_state(tmp_path, monkeypatch)

    response = client.get("/api/gateway/webhooks/queue/run-failed")
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["item"]["run_id"] == "run-failed"
    assert data["item"]["retryable"] is True
    assert data["run_record"]["issue_id"] == "GRO-204"
    for key in ["item", "run_record", "recent_timeline", "retry_history", "recovery_context", "evidence"]:
        assert key in data


def test_queue_controls_are_audit_only_and_timeline_backed(tmp_path, monkeypatch):
    client = _seed_queue_state(tmp_path, monkeypatch)

    retry = client.post("/api/gateway/webhooks/queue/retry/run-failed")
    assert retry.status_code == 200, retry.text
    retry_data = retry.json()
    assert retry_data["ok"] is True
    assert retry_data["stdout"] == ""
    assert retry_data["stderr"] == ""
    assert retry_data["timeline_item"]["source"] == "QueueControl"

    purge = client.post("/api/gateway/webhooks/queue/purge")
    assert purge.status_code == 200, purge.text
    purge_data = purge.json()
    assert purge_data["ok"] is True
    assert purge_data["stdout"] == ""
    assert purge_data["stderr"] == ""
    assert purge_data["timeline_item"]["source"] == "QueueControl"

    timeline = client.get("/api/timeline?source=QueueControl&limit=10")
    assert timeline.status_code == 200
    sources = {item["source"] for item in timeline.json()["items"]}
    assert "QueueControl" in sources


def test_dashboard_queue_contract_live_wiring_no_mock_fallback(tmp_path, monkeypatch):
    client = _seed_queue_state(tmp_path, monkeypatch)

    response = client.get("/dashboard")
    assert response.status_code == 200
    html = response.text
    assert "webhooks/queue/detail" in html
    assert "webhooks/queue/${encodeURIComponent(id)}" in html
    assert "webhooks/queue/retry/${encodeURIComponent(taskId)}" in html
    assert "webhooks/queue/purge" in html
    assert "queue-detail-source-line" in html
    assert "queue-depth-chips" in html
    assert "queue-retry-candidates" in html
    assert "queue-dead-letter-items" in html
    assert "Queue detail API unavailable" in html
    assert "No mock queue fallback rendered" in html
    for forbidden in ["mockQueue", "fake queue", "fake retry", "fake dead-letter"]:
        assert forbidden not in html
