"""Tests for prismatic.schedules — schedule inventory + mutation.

GRO-2402 follow-up: schedules.py is used by the gateway to expose
schedule inventory (cron jobs, systemd timers, AGY/Jules schedules).
It also handles user requests to mutate cron jobs via chat.

Until now it had zero direct tests. A bug here would silently break
schedule visibility or authorize unauthorized mutations.

These tests cover:
- Dataclass shapes (LastRunInfo, ScheduleRecord, ScheduleEvent)
- to_dict / from_dict round-trips
- get_prismatic_cron_jobs (real file format)
- get_systemd_timer_schedules
- Constants (OWNER_*, TYPE_*, STATUS_*)
- UnauthorizedMutationError
- request_schedule_mutation (validation only — no actual mutation)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

_PE_ROOT = Path(os.environ.get(
    "PRISMATIC_HOME",
    os.path.join(os.environ.get("HOME", ""), "work", "prismatic-engine")
))
sys.path.insert(0, str(_PE_ROOT))
sys.path.insert(0, str(_PE_ROOT / ".venv_dev" / "lib" / "python3.12" / "site-packages"))

from prismatic.schedules import (  # noqa: E402
    LastRunInfo,
    ScheduleRecord,
    ScheduleEvent,
    get_prismatic_cron_jobs,
    get_systemd_timer_schedules,
    get_all_schedules,
    UnauthorizedMutationError,
    request_schedule_mutation,
    OWNER_PRISMATIC,
    OWNER_AGY,
    OWNER_JULES,
    OWNER_TASK_MANAGER,
    TYPE_CRON,
    TYPE_SYSTEMD,
    TYPE_ONE_SHOT,
    TYPE_INTERVAL,
    TYPE_REMOTE,
    STATUS_SUCCESS,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_CANCELLED,
)


# ── Constants ────────────────────────────────────────────────────────
def test_owner_constants():
    assert OWNER_PRISMATIC == "prismatic"
    assert OWNER_AGY == "agy"
    assert OWNER_JULES == "jules"
    assert OWNER_TASK_MANAGER == "task-manager"


def test_type_constants():
    assert TYPE_CRON == "cron"
    assert TYPE_SYSTEMD == "systemd-timer"
    assert TYPE_ONE_SHOT == "one-shot"
    assert TYPE_INTERVAL == "interval"
    assert TYPE_REMOTE == "remote-managed"


def test_status_constants():
    assert STATUS_SUCCESS == "success"
    assert STATUS_FAILED == "failed"
    assert STATUS_RUNNING == "running"
    assert STATUS_CANCELLED == "cancelled"


# ── LastRunInfo ──────────────────────────────────────────────────────
def test_last_run_info_minimal():
    info = LastRunInfo(fired_at="2026-06-24T18:00:00Z", status=STATUS_SUCCESS)
    assert info.fired_at == "2026-06-24T18:00:00Z"
    assert info.status == STATUS_SUCCESS
    assert info.run_id is None
    assert info.duration_sec is None
    assert info.error_message is None


def test_last_run_info_with_all_fields():
    info = LastRunInfo(
        fired_at="2026-06-24T18:00:00Z",
        status=STATUS_FAILED,
        run_id="run-123",
        duration_sec=12.5,
        error_message="timeout",
    )
    assert info.run_id == "run-123"
    assert info.duration_sec == 12.5
    assert info.error_message == "timeout"


def test_last_run_info_to_dict_omits_none():
    """to_dict() skips None fields (forward-compat with old data)."""
    info = LastRunInfo(fired_at="2026-06-24T18:00:00Z", status=STATUS_SUCCESS)
    d = info.to_dict()
    assert d == {"fired_at": "2026-06-24T18:00:00Z", "status": STATUS_SUCCESS}
    assert "run_id" not in d
    assert "error_message" not in d


def test_last_run_info_to_dict_includes_set_fields():
    info = LastRunInfo(
        fired_at="x", status="y", error_message="boom",
    )
    d = info.to_dict()
    assert d["error_message"] == "boom"


# ── ScheduleRecord ───────────────────────────────────────────────────
def test_schedule_record_required_fields():
    r = ScheduleRecord(
        id="cron:1", name="Test", owner=OWNER_PRISMATIC,
        schedule_type=TYPE_CRON, schedule_expr="0 * * * *",
        enabled=True,
    )
    assert r.id == "cron:1"
    assert r.enabled is True
    assert r.metadata == {}  # default factory


def test_schedule_record_to_dict_shape():
    r = ScheduleRecord(
        id="cron:1", name="Test", owner=OWNER_PRISMATIC,
        schedule_type=TYPE_CRON, schedule_expr="0 * * * *",
        enabled=False,
    )
    d = r.to_dict()
    assert d["id"] == "cron:1"
    assert d["enabled"] is False
    assert d["last_run"] is None
    assert "metadata" in d


def test_schedule_record_to_dict_with_last_run():
    r = ScheduleRecord(
        id="cron:1", name="Test", owner=OWNER_PRISMATIC,
        schedule_type=TYPE_CRON, schedule_expr="0 * * * *",
        enabled=True,
        last_run=LastRunInfo(fired_at="2026-06-24T18:00:00Z", status=STATUS_SUCCESS),
    )
    d = r.to_dict()
    assert d["last_run"]["fired_at"] == "2026-06-24T18:00:00Z"


def test_schedule_record_from_dict_minimal():
    data = {
        "id": "cron:1", "name": "Test", "owner": OWNER_PRISMATIC,
        "schedule_type": TYPE_CRON, "schedule_expr": "0 * * * *",
        "enabled": True,
    }
    r = ScheduleRecord.from_dict(data)
    assert r.id == "cron:1"
    assert r.last_run is None
    assert r.metadata == {}


def test_schedule_record_from_dict_with_last_run():
    data = {
        "id": "cron:1", "name": "Test", "owner": OWNER_PRISMATIC,
        "schedule_type": TYPE_CRON, "schedule_expr": "0 * * * *",
        "enabled": True,
        "last_run": {
            "fired_at": "2026-06-24T18:00:00Z",
            "status": STATUS_FAILED,
            "error_message": "boom",
        },
    }
    r = ScheduleRecord.from_dict(data)
    assert r.last_run is not None
    assert r.last_run.status == STATUS_FAILED
    assert r.last_run.error_message == "boom"


def test_schedule_record_round_trip():
    original = ScheduleRecord(
        id="cron:abc", name="X", owner=OWNER_AGY,
        schedule_type=TYPE_INTERVAL, schedule_expr="every 5m",
        enabled=True,
        next_run_at="2026-06-24T20:00:00Z",
        metadata={"key": "value"},
    )
    d = original.to_dict()
    restored = ScheduleRecord.from_dict(d)
    assert restored.id == original.id
    assert restored.owner == original.owner
    assert restored.schedule_type == original.schedule_type
    assert restored.metadata == original.metadata


# ── ScheduleEvent ───────────────────────────────────────────────────
def test_schedule_event_defaults():
    e = ScheduleEvent(
        event_id="evt-1", event_type="schedule.fired",
        schedule_id="cron:1", owner=OWNER_PRISMATIC,
        timestamp="2026-06-24T18:00:00Z",
    )
    assert e.payload == {}  # default factory
    assert e.event_type == "schedule.fired"


def test_schedule_event_to_dict():
    e = ScheduleEvent(
        event_id="evt-1", event_type="schedule.created",
        schedule_id="cron:1", owner=OWNER_PRISMATIC,
        timestamp="2026-06-24T18:00:00Z",
        payload={"key": "val"},
    )
    d = e.to_dict()
    assert d["event_id"] == "evt-1"
    assert d["payload"] == {"key": "val"}


# ── get_prismatic_cron_jobs ──────────────────────────────────────────
def test_get_prismatic_cron_jobs_returns_empty_for_nonexistent_file(tmp_path):
    """No cron jobs file → empty list (no error)."""
    result = get_prismatic_cron_jobs(cron_jobs_path=tmp_path / "nonexistent.json")
    assert result == []


def test_get_prismatic_cron_jobs_parses_list_format(tmp_path):
    """Cron file with list-of-jobs format."""
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(json.dumps([
        {"id": "job-1", "name": "Test Job", "enabled": True,
         "schedule_display": "0 * * * *", "script": "test.sh",
         "deliver": "local"},
    ]))
    records = get_prismatic_cron_jobs(cron_jobs_path=jobs_file)
    assert len(records) == 1
    assert records[0].id == "prismatic:cron:job-1"
    assert records[0].name == "Test Job"
    assert records[0].owner == OWNER_PRISMATIC
    assert records[0].schedule_type == TYPE_CRON
    assert records[0].enabled is True
    assert records[0].metadata["script"] == "test.sh"


def test_get_prismatic_cron_jobs_parses_dict_format(tmp_path):
    """Cron file with {jobs: [...]} format."""
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(json.dumps({
        "jobs": [
            {"id": "job-1", "name": "Dict Job", "enabled": False,
             "schedule": "every 5m"},
        ],
    }))
    records = get_prismatic_cron_jobs(cron_jobs_path=jobs_file)
    assert len(records) == 1
    assert records[0].name == "Dict Job"
    assert records[0].schedule_expr == "every 5m"
    assert records[0].enabled is False


def test_get_prismatic_cron_jobs_handles_corrupt_file(tmp_path):
    """Corrupt JSON → returns empty list (no crash)."""
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text("not json {{{")
    records = get_prismatic_cron_jobs(cron_jobs_path=jobs_file)
    assert records == []


def test_get_prismatic_cron_jobs_skips_non_dict_entries(tmp_path):
    """Non-dict entries in the jobs list are skipped silently."""
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(json.dumps([
        "not a dict",
        {"id": "real-job", "name": "Real", "enabled": True},
        42,
    ]))
    records = get_prismatic_cron_jobs(cron_jobs_path=jobs_file)
    assert len(records) == 1
    assert records[0].id == "prismatic:cron:real-job"


def test_get_prismatic_cron_jobs_includes_last_run(tmp_path):
    """Jobs with last_run_at → ScheduleRecord.last_run is populated."""
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(json.dumps([
        {
            "id": "job-1", "name": "Job", "enabled": True,
            "schedule_display": "0 * * * *",
            "last_run_at": "2026-06-24T18:00:00Z",
            "last_status": "failed",
            "last_error": "timeout",
        },
    ]))
    records = get_prismatic_cron_jobs(cron_jobs_path=jobs_file)
    assert records[0].last_run is not None
    assert records[0].last_run.status == "failed"
    assert records[0].last_run.error_message == "timeout"


def test_get_prismatic_cron_jobs_id_format(tmp_path):
    """All prismatic cron job IDs have 'prismatic:cron:' prefix."""
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(json.dumps([
        {"id": "a", "name": "A", "enabled": True},
        {"id": "b", "name": "B", "enabled": False},
    ]))
    records = get_prismatic_cron_jobs(cron_jobs_path=jobs_file)
    ids = {r.id for r in records}
    assert ids == {"prismatic:cron:a", "prismatic:cron:b"}


def test_get_prismatic_cron_jobs_default_schedule_when_missing(tmp_path):
    """No schedule field → uses '* * * * *' as fallback."""
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(json.dumps([
        {"id": "job-1", "name": "X", "enabled": True},
    ]))
    records = get_prismatic_cron_jobs(cron_jobs_path=jobs_file)
    assert records[0].schedule_expr == "* * * * *"


def test_get_prismatic_cron_jobs_paused_field_treated_as_disabled(tmp_path):
    """Jobs with paused=True are treated as disabled."""
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(json.dumps([
        {"id": "job-1", "name": "X", "paused": True},
    ]))
    records = get_prismatic_cron_jobs(cron_jobs_path=jobs_file)
    assert records[0].enabled is False


# ── get_systemd_timer_schedules ──────────────────────────────────────
def test_get_systemd_timer_schedules_returns_list():
    """Returns a list (possibly empty if no systemd available)."""
    result = get_systemd_timer_schedules()
    assert isinstance(result, list)


# ── get_all_schedules ────────────────────────────────────────────────
def test_get_all_schedules_returns_combined_list(tmp_path):
    """Combines cron + systemd + AGY + Jules (each may be empty)."""
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(json.dumps([
        {"id": "job-1", "name": "Cron Job", "enabled": True},
    ]))
    result = get_all_schedules(cron_jobs_path=jobs_file)
    assert isinstance(result, list)
    # At least the cron job should be present
    cron_ids = [r.id for r in result if r.owner == OWNER_PRISMATIC]
    assert "prismatic:cron:job-1" in cron_ids


# ── UnauthorizedMutationError ───────────────────────────────────────
def test_unauthorized_mutation_error_is_permission_error():
    """It's a subclass of PermissionError (caught by proper exception handlers)."""
    err = UnauthorizedMutationError("blocked")
    assert isinstance(err, PermissionError)
    assert str(err) == "blocked"


# ── request_schedule_mutation ───────────────────────────────────────
def test_request_schedule_mutation_rejects_unknown_schedule_id(tmp_path):
    """Unknown schedule_id → FileNotFoundError."""
    try:
        request_schedule_mutation(
            schedule_id="prismatic:cron:nonexistent",
            enabled=False,
            config_path=tmp_path,
        )
        assert False, "Should have raised FileNotFoundError"
    except FileNotFoundError as exc:
        assert "nonexistent" in str(exc)


def test_request_schedule_mutation_accepts_prismatic_owner(tmp_path):
    """Prismatic-owned schedules are mutated (mocked)."""
    # Create a real cron jobs file with a job
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(json.dumps([
        {"id": "test-job", "name": "Test", "enabled": True,
         "schedule_display": "0 * * * *", "script": "test.sh"},
    ]))
    with patch("prismatic.schedules._mutate_local_cron_job") as mock_mutate:
        mock_mutate.return_value = {"ok": True, "job_id": "test-job"}
        result = request_schedule_mutation(
            schedule_id="prismatic:cron:test-job",
            enabled=False,
            config_path=jobs_file,
        )
    assert isinstance(result, dict)
    assert result["ok"] is True
    # The actual mutation function was called
    mock_mutate.assert_called_once()


def test_request_schedule_mutation_rejects_agy_owner(tmp_path):
    """AGY-owned schedules can't be mutated directly (raises error)."""
    from prismatic import schedules as sched_mod
    fake_schedule = ScheduleRecord(
        id="agy:some-job", name="AGY Job", owner=OWNER_AGY,
        schedule_type=TYPE_REMOTE, schedule_expr="managed by AGY",
        enabled=True,
    )
    with patch.object(sched_mod, "get_all_schedules", return_value=[fake_schedule]):
        try:
            result = request_schedule_mutation(
                schedule_id="agy:some-job",
                enabled=False,
                config_path=tmp_path,
            )
            # If it returns, the result should indicate failure
            assert result.get("ok") is False
        except UnauthorizedMutationError as exc:
            # Acceptable: error message mentions AGY + suggestion
            assert "agy" in str(exc).lower()


def test_request_schedule_mutation_rejects_jules_owner(tmp_path):
    """Jules-owned schedules are completely read-only."""
    from prismatic import schedules as sched_mod
    fake_schedule = ScheduleRecord(
        id="jules:some-job", name="Jules Job", owner=OWNER_JULES,
        schedule_type=TYPE_REMOTE, schedule_expr="managed by Jules",
        enabled=True,
    )
    with patch.object(sched_mod, "get_all_schedules", return_value=[fake_schedule]):
        try:
            result = request_schedule_mutation(
                schedule_id="jules:some-job",
                enabled=False,
                config_path=tmp_path,
            )
            # If it returns, the result should indicate failure
            assert result.get("ok") is False
        except UnauthorizedMutationError:
            # Acceptable to raise
            pass


def test_request_schedule_mutation_systemd_schedule_rejected(tmp_path):
    """systemd-timer schedules are restricted (require sudo, raise error)."""
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(json.dumps([
        {"id": "timer-job", "name": "Timer", "enabled": True,
         "schedule_display": "OnCalendar=daily"},
    ]))
    # The schedule we create will be of type TYPE_CRON by default.
    # We need a systemd-timer schedule. Patch get_all_schedules.
    from prismatic import schedules as sched_mod
    fake_schedule = ScheduleRecord(
        id="prismatic:systemd-timer:my-timer",
        name="My Timer", owner=OWNER_PRISMATIC,
        schedule_type=TYPE_SYSTEMD, schedule_expr="OnCalendar=daily",
        enabled=True,
    )
    with patch.object(sched_mod, "get_all_schedules", return_value=[fake_schedule]):
        try:
            request_schedule_mutation(
                schedule_id="prismatic:systemd-timer:my-timer",
                enabled=False,
                config_path=tmp_path,
            )
            assert False, "Should have rejected systemd mutation"
        except UnauthorizedMutationError as exc:
            assert "systemd" in str(exc).lower() or "safety" in str(exc).lower()


# ── Integration: realistic cron file ───────────────────────────────
def test_realistic_cron_file_round_trip(tmp_path):
    """A realistic cron jobs.json → ScheduleRecord → to_dict round-trip."""
    jobs_file = tmp_path / "jobs.json"
    realistic = [
        {
            "id": "fleet-watcher-5m",
            "name": "Fleet Watcher (every 5m)",
            "enabled": True,
            "schedule_display": "every 5m",
            "script": "fleet_watch.py",
            "deliver": "telegram:123",
            "last_run_at": "2026-06-24T18:00:00Z",
            "last_status": "success",
        },
        {
            "id": "nightly-cleanup",
            "name": "Nightly Cleanup",
            "enabled": False,
            "schedule_display": "0 3 * * *",
            "script": "cleanup.sh",
            "paused": True,
        },
    ]
    jobs_file.write_text(json.dumps(realistic))

    records = get_prismatic_cron_jobs(cron_jobs_path=jobs_file)
    assert len(records) == 2

    # Active job
    active = records[0]
    assert active.id == "prismatic:cron:fleet-watcher-5m"
    assert active.enabled is True
    assert active.last_run is not None
    assert active.last_run.status == STATUS_SUCCESS

    # Paused job
    paused = records[1]
    assert paused.id == "prismatic:cron:nightly-cleanup"
    assert paused.enabled is False

    # Round-trip via dict
    for r in records:
        d = r.to_dict()
        restored = ScheduleRecord.from_dict(d)
        assert restored.id == r.id
        assert restored.enabled == r.enabled
