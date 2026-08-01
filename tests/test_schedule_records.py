from __future__ import annotations

import json
import pytest
from pathlib import Path
from prismatic.schedules import (
    get_prismatic_cron_jobs,
    get_systemd_timer_schedules,
    get_agy_schedules,
    get_jules_schedules,
    get_all_schedules,
    request_schedule_mutation,
    process_chat_schedule_request,
    UnauthorizedMutationError,
    OWNER_PRISMATIC,
    OWNER_AGY,
    OWNER_JULES,
)


def test_cron_jobs_inventory(tmp_path: Path) -> None:
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(json.dumps([
        {
            "id": "backup",
            "name": "Database Backup",
            "enabled": True,
            "schedule": "0 0 * * *",
            "script": "backup.py",
        },
        {
            "id": "cleanup",
            "name": "Tmp Cleanup",
            "paused": True,
            "schedule": "0 * * * *",
            "script": "cleanup.py",
        }
    ]))

    records = get_prismatic_cron_jobs(jobs_file)
    assert len(records) == 2
    
    backup = next(r for r in records if "backup" in r.id)
    assert backup.name == "Database Backup"
    assert backup.enabled is True
    assert backup.owner == OWNER_PRISMATIC
    assert backup.schedule_expr == "0 0 * * *"

    cleanup = next(r for r in records if "cleanup" in r.id)
    assert cleanup.enabled is False


def test_systemd_timers() -> None:
    timers = get_systemd_timer_schedules()
    assert len(timers) > 0
    assert any("watchdog" in t.id for t in timers)
    assert timers[0].owner == OWNER_PRISMATIC


def test_remote_adapters() -> None:
    agy = get_agy_schedules()
    assert len(agy) > 0
    assert all(a.owner == OWNER_AGY for a in agy)

    jules = get_jules_schedules()
    assert len(jules) > 0
    assert all(j.owner == OWNER_JULES for j in jules)
    assert jules[0].deep_link is not None


def test_mutation_policy_direct_cron(tmp_path: Path) -> None:
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(json.dumps([
        {
            "id": "backup",
            "name": "Database Backup",
            "enabled": True,
            "schedule": "0 0 * * *",
            "script": "backup.py",
        }
    ]))

    # Modify environmental variable temporarily to point to our test jobs file
    # for the request_schedule_mutation internal logic
    res = request_schedule_mutation(
        schedule_id="prismatic:cron:backup",
        enabled=False,
        schedule_expr="30 1 * * *",
        config_path=jobs_file,
    )
    assert res["status"] == "success"
    assert res["schedule"]["enabled"] is False
    assert res["schedule"]["schedule"] == "30 1 * * *"

    # Verify write back to file
    updated_jobs = json.loads(jobs_file.read_text())
    assert updated_jobs[0]["enabled"] is False
    assert updated_jobs[0]["schedule"] == "30 1 * * *"


def test_mutation_policy_unauthorized() -> None:
    # Attempting to mutate systemd should raise UnauthorizedMutationError
    with pytest.raises(UnauthorizedMutationError) as exc_info:
        request_schedule_mutation(schedule_id="prismatic:systemd:prismatic-watchdog", enabled=False)
    assert "systemd timer" in str(exc_info.value)

    # Attempting to mutate AGY schedule should raise UnauthorizedMutationError and suggest command
    with pytest.raises(UnauthorizedMutationError) as exc_info:
        request_schedule_mutation(schedule_id="agy:schedule:daily-repo-sync", enabled=False)
    assert "ask AGY to update" in str(exc_info.value)

    # Attempting to mutate Jules schedule should raise UnauthorizedMutationError
    with pytest.raises(UnauthorizedMutationError) as exc_info:
        request_schedule_mutation(schedule_id="jules:schedule:dependency-scan", enabled=False)
    assert "Jules schedule" in str(exc_info.value)


def test_process_chat_command() -> None:
    msg1 = "ask AGY to update schedule agy:schedule:daily-repo-sync to disabled"
    res1 = process_chat_schedule_request(msg1)
    assert res1["success"] is True
    assert res1["schedule_id"] == "agy:schedule:daily-repo-sync"
    assert res1["updates"]["enabled"] is False

    msg2 = "unrelated message about fixing code"
    res2 = process_chat_schedule_request(msg2)
    assert res2["success"] is False


def test_gateway_endpoints() -> None:
    from fastapi.testclient import TestClient
    from prismatic.gateway.server import app
    
    client = TestClient(app)
    
    # 1. Test GET /schedules
    response = client.get("/schedules")
    assert response.status_code == 200
    schedules = response.json()
    assert len(schedules) > 0
    # Must contain our mocked systemd, agy, and jules schedules
    owners = {s["owner"] for s in schedules}
    assert "prismatic" in owners
    assert "agy" in owners
    assert "jules" in owners

    # 2. Test POST /schedules/chat-command
    response = client.post(
        "/schedules/chat-command",
        json={"message": "ask AGY to update schedule agy:schedule:daily-repo-sync to disabled"}
    )
    assert response.status_code == 200
    res = response.json()
    assert res["success"] is True
    assert res["schedule_id"] == "agy:schedule:daily-repo-sync"
    assert res["updates"]["enabled"] is False

    # 3. Test POST /schedules/agy:schedule:daily-repo-sync/mutate (restricted)
    response = client.post(
        "/schedules/agy:schedule:daily-repo-sync/mutate",
        json={"enabled": False}
    )
    assert response.status_code == 403
    assert "ask AGY to update" in response.json()["error"]

    # 4. Test POST /schedules/jules:schedule:dependency-scan/mutate (read-only)
    response = client.post(
        "/schedules/jules:schedule:dependency-scan/mutate",
        json={"enabled": False}
    )
    assert response.status_code == 403
    assert "read-only" in response.json()["error"]

