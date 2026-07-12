import json
from pathlib import Path

from fastapi.testclient import TestClient


def _write_merge_state(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "last_scan": "2026-07-12T10:00:00+00:00",
                "last_apply": None,
                "drift_detected": True,
                "total_merged": 2,
                "pending": {
                    "GRO-1001": {
                        "tier": 2,
                        "confidence": 91.5,
                        "files": ["prismatic/a.py"],
                        "modified_files": 1,
                        "contention_files": [],
                        "checks": {"passed": True},
                        "mergeable": True,
                    },
                    "GRO-1002": {
                        "tier": 4,
                        "confidence": 61.0,
                        "files": ["prismatic/gateway/server.py"],
                        "modified_files": 1,
                        "contention_files": ["prismatic/gateway/server.py"],
                        "checks": {"passed": False},
                        "blocked_reason": "conflict with canonical gateway changes",
                    },
                },
                "merged": {
                    "GRO-999": {"commit": "abcdef123", "merged_at": "2026-07-11T09:00:00+00:00", "files": ["README.md"]},
                    "GRO-998": {"commit": "123456789", "merged_at": "2026-07-10T09:00:00+00:00", "files": ["pyproject.toml"]},
                },
            }
        ),
        encoding="utf-8",
    )


def _client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path / "state"))
    merge_state = tmp_path / "merge" / "state_v6.json"
    monkeypatch.setenv("PRISMATIC_MERGE_STATE_PATH", str(merge_state))
    _write_merge_state(merge_state)
    from prismatic.gateway import server

    return TestClient(server.app)


def test_merge_status_endpoint_normalizes_state(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    res = client.get("/api/gateway/merge/status")
    assert res.status_code == 200
    payload = res.json()
    assert payload["source"] == "merge_state+governance_triage+merge_control_state"
    assert payload["pending_count"] == 2
    assert payload["open_count"] == 2
    assert payload["merged_count"] == 2
    assert payload["mergeable_count"] == 1
    assert payload["blocked_count"] == 1
    assert payload["conflict_count"] == 1
    assert payload["checks"] == {"passed": 1, "failed": 1, "unknown": 0}
    assert payload["duplicate_family_count"] >= 1
    assert payload["evidence"]["triage_source_issue"] == "GRO-3520"
    assert any(row["ticket"] == "GRO-1001" and row["mergeable"] for row in payload["pending"])
    assert any(row["ticket"] == "GRO-1002" and row["blocked"] for row in payload["pending"])


def test_merge_control_allowlist_and_timeline(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    invalid = client.post("/api/gateway/merge/control/rm-rf")
    assert invalid.status_code == 400
    assert invalid.json()["allowed_actions"] == ["hold", "promote", "refresh"]

    hold = client.post("/api/gateway/merge/control/hold")
    assert hold.status_code == 200
    payload = hold.json()
    assert payload["status"] == "ok"
    assert payload["entry"]["action"] == "hold"
    assert payload["timeline_item"]["source"] == "MergeControl"
    assert payload["stdout"] == ""
    assert payload["stderr"] == ""

    status = client.get("/api/gateway/merge/status").json()
    assert status["last_control_action"]["action"] == "hold"
    timeline = client.get("/api/timeline?source=MergeControl").json()
    assert any(item["title"] == "Hold merge queue" for item in timeline["items"])

    promote = client.post("/api/gateway/merge/control/promote")
    assert promote.status_code == 200
    assert promote.json()["timeline_item"]["source"] == "MergeControl"
    refresh = client.post("/api/gateway/merge/control/refresh")
    assert refresh.status_code == 200
    assert refresh.json()["timeline_item"]["source"] == "MergeControl"


def test_merge_dashboard_fetch_action_contract(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    html = dashboard.text
    assert 'fetch("/api/gateway/merge/status")' in html
    assert 'fetch(`/api/gateway/merge/control/${action}`, { method: "POST" })' in html
    assert "Merge Pipeline API unavailable" in html
    assert "No fallback data rendered" in html
    assert "merge-duplicate-families" in html
    assert "mockMerge" not in html
    assert "auto-merges to staging" not in html
    assert "STDOUT" not in html
