from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prismatic.gateway import server

OPERATOR_TOKEN = "test-operator-token"
APPROVER_TOKEN = "test-approver-token"
KEY = "admission:GRO-4210:0123456789abcdef"


def _run(*args: str, cwd: Path) -> str:
    return subprocess.run(
        list(args), cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def api_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    _run("git", "init", "-q", cwd=worktree)
    _run("git", "config", "user.email", "test@example.invalid", cwd=worktree)
    _run("git", "config", "user.name", "Test", cwd=worktree)
    task = worktree / "tasks" / "GRO-4210" / "TASK.md"
    task.parent.mkdir(parents=True)
    task.write_text("bounded fixture task\n")
    _run("git", "add", ".", cwd=worktree)
    _run("git", "commit", "-qm", "fixture", cwd=worktree)
    commit = _run("git", "rev-parse", "HEAD", cwd=worktree)
    tree = _run("git", "rev-parse", "HEAD^{tree}", cwd=worktree)

    policy = tmp_path / "admission-policy.json"
    policy.write_text(
        json.dumps(
            {
                "worktrees": [str(worktree.resolve())],
                "producers": ["agy-pnv6"],
                "max_age_seconds": 300,
            }
        )
    )
    policy.chmod(0o600)
    credentials = tmp_path / "control-auth.json"
    credentials.write_text(
        json.dumps(
            {
                "version": 1,
                "credentials": [
                    {
                        "actor": "michael",
                        "token_sha256": hashlib.sha256(
                            OPERATOR_TOKEN.encode()
                        ).hexdigest(),
                        "roles": ["operator"],
                    },
                    {
                        "actor": "reviewer",
                        "token_sha256": hashlib.sha256(
                            APPROVER_TOKEN.encode()
                        ).hexdigest(),
                        "roles": ["approver"],
                    },
                ],
            }
        )
    )
    credentials.chmod(0o600)
    monkeypatch.setenv("PRISMATIC_CONTROL_AUTH_FILE", str(credentials))
    monkeypatch.setenv("PRISMATIC_TASK_ADMISSION_POLICY_FILE", str(policy))
    monkeypatch.setenv("PRISMATIC_BUS_DB", str(tmp_path / "bus.sqlite"))

    payload = {
        "version": 1,
        "task_id": "GRO-4210",
        "base_commit": commit,
        "base_tree": tree,
        "task_file": "tasks/GRO-4210/TASK.md",
        "task_file_sha256": hashlib.sha256(task.read_bytes()).hexdigest(),
        "producer_identity": "agy-pnv6",
        "worktree": str(worktree.resolve()),
        "writer_cap": 1,
        "idempotency_key": KEY,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "admitted",
    }
    with TestClient(server.app) as client:
        yield client, payload, tmp_path


def _headers(token: str = OPERATOR_TOKEN) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": KEY,
        "Content-Type": "application/json",
    }


def test_admission_and_readback_require_operator(api_fixture) -> None:
    client, payload, _ = api_fixture
    assert (
        client.post("/api/dashboard/task-admissions", json=payload).status_code == 401
    )
    assert client.get("/api/dashboard/task-admissions").status_code == 401
    assert (
        client.get(
            "/api/dashboard/task-admissions",
            headers={"Authorization": f"Bearer {APPROVER_TOKEN}"},
        ).status_code
        == 403
    )


def test_create_replay_and_authenticated_readback(api_fixture) -> None:
    client, payload, _ = api_fixture
    response = client.post(
        "/api/dashboard/task-admissions",
        content=json.dumps(payload),
        headers=_headers(),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["ok"] is True
    assert body["replayed"] is False
    assert body["launch_performed"] is False
    assert body["record"]["actor"] == "michael"
    assert OPERATOR_TOKEN not in response.text

    replay = client.post(
        "/api/dashboard/task-admissions",
        content=json.dumps(payload),
        headers=_headers(),
    )
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["record"] == body["record"]

    auth = {"Authorization": f"Bearer {OPERATOR_TOKEN}"}
    listed = client.get("/api/dashboard/task-admissions", headers=auth)
    single = client.get("/api/dashboard/task-admissions/GRO-4210", headers=auth)
    assert listed.status_code == 200 and listed.json()["count"] == 1
    assert single.status_code == 200 and single.json()["record"] == body["record"]


def test_content_type_duplicate_keys_and_header_mismatch_fail(api_fixture) -> None:
    client, payload, _ = api_fixture
    auth = {"Authorization": f"Bearer {OPERATOR_TOKEN}", "Idempotency-Key": KEY}
    response = client.post(
        "/api/dashboard/task-admissions", content=json.dumps(payload), headers=auth
    )
    assert response.status_code == 415

    raw = json.dumps(payload)[:-1] + ',"task_id":"GRO-9999"}'
    response = client.post(
        "/api/dashboard/task-admissions", content=raw, headers=_headers()
    )
    assert response.status_code == 422
    assert response.json()["error"] == "duplicate_json_key"

    headers = _headers()
    headers["Idempotency-Key"] = "different-key-0123456789abcdef00"
    response = client.post(
        "/api/dashboard/task-admissions", content=json.dumps(payload), headers=headers
    )
    assert response.status_code == 422
    assert response.json()["error"] == "idempotency_key_mismatch"


def test_storage_and_response_do_not_contain_bearer(api_fixture) -> None:
    client, payload, tmp_path = api_fixture
    response = client.post(
        "/api/dashboard/task-admissions",
        content=json.dumps(payload),
        headers=_headers(),
    )
    assert response.status_code == 201
    database = (tmp_path / "bus.sqlite").read_bytes()
    assert OPERATOR_TOKEN.encode() not in database
    assert b"Authorization" not in database
    assert b"bounded fixture task" not in database


def test_endpoint_reports_idempotency_and_task_conflicts(api_fixture) -> None:
    client, payload, _ = api_fixture
    assert (
        client.post(
            "/api/dashboard/task-admissions",
            content=json.dumps(payload),
            headers=_headers(),
        ).status_code
        == 201
    )
    created = datetime.strptime(payload["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    changed = {
        **payload,
        "created_at": (created + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    conflict = client.post(
        "/api/dashboard/task-admissions",
        content=json.dumps(changed),
        headers=_headers(),
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "idempotency_conflict"

    other_key = "admission:GRO-4210:fedcba9876543210"
    changed = {**payload, "idempotency_key": other_key}
    headers = _headers()
    headers["Idempotency-Key"] = other_key
    conflict = client.post(
        "/api/dashboard/task-admissions",
        content=json.dumps(changed),
        headers=headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "task_already_admitted"


def test_dirty_tracked_worktree_fails_closed(api_fixture) -> None:
    client, payload, tmp_path = api_fixture
    task = tmp_path / "worktree" / payload["task_file"]
    task.write_text("mutated tracked task\n")
    payload["task_file_sha256"] = hashlib.sha256(task.read_bytes()).hexdigest()
    response = client.post(
        "/api/dashboard/task-admissions",
        content=json.dumps(payload),
        headers=_headers(),
    )
    assert response.status_code == 422
    assert response.json()["error"] == "worktree_dirty"


def test_readback_survives_missing_policy_after_admission(
    api_fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, payload, _ = api_fixture
    created = client.post(
        "/api/dashboard/task-admissions",
        content=json.dumps(payload),
        headers=_headers(),
    )
    assert created.status_code == 201
    monkeypatch.delenv("PRISMATIC_TASK_ADMISSION_POLICY_FILE")
    auth = {"Authorization": f"Bearer {OPERATOR_TOKEN}"}
    assert client.get("/api/dashboard/task-admissions", headers=auth).status_code == 200
    assert (
        client.get("/api/dashboard/task-admissions/GRO-4210", headers=auth).status_code
        == 200
    )


def test_missing_policy_fails_closed(
    api_fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, payload, _ = api_fixture
    monkeypatch.delenv("PRISMATIC_TASK_ADMISSION_POLICY_FILE")
    response = client.post(
        "/api/dashboard/task-admissions",
        content=json.dumps(payload),
        headers=_headers(),
    )
    assert response.status_code == 503
    assert response.json()["error"] == "admission_policy_unavailable"
