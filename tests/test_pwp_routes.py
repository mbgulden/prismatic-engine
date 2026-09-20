"""tests/test_pwp_routes.py — Unit and integration tests for PWP Studio API gateway routes."""

import pytest
from fastapi.testclient import TestClient

from prismatic.gateway.routes.pwp import pwp_router, load_pwp_studio_state
from prismatic.gateway.server import app

client = TestClient(app)


def test_pwp_status_route() -> None:
    resp = client.get("/api/pwp/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["plugin_id"] == "pwp-design-token-plugin"
    assert "state" in data
    assert "capabilities" in data


def test_pwp_connect_disconnect_routes() -> None:
    resp_disc = client.post("/api/pwp/disconnect")
    assert resp_disc.status_code == 200
    assert resp_disc.json()["state"] == "disconnected"

    resp_conn = client.post("/api/pwp/connect")
    assert resp_conn.status_code == 200
    assert resp_conn.json()["state"] == "connected"


def test_pwp_ingest_routes() -> None:
    resp = client.get("/api/pwp/ingest/graph")
    assert resp.status_code == 200
    assert "graph" in resp.json()

    resp_ingest = client.post(
        "/api/pwp/ingest/drive",
        json={"folder_url": "https://drive.google.com/test", "client_name": "Test Client"},
    )
    assert resp_ingest.status_code == 200
    data = resp_ingest.json()
    assert data["ok"] is True
    assert data["docs_parsed"] == 5


def test_pwp_synthesize_route() -> None:
    resp = client.post(
        "/api/pwp/synthesize",
        json={"site_name": "New Test Platform", "theme": "saas"},
    )
    assert resp.status_code == 200
    plan = resp.json()["build_plan"]
    assert plan["site_name"] == "New Test Platform"
    assert plan["theme"] == "saas"
    assert len(plan["pages"]) >= 3


def test_pwp_distill_route() -> None:
    resp = client.post(
        "/api/pwp/distill",
        json={"project": "Test Swarm Project", "priority": "Urgent"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "GRO-" in data["epic"]["epic_id"]


def test_pwp_theme_routes() -> None:
    # Compile
    tokens = {
        "colors": {
            "primary": "#ef4444",
            "secondary": "#10b981",
            "surface": "#020617",
            "text": "#ffffff",
        }
    }
    resp_compile = client.post("/api/pwp/theme/compile", json={"tokens": tokens})
    assert resp_compile.status_code == 200
    assert "--pwp-color-primary: #ef4444;" in resp_compile.json()["css"]

    # Validate
    resp_val = client.post("/api/pwp/theme/validate", json={"tokens": tokens})
    assert resp_val.status_code == 200
    assert resp_val.json()["valid"] is True

    # Diff
    resp_diff = client.post("/api/pwp/theme/diff", json={})
    assert resp_diff.status_code == 200
    assert "additive" in resp_diff.json()["diff"]


def test_pwp_credentials_and_provision_routes() -> None:
    resp_creds = client.get("/api/pwp/credentials/status")
    assert resp_creds.status_code == 200
    assert "google_drive" in resp_creds.json()["credentials"]

    resp_prov = client.post("/api/pwp/provision", json={"domain": "client-test.com"})
    assert resp_prov.status_code == 200
    assert resp_prov.json()["site"]["domain"] == "client-test.com"

    resp_kpi = client.get("/api/pwp/sites/kpi")
    assert resp_kpi.status_code == 200
    assert any(s["domain"] == "client-test.com" for s in resp_kpi.json()["sites"])


def test_pwp_multi_property_and_gap_routes(monkeypatch) -> None:
    # Signed webhook deliveries (fail-closed): the gateway requires the
    # Prismatic generic scheme for /api/pwp/webhooks/*.
    import hashlib as _hashlib
    import hmac as _hmac_module
    import json as _json
    import time as _time

    secret = "test-pwp-webhook-secret"
    monkeypatch.setenv("PRISMATIC_WEBHOOK_SECRET", secret)

    def _signed_zapier_post(payload: dict):
        body = _json.dumps(payload).encode()
        ts = int(_time.time())
        mac = _hmac_module.new(
            secret.encode(), f"{ts}.".encode() + body, _hashlib.sha256
        ).hexdigest()
        return client.post(
            "/api/pwp/webhooks/zapier",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Prismatic-Signature": f"t={ts},v1={mac}",
            },
        )

    # Workspaces
    resp_ws = client.get("/api/pwp/workspaces")
    assert resp_ws.status_code == 200
    assert len(resp_ws.json()["workspaces"]) >= 1

    # Project Export
    resp_exp = client.post("/api/pwp/export-project", json={"slug": "prismatic-core"})
    assert resp_exp.status_code == 200
    assert "scaffold" in resp_exp.json()

    # Webhooks
    resp_zap = _signed_zapier_post({"site_slug": "prismatic-core", "event": "lead_captured"})
    assert resp_zap.status_code == 200
    assert resp_zap.json()["ok"] is True

    # Linear Sync
    resp_sync = client.post("/api/pwp/sync-linear", json={"slug": "prismatic-core"})
    assert resp_sync.status_code == 200
    assert resp_sync.json()["kpi_status"] == "configured"

    # SEO Rankings
    resp_seo = client.get("/api/pwp/seo-rankings")
    assert resp_seo.status_code == 200
    assert "rankings" in resp_seo.json()

    # Codified CLI Capabilities (DNS, Vitals Audit, Staging Queue, Cron)
    resp_dns = client.post("/api/pwp/provision/verify-dns", json={"domain": "engine.prismatic.local"})
    assert resp_dns.status_code == 200
    assert resp_dns.json()["dns_status"] == "propagated"

    resp_vitals = client.post("/api/pwp/audit-vitals", json={"slug": "prismatic-core"})
    assert resp_vitals.status_code == 200
    assert "metrics" in resp_vitals.json()

    resp_staging = client.get("/api/pwp/changes/pending")
    assert resp_staging.status_code == 200
    assert len(resp_staging.json()["pending_changes"]) >= 1

    resp_cron = client.get("/api/pwp/cron/status")
    assert resp_cron.status_code == 200
    assert resp_cron.json()["cron_enabled"] is True
