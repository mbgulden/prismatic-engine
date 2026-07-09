from __future__ import annotations

import json
from dataclasses import dataclass

from fastapi.testclient import TestClient

from prismatic.gateway import server


@dataclass
class FakeRun:
    run_id: str
    issue_id: str
    agent_name: str
    status: str
    started_at: str
    completed_at: str | None = None


class FakeRunStore:
    def __init__(self, runs):
        self.runs = runs
        self.reloaded = False

    def reload(self):
        self.reloaded = True

    def get_recent_runs(self, limit=200):
        return self.runs[:limit]


def test_api_agents_merges_registry_and_recent_runs(tmp_path, monkeypatch):
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "kai": {"status": "running", "last_heartbeat": "2026-07-09T05:00:00Z"},
                "agent:kai-js": {"status": "idle", "last_seen": "2026-07-09T04:55:00Z"},
            }
        )
    )
    monkeypatch.setenv("PRISMATIC_AGENT_REGISTRY", str(registry))
    fake_store = FakeRunStore(
        [
            FakeRun(
                run_id="run-1",
                issue_id="GRO-3337",
                agent_name="kai",
                status="running",
                started_at="2026-07-09T05:00:00+00:00",
            ),
            FakeRun(
                run_id="run-2",
                issue_id="GRO-3336",
                agent_name="jules",
                status="completed",
                started_at="2026-07-09T04:00:00+00:00",
                completed_at="2026-07-09T04:00:12+00:00",
            ),
        ]
    )
    monkeypatch.setattr(server, "_run_store", fake_store)

    response = TestClient(server.app).get("/api/agents")

    assert response.status_code == 200
    payload = response.json()
    assert "updated_at" in payload
    assert payload["agents"]["kai"]["status"] == "Running"
    assert payload["agents"]["kai"]["dispatched"] == 1
    assert payload["agents"]["kai"]["queue"] == ["GRO-3337 — running"]
    assert payload["agents"]["jules"]["duration"] == "12.0s"
    assert fake_store.reloaded is True
