"""Portal Phase 1 (P0 #5): API token mint/use/revoke/list + scope enforcement.

Also covers the P0 #7 integration surface: provider identity -> role mapping
through the control-auth middleware, and the legacy behavior guarantee (no
instance.json / no token DB => today's credential-file behavior, unchanged).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from pe.deploy import instance as deploy_instance
from prismatic.gateway import control_auth, token_routes
from prismatic.gateway.token_store import TOKEN_ROLES, TokenStore

ADMIN_EMAIL = "michael@example.com"
OPERATOR_SECRET = "operator-secret-value"
APPROVER_SECRET = "approver-secret-value"


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _write_credentials(path: Path, mode: int = 0o600) -> Path:
    records = [
        {"actor": "operator", "token_sha256": _digest(OPERATOR_SECRET), "roles": ["operator"]},
        {"actor": "approver", "token_sha256": _digest(APPROVER_SECRET), "roles": ["approver"]},
    ]
    path.write_text(json.dumps({"version": 1, "credentials": records}))
    path.chmod(mode)
    return path


def _make_app() -> FastAPI:
    app = FastAPI()
    app.middleware("http")(control_auth.control_authorization_middleware)
    app.include_router(token_routes.router)

    @app.get("/read")
    async def read() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/ordinary")
    async def ordinary(request: Request) -> dict[str, object]:
        return {
            "actor": request.state.control_actor,
            "portal_roles": sorted(request.state.portal_roles),
        }

    @app.post("/items/{item_id}/approve")
    async def approve(item_id: str) -> dict[str, str]:
        return {"item": item_id}

    return app


@pytest.fixture
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(state))
    return state


@pytest.fixture
def client(state_dir: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    creds = _write_credentials(state_dir / "control-auth.json")
    monkeypatch.setenv("PRISMATIC_CONTROL_AUTH_FILE", str(creds))
    config = deploy_instance.ensure(state_dir)
    config.auth_provider = "cloudflare-access"
    config.identity_roles[ADMIN_EMAIL] = "admin"
    deploy_instance.save(config, state_dir)
    return TestClient(_make_app(), raise_server_exceptions=False)


def _admin_headers() -> dict[str, str]:
    return {"cf-access-authenticated-user-email": ADMIN_EMAIL}


def _mint(client: TestClient, name: str = "ci", role: str = "operator") -> dict:
    response = client.post(
        "/api/tokens",
        json={"name": name, "role": role},
        headers=_admin_headers(),
    )
    assert response.status_code == 201, response.text
    return response.json()


# --- lifecycle ---


def test_mint_returns_secret_once_and_lists_metadata(client: TestClient):
    body = _mint(client, name="ci-runner", role="operator")
    assert body["token"].startswith("pt_")
    assert body["name"] == "ci-runner"
    assert body["role"] == "operator"
    assert "secret_sha256" not in body
    assert "secret_prefix" in body

    listed = client.get("/api/tokens", headers=_admin_headers()).json()["tokens"]
    assert len(listed) == 1
    assert "token" not in listed[0]
    assert "secret_sha256" not in listed[0]
    assert listed[0]["secret_prefix"] == body["token"][:12]
    assert listed[0]["revoked"] is False


def test_operator_token_passes_control_endpoint(client: TestClient):
    token = _mint(client, role="operator")["token"]
    response = client.post(
        "/ordinary", headers={"authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["portal_roles"] == ["operator"]
    assert response.json()["actor"] == "portal-token:ci"


def test_viewer_token_cannot_hit_operator_endpoint(client: TestClient):
    token = _mint(client, role="viewer")["token"]
    response = client.post("/ordinary", headers={"authorization": f"Bearer {token}"})
    assert response.status_code == 403
    # Read-only routes still pass (no role required).
    assert client.get("/read", headers={"authorization": f"Bearer {token}"}).status_code == 200


def test_revoke_kills_token(client: TestClient):
    body = _mint(client)
    token, token_id = body["token"], body["id"]
    assert client.post("/ordinary", headers={"authorization": f"Bearer {token}"}).status_code == 200

    revoke = client.delete(f"/api/tokens/{token_id}", headers=_admin_headers())
    assert revoke.status_code == 200
    assert revoke.json()["token"]["revoked"] is True

    assert client.post("/ordinary", headers={"authorization": f"Bearer {token}"}).status_code == 401


def test_revoke_unknown_token_is_404(client: TestClient):
    response = client.delete("/api/tokens/does-not-exist", headers=_admin_headers())
    assert response.status_code == 404


def test_token_management_requires_admin(client: TestClient):
    operator_token = _mint(client, role="operator")["token"]
    headers = {"authorization": f"Bearer {operator_token}"}
    # Operator tokens can never mint tokens.
    assert client.post("/api/tokens", json={"name": "x", "role": "viewer"}, headers=headers).status_code == 403
    assert client.get("/api/tokens", headers=headers).status_code == 403
    assert client.delete("/api/tokens/abc", headers=headers).status_code == 403
    # Unmapped identities get nothing.
    assert client.get("/api/tokens").status_code == 401
    assert client.post("/api/tokens", json={"name": "x", "role": "viewer"}).status_code == 401


def test_mint_rejects_bad_role(client: TestClient):
    response = client.post(
        "/api/tokens",
        json={"name": "x", "role": "admin"},
        headers=_admin_headers(),
    )
    assert response.status_code == 422


def test_no_plaintext_secrets_in_store(client: TestClient, state_dir: Path):
    body = _mint(client)
    # SQLite WAL mode can hold rows in the -wal/-shm sidecars: scan them too.
    raw = b"".join(
        (state_dir / "db" / name).read_bytes()
        for name in ("portal_tokens.db", "portal_tokens.db-wal", "portal_tokens.db-shm")
        if (state_dir / "db" / name).exists()
    )
    assert body["token"].encode() not in raw
    assert hashlib.sha256(body["token"].encode()).hexdigest().encode() in raw


def test_expired_token_rejected(client: TestClient, state_dir: Path):
    body = _mint(client)
    token = body["token"]
    config = deploy_instance.load(state_dir)
    assert config is not None
    store = TokenStore.for_state_dir(state_dir, config.instance_id)
    # Backdate the expiry directly (simulates passage of time).
    import sqlite3

    db = state_dir / "db" / "portal_tokens.db"
    connection = sqlite3.connect(str(db))
    connection.execute(
        "UPDATE api_tokens SET expires_at = ? WHERE id = ?",
        ("2000-01-01T00:00:00+00:00", body["id"]),
    )
    connection.commit()
    connection.close()
    assert store.verify(token) is None
    assert client.post("/ordinary", headers={"authorization": f"Bearer {token}"}).status_code == 401


def test_token_bound_to_minting_instance(state_dir: Path):
    config = deploy_instance.ensure(state_dir)
    store_a = TokenStore.for_state_dir(state_dir, config.instance_id)
    _, secret = store_a.mint("a", "viewer")
    store_b = TokenStore.for_state_dir(state_dir, "other-instance-id")
    assert store_b.verify(secret) is None
    assert store_a.verify(secret) is not None


def test_provider_identity_admin_can_mint_but_unmapped_cannot(client: TestClient):
    # Unmapped Cloudflare identity: authenticated as nobody -> 401 on admin API.
    response = client.get(
        "/api/tokens", headers={"cf-access-authenticated-user-email": "stranger@example.com"}
    )
    assert response.status_code == 401


# --- legacy behavior guarantee (P0 #7 must not change today's posture) ---


def test_legacy_behavior_unchanged_without_instance_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """No instance.json and no token DB: credential-file behavior is identical."""
    state = tmp_path / "legacy"
    state.mkdir()
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(state))
    creds = _write_credentials(state / "control-auth.json")
    monkeypatch.setenv("PRISMATIC_CONTROL_AUTH_FILE", str(creds))
    assert not (state / "instance.json").exists()

    app = FastAPI()
    app.middleware("http")(control_auth.control_authorization_middleware)

    @app.post("/ordinary")
    async def ordinary() -> dict[str, bool]:
        return {"ok": True}

    test_client = TestClient(app, raise_server_exceptions=False)
    # Valid file credential still works.
    ok = test_client.post(
        "/ordinary", headers={"authorization": f"Bearer {OPERATOR_SECRET}"}
    )
    assert ok.status_code == 200
    # Missing credential still 401s with the Bearer challenge.
    denied = test_client.post("/ordinary")
    assert denied.status_code == 401
    assert denied.headers["www-authenticate"] == "Bearer"
    # Wrong bearer still 401s.
    assert (
        test_client.post("/ordinary", headers={"authorization": "Bearer nope"}).status_code
        == 401
    )


def test_unknown_provider_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state = tmp_path / "bad"
    state.mkdir()
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(state))
    creds = _write_credentials(state / "control-auth.json")
    monkeypatch.setenv("PRISMATIC_CONTROL_AUTH_FILE", str(creds))
    (state / "instance.json").write_text(
        json.dumps(
            {
                "version": 1,
                "instance_id": "12345678123456781234567812345678",
                "auth_provider": "nope",
                "identity_roles": {},
            }
        )
    )
    app = FastAPI()
    app.middleware("http")(control_auth.control_authorization_middleware)

    @app.post("/ordinary")
    async def ordinary() -> dict[str, bool]:
        return {"ok": True}

    test_client = TestClient(app, raise_server_exceptions=False)
    # Unknown provider: no identity can establish -> 401, never a 500.
    denied = test_client.post("/ordinary")
    assert denied.status_code == 401
    # The credential file path still works even with a broken provider name.
    ok = test_client.post(
        "/ordinary", headers={"authorization": f"Bearer {OPERATOR_SECRET}"}
    )
    assert ok.status_code == 200


def test_token_roles_limited_to_viewer_operator():
    assert TOKEN_ROLES == frozenset({"viewer", "operator"})
