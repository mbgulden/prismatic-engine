from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from prismatic.gateway import control_auth

TOKENS = {
    "operator": "operator-secret-value",
    "approver": "approver-secret-value",
    "executor": "executor-secret-value",
    "combined": "combined-secret-value",
}


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _write_credentials(
    path: Path,
    records: list[dict[str, object]] | None = None,
    *,
    mode: int = 0o600,
) -> Path:
    records = records or [
        {"actor": role, "token_sha256": _digest(token), "roles": [role]}
        for role, token in TOKENS.items()
        if role != "combined"
    ]
    path.write_text(json.dumps({"version": 1, "credentials": records}))
    path.chmod(mode)
    return path


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    credentials = _write_credentials(tmp_path / "control-auth.json")
    monkeypatch.setenv("PRISMATIC_CONTROL_AUTH_FILE", str(credentials))
    app = FastAPI()
    app.middleware("http")(control_auth.control_authorization_middleware)

    @app.api_route("/read", methods=["GET", "HEAD", "OPTIONS"])
    async def read() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/ordinary")
    async def ordinary(request: Request) -> dict[str, object]:
        return {
            "actor": request.state.control_actor,
            "roles": sorted(request.state.control_roles),
            "body": await request.json(),
        }

    @app.post("/items/{item_id}/approve")
    async def approve(item_id: str) -> dict[str, str]:
        return {"item": item_id}

    @app.post("/items/{item_id}/reject")
    async def reject(item_id: str) -> dict[str, str]:
        return {"item": item_id}

    @app.post("/api/agy/actions/final-authorization")
    async def final_authorization() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/agy/things/real-executor-arming")
    async def arm_executor() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/agy/merge-backlog/item/pr-executor")
    async def invoke_executor() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/agy/merge-backlog/item/pr-approval")
    async def approve_pr() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/agy/merge-backlog/item/pr-create-approved")
    async def create_approved_pr_action() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/agy/promotion-decisions/item/operator-action")
    async def record_operator_action_approval() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/native-crons/{cron_id}/action")
    async def native_cron(cron_id: str, request: Request) -> dict[str, object]:
        return {"cron": cron_id, "body": await request.json()}

    @app.post("/webhooks/provider")
    async def webhook() -> dict[str, bool]:
        return {"provider_signature_boundary": True}

    return TestClient(app)


def _auth(role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[role]}"}


def test_reads_remain_available_without_token(client: TestClient) -> None:
    assert client.get("/read").status_code == 200
    assert client.head("/read").status_code == 200
    assert client.options("/read").status_code == 200


def test_webhook_post_bypasses_only_control_middleware(client: TestClient) -> None:
    response = client.post("/webhooks/provider")
    assert response.status_code == 200
    assert "X-Prismatic-Control-Authorization" not in response.headers


def test_all_other_mutations_and_unknown_methods_fail_closed_without_config(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PRISMATIC_CONTROL_AUTH_FILE")
    for method in ("POST", "PUT", "PATCH", "DELETE", "BREW"):
        response = client.request(method, "/unknown")
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "case",
    [
        "relative",
        "missing",
        "symlink",
        "nonregular",
        "permissive",
        "malformed",
        "unknown",
    ],
)
def test_invalid_credential_files_deny(
    case: str,
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / f"{case}.json"
    if case == "relative":
        configured = "relative.json"
    elif case == "missing":
        configured = str(path)
    elif case == "symlink":
        target = _write_credentials(tmp_path / "target.json")
        path.symlink_to(target)
        configured = str(path)
    elif case == "nonregular":
        path.mkdir()
        configured = str(path)
    elif case == "permissive":
        configured = str(_write_credentials(path, mode=0o640))
    elif case == "malformed":
        path.write_text("{not-json")
        path.chmod(0o600)
        configured = str(path)
    else:
        path.write_text(json.dumps({"version": 1, "credentials": [], "extra": True}))
        path.chmod(0o600)
        configured = str(path)
    monkeypatch.setenv("PRISMATIC_CONTROL_AUTH_FILE", configured)
    assert client.post("/ordinary", json={}).status_code == 401


@pytest.mark.parametrize("version", [True, 1.0])
def test_credential_schema_version_requires_exact_integer(
    version: object,
    client: TestClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_credentials(tmp_path / "invalid-version.json")
    document = json.loads(path.read_text())
    document["version"] = version
    path.write_text(json.dumps(document))
    path.chmod(0o600)
    monkeypatch.setenv("PRISMATIC_CONTROL_AUTH_FILE", str(path))

    response = client.post(
        "/ordinary", headers=_auth("operator"), json={"must_not_run": True}
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_duplicate_digest_and_empty_roles_deny(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = {"actor": "a", "token_sha256": _digest("same"), "roles": ["operator"]}
    path = _write_credentials(tmp_path / "duplicates.json", [record, record])
    monkeypatch.setenv("PRISMATIC_CONTROL_AUTH_FILE", str(path))
    assert (
        client.post(
            "/ordinary", headers={"Authorization": "Bearer same"}, json={}
        ).status_code
        == 401
    )

    record["roles"] = []
    path = _write_credentials(tmp_path / "empty.json", [record])
    monkeypatch.setenv("PRISMATIC_CONTROL_AUTH_FILE", str(path))
    assert (
        client.post(
            "/ordinary", headers={"Authorization": "Bearer same"}, json={}
        ).status_code
        == 401
    )


def test_invalid_bearer_is_secret_safe(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    token = "do-not-leak-this-token"
    response = client.post(
        "/ordinary", headers={"Authorization": f"Bearer {token}"}, json={}
    )
    evidence = response.text + repr(response.headers) + caplog.text
    assert response.status_code == 401
    assert token not in evidence
    assert _digest(token) not in evidence


def test_operator_only_permits_ordinary_mutation(client: TestClient) -> None:
    response = client.post(
        "/ordinary", headers=_auth("operator"), json={"preserved": True}
    )
    assert response.status_code == 200
    assert response.json() == {
        "actor": "operator",
        "roles": ["operator"],
        "body": {"preserved": True},
    }
    assert response.headers["X-Prismatic-Control-Authorization"] == "authorized"
    assert response.headers["X-Prismatic-Control-Role"] == "operator"
    assert client.post("/items/1/approve", headers=_auth("operator")).status_code == 403
    for protected_path in (
        "/api/agy/merge-backlog/item/pr-approval",
        "/api/agy/merge-backlog/item/pr-create-approved",
        "/api/agy/promotion-decisions/item/operator-action",
    ):
        assert client.post(protected_path, headers=_auth("operator")).status_code == 403
    assert (
        client.post(
            "/api/agy/things/real-executor-arming", headers=_auth("operator")
        ).status_code
        == 403
    )


def test_approver_and_executor_roles_do_not_imply_each_other_or_operator(
    client: TestClient,
) -> None:
    assert client.post("/items/1/approve", headers=_auth("approver")).status_code == 200
    assert client.post("/items/1/reject", headers=_auth("approver")).status_code == 200
    for protected_path in (
        "/api/agy/merge-backlog/item/pr-approval",
        "/api/agy/merge-backlog/item/pr-create-approved",
        "/api/agy/promotion-decisions/item/operator-action",
    ):
        assert client.post(protected_path, headers=_auth("approver")).status_code == 200
    assert (
        client.post(
            "/api/agy/actions/final-authorization", headers=_auth("approver")
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/agy/things/real-executor-arming", headers=_auth("approver")
        ).status_code
        == 403
    )
    assert (
        client.post("/ordinary", headers=_auth("approver"), json={}).status_code == 403
    )

    assert (
        client.post(
            "/api/agy/things/real-executor-arming", headers=_auth("executor")
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/agy/merge-backlog/item/pr-executor", headers=_auth("executor")
        ).status_code
        == 200
    )
    assert client.post("/items/1/approve", headers=_auth("executor")).status_code == 403
    assert (
        client.post("/ordinary", headers=_auth("executor"), json={}).status_code == 403
    )


def test_explicit_multiple_roles_are_honored(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_credentials(
        tmp_path / "combined.json",
        [
            {
                "actor": "combined",
                "token_sha256": _digest(TOKENS["combined"]),
                "roles": ["approver", "executor"],
            }
        ],
    )
    monkeypatch.setenv("PRISMATIC_CONTROL_AUTH_FILE", str(path))
    headers = _auth("combined")
    assert client.post("/items/1/approve", headers=headers).status_code == 200
    assert (
        client.post("/api/agy/things/real-executor-arming", headers=headers).status_code
        == 200
    )
    assert client.post("/ordinary", headers=headers, json={}).status_code == 403


def test_native_cron_run_requires_executor_and_body_is_preserved(
    client: TestClient,
) -> None:
    payload = {"action": "run", "evidence": "still here"}
    assert (
        client.post(
            "/native-crons/one/action", headers=_auth("operator"), json=payload
        ).status_code
        == 403
    )
    response = client.post(
        "/native-crons/one/action", headers=_auth("executor"), json=payload
    )
    assert response.status_code == 200
    assert response.json()["body"] == payload
    assert response.headers["X-Prismatic-Control-Role"] == "executor"
    assert (
        client.post(
            "/native-crons/one/action",
            headers=_auth("operator"),
            json={"action": "pause"},
        ).status_code
        == 200
    )


def test_cookie_query_and_body_credentials_never_authenticate(
    client: TestClient,
) -> None:
    token = TOKENS["operator"]
    client.cookies.set("Authorization", f"Bearer {token}")
    assert (
        client.post(f"/ordinary?token={token}", json={"token": token}).status_code
        == 401
    )


def test_import_has_no_credential_file_side_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = tmp_path / "must-not-be-read.json"
    sentinel.write_text("not credentials")
    sentinel.chmod(0)
    before = sentinel.stat()
    monkeypatch.setenv("PRISMATIC_CONTROL_AUTH_FILE", str(sentinel))
    importlib.reload(control_auth)
    after = sentinel.stat()
    assert (after.st_mode, after.st_mtime_ns, after.st_size) == (
        before.st_mode,
        before.st_mtime_ns,
        before.st_size,
    )
    sentinel.chmod(0o600)


def test_gateway_app_installs_central_fail_closed_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prismatic.gateway.server import app

    path = _write_credentials(tmp_path / "gateway-control-auth.json")
    monkeypatch.setenv("PRISMATIC_CONTROL_AUTH_FILE", str(path))
    gateway = TestClient(app)

    assert gateway.get("/definitely-unknown").status_code == 404
    assert gateway.post("/definitely-unknown").status_code == 401
    authorized = gateway.post("/definitely-unknown", headers=_auth("operator"))
    assert authorized.status_code == 404
    assert authorized.headers["X-Prismatic-Control-Role"] == "operator"
    assert gateway.post("/webhooks/definitely-unknown").status_code == 404


def test_success_headers_never_expose_actor_token_or_digest(client: TestClient) -> None:
    response = client.post("/ordinary", headers=_auth("operator"), json={})
    header_evidence = repr(response.headers)
    assert "operator-secret-value" not in header_evidence
    assert _digest(TOKENS["operator"]) not in header_evidence
    assert "actor" not in header_evidence.lower()
