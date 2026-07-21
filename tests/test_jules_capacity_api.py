from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from prismatic.gateway.server import app
from prismatic.jules_capacity import JULES_DAILY_CAPACITY_MARKER, record_jules_launch


def test_gateway_jules_capacity_endpoint_uses_privacy_minimal_durable_ledger(
    tmp_path: Path, monkeypatch
) -> None:
    db = tmp_path / "private" / "jules.sqlite3"
    monkeypatch.setenv("PRISMATIC_JULES_CAPACITY_DB_PATH", str(db))
    today = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    record_jules_launch(
        issue_id="GRO-API",
        repository="https://user:pass@example.com/repo.git?token=SECRET",
        source_path="/tmp/jules-GRO-API.log",
        launch_identity="req-api",
        launch_ts_utc=today,
        lifecycle_status="failed",
        error_class="cli failed without raw output password=SECRET",
    )

    response = TestClient(app).get("/api/gateway/jules/capacity")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["marker"] == JULES_DAILY_CAPACITY_MARKER
    assert payload["limit"] == 300
    assert payload["observed_launches"] == 1
    assert payload["failed"] == 1
    assert payload["remaining_observed_capacity"] == 299
    assert payload["coverage_state"] in {"partial_coverage", "fresh", "unavailable"}
    public_text = response.text.lower()
    forbidden = [
        "raw output",
        "password",
        "secret",
        "user:pass",
        "token=",
        "gro-api",
        "session_id",
        "issue_id",
        "repository",
        "source_path",
        "launch_key",
        "current",
        "prompt",
        "title",
    ]
    for needle in forbidden:
        assert needle not in public_text
