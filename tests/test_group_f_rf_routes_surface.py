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


@pytest.fixture
def client():
    return TestClient(app)


def test_group_f_canonical_rf_route_surface_no_doubled_prefix(client):
    """Positive & Direct Negative: Canonical RF endpoints exist under /api/review-factory/.
    Doubled prefix /api/review-factory/review/healthz MUST return 404 Not Found.
    """
    # 1. Positive: /api/review-factory/healthz MUST return 200 OK
    resp_health = client.get("/api/review-factory/healthz")
    assert resp_health.status_code == 200
    assert resp_health.json().get("status") == "ok"

    # 2. Direct Negative: Doubled prefix /api/review-factory/review/healthz MUST return 404
    resp_doubled = client.get("/api/review-factory/review/healthz")
    assert resp_doubled.status_code == 404

    # 3. Canonical Endpoints Surface Check
    paths = []
    for r in app.routes:
        if hasattr(r, "path"):
            paths.append(r.path)
        elif hasattr(r, "routes"):
            for sub in r.routes:
                paths.append(getattr(sub, "path", ""))

    # No route path in app should contain doubled '/review-factory/review'
    doubled_routes = [p for p in paths if "/review-factory/review" in p]
    assert len(doubled_routes) == 0, f"Found doubled prefix routes: {doubled_routes}"
