"""Group F: 5-Point Counterexample Matrix Test for Canonical Review Factory Route Surface.

Matrix Coverage:
1. Positive: /api/review-factory/healthz returns 200 OK with status ok.
2. Direct Negative: /api/review-factory/review/healthz MUST NOT exist (no doubled prefix).
3. Collision & Isolation: All canonical RF endpoints exist under /api/review-factory without route collisions.
4. Boundary & Empty: Unauthenticated requests to protected endpoints return 401 Unauthorized.
5. Bypass Path: Malformed path requests return 404 Not Found.
"""

import pytest
from fastapi.testclient import TestClient
from prismatic.gateway.server import app
from prismatic.review_factory import routes as rf_routes
from prismatic.review_factory.db import ReviewFactoryDB
from prismatic.review_factory.models import ReviewJob, ReviewJobState
from prismatic.review_factory.queue import ReviewQueue


@pytest.fixture
def client():
    return TestClient(app)


def test_group_f_canonical_rf_route_surface_no_doubled_prefix(client):
    """Positive & Direct Negative: Canonical RF endpoints exist under /api/review-factory/.

    Doubled prefix /api/review-factory/review/healthz MUST return 404 Not Found.
    """
    resp_health = client.get("/api/review-factory/healthz")
    assert resp_health.status_code == 200
    assert resp_health.json().get("status") == "ok"

    resp_doubled = client.get("/api/review-factory/review/healthz")
    assert resp_doubled.status_code == 404

    paths = []
    for r in app.routes:
        if hasattr(r, "path"):
            paths.append(r.path)
        elif hasattr(r, "routes"):
            for sub in r.routes:
                paths.append(getattr(sub, "path", ""))

    doubled_routes = [p for p in paths if "/review-factory/review" in p]
    assert len(doubled_routes) == 0, f"Found doubled prefix routes: {doubled_routes}"


def _merge_ready_job(queue: ReviewQueue, *, tier: int, suffix: str) -> ReviewJob:
    job = ReviewJob(
        completed_work_id=f"cw-route-{suffix}",
        task_id=f"TASK-ROUTE-{suffix}",
        repository="local-repo",
        base_commit="a" * 40,
        base_tree="a" * 40,
        candidate_commit="b" * 40,
        candidate_tree="c" * 40,
        changed_paths_json="[]",
        risk_tier=tier,
        state=ReviewJobState.MERGE_READY.value,
    )
    queue.db.insert_review_job(job)
    return job


def test_group_f_authorize_route_maps_exact_domain_actor_without_bypass(
    tmp_path, monkeypatch, client
):
    queue = ReviewQueue(db=ReviewFactoryDB(tmp_path / "review.sqlite3"))
    monkeypatch.setattr(rf_routes._get_queue, "_instance", queue, raising=False)

    import hashlib
    import json
    import os

    from prismatic.core import merge_factory as mf_core
    from prismatic.gateway import control_auth

    test_keys = mf_core.parse_token_keys(
        "route-admin-secret-123:route-admin:merge-factory-admin"
    )

    def patched_get_principal(token):
        if not token:
            raise PermissionError("Authentication token is missing.")
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        principal = test_keys.get(token_hash)
        if not principal:
            raise PermissionError("Invalid or unauthorized authentication token.")
        return principal

    monkeypatch.setattr(mf_core, "get_authenticated_principal", patched_get_principal)
    monkeypatch.setattr(rf_routes, "get_authenticated_principal", patched_get_principal)

    credentials_path = tmp_path / "control-auth.json"
    credentials_path.write_text(
        json.dumps(
            {
                "version": 1,
                "credentials": [
                    {
                        "actor": "route-admin",
                        "token_sha256": hashlib.sha256(
                            b"route-admin-secret-123"
                        ).hexdigest(),
                        "roles": ["operator", "approver"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    os.chmod(credentials_path, 0o600)
    monkeypatch.setattr(
        control_auth, "_CREDENTIAL_FILE_ENV", "PRISMATIC_CONTROL_AUTH_FILE"
    )
    monkeypatch.setenv("PRISMATIC_CONTROL_AUTH_FILE", str(credentials_path))
    headers = {"Authorization": "Bearer route-admin-secret-123"}

    unauth_job = _merge_ready_job(queue, tier=0, suffix="UNAUTH")
    unauthenticated = client.post(
        f"/api/review-factory/job/{unauth_job.review_job_id}/authorize",
        json={"expected_merge_tree": "d" * 40},
    )
    assert unauthenticated.status_code == 401
    assert queue.db.get_authorization_for_job(unauth_job.review_job_id) is None
    assert queue.db.get_review_job(unauth_job.review_job_id).state == "merge_ready"

    missing_tree_job = _merge_ready_job(queue, tier=0, suffix="NO-TREE")
    missing_tree = client.post(
        f"/api/review-factory/job/{missing_tree_job.review_job_id}/authorize",
        json={},
        headers=headers,
    )
    assert missing_tree.status_code == 400
    assert queue.db.get_authorization_for_job(missing_tree_job.review_job_id) is None

    tier0 = _merge_ready_job(queue, tier=0, suffix="T0")
    tier0_response = client.post(
        f"/api/review-factory/job/{tier0.review_job_id}/authorize",
        json={"expected_merge_tree": "d" * 40},
        headers=headers,
    )
    assert tier0_response.status_code == 200
    assert tier0_response.json()["actor"] == "standing-policy: tier-0"
    assert tier0_response.json()["requested_by"] == "route-admin"
    stored_auth_t0 = queue.db.get_authorization_for_job(tier0.review_job_id)
    assert stored_auth_t0 is not None
    assert stored_auth_t0.actor == "standing-policy: tier-0"

    tier2 = _merge_ready_job(queue, tier=2, suffix="T2")
    tier2_response = client.post(
        f"/api/review-factory/job/{tier2.review_job_id}/authorize",
        json={"expected_merge_tree": "e" * 40},
        headers=headers,
    )
    assert tier2_response.status_code == 200
    assert tier2_response.json()["actor"] == "human:route-admin"
    stored_auth_t2 = queue.db.get_authorization_for_job(tier2.review_job_id)
    assert stored_auth_t2 is not None
    assert stored_auth_t2.actor == "human:route-admin"
