import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from prismatic.cost.tracker import calculate_cost
from prismatic.vertex_telemetry import VertexBillingLedger


def _seed_quota_state(tmp_path: Path, monkeypatch):
    state_dir = tmp_path / "state"
    db_path = state_dir / "event_router.db"
    cost_db = tmp_path / "cost.db"
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(state_dir))
    monkeypatch.setenv("PRISMATIC_EVENT_ROUTER_DB", str(db_path))
    monkeypatch.setenv("PRISMATIC_COST_DB", str(cost_db))

    ledger = VertexBillingLedger(str(db_path))
    now = datetime.now(timezone.utc).isoformat()
    ledger.record_quota_snapshot(
        [
            {
                "region": "us-central1",
                "model": "gemini-2.5-pro",
                "metric_type": "tpm",
                "metric_name": "tokens-per-minute",
                "usage": 900,
                "limit_value": 1000,
                "utilization_pct": 90.0,
                "recorded_at": now,
            }
        ],
        project_id="quota-test",
    )
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS telemetry_credit_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT,
                credits_spent REAL NOT NULL,
                operation TEXT,
                recorded_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """INSERT INTO telemetry_credit_ledger
               (run_id, agent, provider, model, credits_spent, operation, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("jules-session", "agent:jules", "google-jules", "jules-cli", 0, "dispatch", now),
        )
        conn.execute(
            """INSERT INTO telemetry_credit_ledger
               (run_id, agent, provider, model, credits_spent, operation, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("ai-ultra", "agent:agy", "google-antigravity", "gemini-omni-veo-lyria-pool", 125, "media_generation_video", now),
        )
        conn.commit()
    with sqlite3.connect(str(cost_db)) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS dispatch_costs (
                run_id TEXT,
                issue_id TEXT,
                agent TEXT,
                model TEXT,
                tokens_in INTEGER,
                tokens_out INTEGER,
                cost_dollars REAL,
                created_at REAL
            )"""
        )
        conn.execute(
            """INSERT INTO dispatch_costs
               (run_id, issue_id, agent, model, tokens_in, tokens_out, cost_dollars, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "cost-run",
                "GRO-Q",
                "codex",
                "gemini-2.5-pro",
                1000,
                2000,
                calculate_cost("gemini-2.5-pro", 1000, 2000),
                datetime.now(timezone.utc).timestamp(),
            ),
        )
        conn.commit()
    from prismatic.gateway import server

    return TestClient(server.app)


def test_quota_endpoint_normalizes_vertex_subscriptions_and_cost(tmp_path, monkeypatch):
    client = _seed_quota_state(tmp_path, monkeypatch)

    response = client.get("/api/quota")
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["source"] == "vertex_ledger+cost_tracker+credit_tracker+subscription_caps+quota_control_state"
    assert data["pressure"] in {"critical", "warning", "ok", "unknown", "exhausted"}
    assert data["evidence"]["vertex_quota_records"] == 1
    assert data["evidence"]["subscription_caps"] >= 8
    assert data["cost_summary"]["available"] is True
    assert data["ai_ultra_credits"]["available"] is True
    assert data["ai_ultra_credits"]["spent"] >= 125
    ids = {item["id"] for item in data["current"]}
    assert "google-jules-cli-sessions-day" in ids
    assert "google-ai-ultra-credits-month" in ids
    assert "openai-codex-subscription-month" in ids
    assert "minimax-subscription-month" in ids
    assert "deepseek-api-rates" in ids
    vertex_items = [item for item in data["current"] if item.get("provider") == "gcp-vertex-ai"]
    assert vertex_items and vertex_items[0]["remaining_pct"] == 10.0


def test_quota_poll_records_intent_and_timeline_without_shell_output(tmp_path, monkeypatch):
    client = _seed_quota_state(tmp_path, monkeypatch)

    response = client.post("/api/quota/poll")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True
    assert data["entry"]["action"] == "poll"
    assert data["entry"]["mode"] == "intent-recorded"
    assert data["timeline_item"]["source"] == "QuotaControl"
    assert data["stdout"] == ""
    assert data["stderr"] == ""

    status = client.get("/api/quota").json()
    assert status["last_poll"]["action"] == "poll"
    timeline = client.get("/api/timeline?source=QuotaControl").json()
    assert any(item["title"] == "Quota poll requested" for item in timeline["items"])


def test_dashboard_quota_contract_has_live_wiring_and_no_fake_fallbacks(tmp_path, monkeypatch):
    client = _seed_quota_state(tmp_path, monkeypatch)

    response = client.get("/dashboard")
    assert response.status_code == 200
    html = response.text
    assert 'fetch("/api/quota")' in html
    assert 'fetch("/api/quota/poll", { method: "POST" })' in html
    assert "quota-state-panel" in html
    assert "quota-ai-ultra" in html
    assert "Quota API unavailable" in html
    assert "No fallback data rendered" in html
    assert "mockQuota" not in html
    assert "QuotaControl" in html
    assert "Trigger Sync" not in html
