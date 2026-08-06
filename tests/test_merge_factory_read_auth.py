"""Focused authentication coverage for Merge Factory read endpoints.

RF-R3: same authenticated principal boundary as other Merge Factory reads.
Unauthenticated and invalid credentials return 401; authorized reads
retain 200/no-record semantics.  Inventory all Merge Factory GET routes
for auth coverage.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prismatic.api.routers.merge_factory import router


VALID_TOKEN="operator-reader-test-token-very-long-and-secure-aaa"  # ≥16 chars


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    monkeypatch.setenv(
        "PRISMATIC_MERGE_FACTORY_KEYS",
        f"{VALID_TOKEN}:operator:reader:ordinary",
    )
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def _attestation_params() -> dict:
    return {
        "base_sha": "a" * 40,
        "candidate_sha": "b" * 40,
        "manifest_digest": "c" * 64,
        "evidence_digest": "d" * 64,
        "repository": "mbgulden/prismatic-engine",
        "target": "main",
    }


@pytest.mark.parametrize(
    "path",
    [
        "/policy",
        "/cohort",
        "/judge/attestation/GRO-R3",
    ],
)
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer invalid-reader-token"},
    ],
    ids=["missing-bearer", "invalid-bearer"],
)
def test_merge_factory_reads_reject_missing_or_invalid_bearer(client, path, headers):
    """All Merge Factory GET routes require a valid bearer token (RF-R3)."""
    if path.startswith("/judge/attestation"):
        response = client.get(f"/api/merge-factory{path}", headers=headers, params=_attestation_params())
    else:
        response = client.get(f"/api/merge-factory{path}", headers=headers)
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


@pytest.mark.parametrize(
    "path",
    [
        "/policy",
        "/cohort",
    ],
)
def test_merge_factory_reads_preserve_response_for_configured_principal(client, path):
    """Authorized GET /policy and /cohort retain 200 + canonical response."""
    expected = {
        "/policy": {"stage_cap": 1, "cron_paused": True},
        "/cohort": {"cohort": [], "count": 0},
    }[path]
    response = client.get(
        f"/api/merge-factory{path}",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
    )
    assert response.status_code == 200
    assert response.json() == expected


def test_attestation_authorized_returns_200_or_404(client):
    """Authorized GET /judge/attestation returns 200 (no-record) or 404, NOT 401/403."""
    response = client.get(
        "/api/merge-factory/judge/attestation/GRO-NONEXISTENT",
        headers={"Authorization": f"Bearer {VALID_TOKEN}"},
        params=_attestation_params(),
    )
    assert response.status_code in (200, 404)


def test_inventory_all_get_routes_have_principal():
    """Inventory check: every @router.get in merge_factory.py has Depends(get_principal).

    This is the RF-R3 inventory gate.  Adding a new GET route without
    authentication will fail this test.
    """
    import re
    from prismatic.api.routers import merge_factory
    src = Path(merge_factory.__file__).read_text()
    pattern = re.compile(
        r'@router\.get\(\"([^\"]+)\"[^)]*\)\s*\nasync def (\w+)\((.*?)\)\s*->',
        re.MULTILINE | re.DOTALL,
    )
    routes = []
    for match in pattern.finditer(src):
        path = match.group(1)
        func = match.group(2)
        params = match.group(3)
        has_principal = "principal: Principal = Depends(get_principal)" in params
        routes.append((path, func, has_principal))
    unprotected = [(p, f) for p, f, has in routes if not has]
    assert not unprotected, (
        f"RF-R3: unprotected Merge Factory GET routes: {unprotected}"
    )
