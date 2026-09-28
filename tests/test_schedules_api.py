"""API contract tests for the Schedule Observatory endpoint (GET /schedules).

WI-8 (cron overhaul): the dashboard's Schedule Observatory renders this endpoint,
so the response shape is pinned here — a bare list of normalized schedule
records. The endpoint itself stays read-only; mutations live on the per-system
paths and are covered by test_schedule_records.py.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from prismatic.gateway import server

EXPECTED_KEYS = {
    "id",
    "name",
    "owner",
    "schedule_type",
    "schedule_expr",
    "enabled",
    "next_run_at",
    "last_run",
    "deep_link",
    "metadata",
}

KNOWN_OWNERS = {"prismatic", "agy", "jules", "task-manager"}
KNOWN_TYPES = {"cron", "systemd-timer", "one-shot", "interval", "remote-managed"}
KNOWN_STATUSES = {"success", "failed", "running", "cancelled"}


def assert_record_shape(record: dict) -> None:
    missing = EXPECTED_KEYS - set(record.keys())
    assert not missing, f"schedule record missing keys: {sorted(missing)}"
    assert record["owner"] in KNOWN_OWNERS, f"unknown owner: {record['owner']!r}"
    assert record["schedule_type"] in KNOWN_TYPES, (
        f"unknown schedule_type: {record['schedule_type']!r}"
    )
    assert isinstance(record["enabled"], bool)
    assert isinstance(record["metadata"], dict)
    last_run = record["last_run"]
    if last_run is not None:
        assert isinstance(last_run, dict)
        assert "fired_at" in last_run and "status" in last_run
        assert last_run["status"] in KNOWN_STATUSES, (
            f"unknown last_run status: {last_run['status']!r}"
        )


def get_schedules() -> list:
    response = TestClient(server.app).get("/schedules")
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    return payload


def test_schedules_returns_unified_record_list() -> None:
    payload = get_schedules()
    # Every provider adapter contributes records even on a clean box
    # (prismatic cron inventory may be empty, but agy/jules/systemd fallbacks fire).
    assert payload, "expected at least one schedule record from GET /schedules"
    for record in payload:
        assert_record_shape(record)


def test_schedules_includes_systemd_timers() -> None:
    payload = get_schedules()
    timers = [r for r in payload if r["schedule_type"] == "systemd-timer"]
    assert timers, "expected at least one systemd-timer record"
    for timer in timers:
        assert timer["id"].startswith("prismatic:systemd:"), timer["id"]


def test_schedules_includes_seeded_prismatic_cron_jobs(tmp_path, monkeypatch) -> None:
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(
        json.dumps(
            [
                {
                    "id": "backup",
                    "name": "Database Backup",
                    "enabled": True,
                    "schedule": "0 0 * * *",
                    "script": "backup.py",
                    "next_run_at": "2026-09-28T00:00:00+00:00",
                },
                {
                    "id": "cleanup",
                    "name": "Tmp Cleanup",
                    "paused": True,
                    "schedule": "0 * * * *",
                    "script": "cleanup.py",
                },
            ]
        )
    )
    monkeypatch.setenv("PRISMATIC_CRON_JOBS", str(jobs_file))

    payload = get_schedules()
    backup = next((r for r in payload if r["id"] == "prismatic:cron:backup"), None)
    assert backup is not None, "seeded cron job not surfaced by GET /schedules"
    assert backup["name"] == "Database Backup"
    assert backup["owner"] == "prismatic"
    assert backup["schedule_type"] == "cron"
    assert backup["schedule_expr"] == "0 0 * * *"
    assert backup["enabled"] is True
    assert backup["next_run_at"] == "2026-09-28T00:00:00+00:00"
    assert backup["metadata"]["script"] == "backup.py"

    cleanup = next((r for r in payload if r["id"] == "prismatic:cron:cleanup"), None)
    assert cleanup is not None
    assert cleanup["enabled"] is False

    for record in payload:
        assert_record_shape(record)


def test_schedules_ids_are_unique() -> None:
    """Every schedule record carries a globally unique id for dashboard rendering."""
    payload = get_schedules()
    ids = [r["id"] for r in payload]
    assert len(ids) == len(set(ids)), "duplicate schedule ids in GET /schedules"
