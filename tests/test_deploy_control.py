"""Tests for the gateway deploy control API (Portal Plan Phase 1, P0 #1).

Covers: auth classification (operator on POST, open GETs), trigger
validation/refusal and the receiver-dispatch seam, rollback state
validation and reuse of the existing ``GatewayRedeployer._rollback``
machinery, history parsing from alerts.log (+ manifest union), the
releases listing, and append-only control receipts.

The real ``pe.deploy`` registry/config/manifest machinery is used
throughout (isolated via env vars); only the network (receiver POST)
and the destructive part of rollback (``_rollback``) are stubbed.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from pe.deploy.gateway_redeploy import GatewayDeployResult
from pe.deploy.manifest import DeployManifestStore, DeployRecord
from prismatic.gateway import deploy_control as dc
from prismatic.gateway.control_auth import required_role

LATEST_SHA = "a" * 40
PREV_SHA = "b" * 40

PILOT = "mbgulden/test-pilot"
DEFAULT_REPO = "mbgulden/prismatic-engine"


def _pilot_overrides(tmp_path):
    return {
        "mirror_dir": str(tmp_path / "mirrors" / "test-pilot.git"),
        "target_service": "test-pilot.service",
        "target_node": "local",
        "release_prefix": "testpilot",
        "port": 19461,
        "hmac_secret_env": "DEPLOY_HMAC_SECRET_TEST_PILOT",
    }


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Isolate every filesystem dependency of the control API.

    HOME and PRISMATIC_STATE_DIR both point under tmp so the
    GatewayRedeployer root (``~/.prismatic``) and ``state_dir()`` agree,
    exactly as in production.
    """
    home = tmp_path / "home"
    prismatic = home / ".prismatic"
    receipts = tmp_path / "receipts.jsonl"
    alerts = tmp_path / "alerts.log"
    db = tmp_path / "deploy_records.json"
    registry = tmp_path / "deploy-repos.json"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(prismatic))
    monkeypatch.setenv("PRISMATIC_CONTROL_RECEIPTS", str(receipts))
    monkeypatch.setenv("PRISMATIC_ALERT_LOG", str(alerts))
    monkeypatch.setenv("PRISMATIC_DEPLOY_DB", str(db))
    monkeypatch.setenv("PRISMATIC_DEPLOY_REPOS_FILE", str(registry))
    monkeypatch.delenv("PRISMATIC_DEPLOY_REPOS", raising=False)
    monkeypatch.delenv("PRISMATIC_DEPLOY_REPOS_BASE64", raising=False)
    for var in ("DEPLOY_HMAC_SECRET", "DEPLOY_HMAC_SECRET_TEST_PILOT"):
        monkeypatch.delenv(var, raising=False)

    registry.write_text(
        json.dumps(
            {
                DEFAULT_REPO: {},
                PILOT: _pilot_overrides(tmp_path),
            }
        ),
        encoding="utf-8",
    )

    app = FastAPI()
    router = dc.create_deploy_control_router()
    assert router is not None
    app.include_router(router, prefix="/api")
    client = TestClient(app, raise_server_exceptions=False)
    return SimpleNamespace(
        tmp=tmp_path,
        home=home,
        prismatic=prismatic,
        receipts=receipts,
        alerts=alerts,
        db=db,
        registry=registry,
        client=client,
    )


def _links(prefix: str) -> tuple[str, str]:
    """(current_link_name, venv_link_name) for a release prefix."""
    if prefix == "prismatic-engine":
        return "current", "venv_current"
    return f"current-{prefix}", f"venv_current-{prefix}"


def _seed_releases(
    iso,
    *,
    repository=PILOT,
    prefix="testpilot",
    latest_id="dep-latest",
    prev_id="dep-prev",
    live="latest",
    with_prev_venv=True,
):
    """Create release dirs, venv dirs, live links, and manifest records."""
    rel = iso.prismatic / "releases"
    venvs = iso.prismatic / "venvs"
    latest_name = f"{prefix}-{LATEST_SHA}"
    prev_name = f"{prefix}-{PREV_SHA}"
    for name, deploy_id, sha in (
        (latest_name, latest_id, LATEST_SHA),
        (prev_name, prev_id, PREV_SHA),
    ):
        d = rel / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.json").write_text(
            json.dumps({"deploy_id": deploy_id, "success": True, "pr_sha": sha}),
            encoding="utf-8",
        )
        v = venvs / name
        v.mkdir(parents=True, exist_ok=True)
    if not with_prev_venv:
        import shutil

        shutil.rmtree(venvs / prev_name)
    current_link, venv_link = _links(prefix)
    live_name = latest_name if live == "latest" else prev_name
    for link_name in (current_link, venv_link):
        link = iso.prismatic / link_name
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(live_name)
    store = DeployManifestStore(db_path=iso.db)
    store.record_deploy(
        DeployRecord(
            deploy_id=prev_id,
            pr_sha=PREV_SHA,
            repository=repository,
            version_dir=str(rel / prev_name),
            success=True,
            deployed_at="2026-01-01T00:00:00+00:00",
        )
    )
    store.record_deploy(
        DeployRecord(
            deploy_id=latest_id,
            pr_sha=LATEST_SHA,
            repository=repository,
            version_dir=str(rel / latest_name),
            success=True,
            deployed_at="2026-01-02T00:00:00+00:00",
        )
    )
    return latest_name, prev_name


def _receipts(iso):
    return [
        json.loads(line)
        for line in iso.receipts.read_text(encoding="utf-8").strip().splitlines()
    ]


def _scope(method: str, path: str) -> dict:
    return {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "server": ("test", 80),
    }


def _role(method: str, path: str) -> str | None:
    return asyncio.run(required_role(Request(_scope(method, path))))


# ---------------------------------------------------------------------------
# Auth classification
# ---------------------------------------------------------------------------


def test_trigger_route_requires_operator_role():
    assert _role("POST", "/api/deploys") == "operator"


def test_rollback_route_requires_operator_role():
    assert _role("POST", "/api/deploys/dep-123/rollback") == "operator"


def test_history_and_releases_gets_are_open():
    assert _role("GET", "/api/deploys") is None
    assert _role("GET", "/api/deploys/releases") is None


# ---------------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------------


def test_trigger_unknown_repo_rejected(iso):
    resp = iso.client.post("/api/deploys", json={"repo": "octo/ghost"})
    assert resp.status_code == 404
    assert "unknown repository" in resp.json()["detail"]
    assert not iso.receipts.exists()


def test_trigger_malformed_repo_name_rejected(iso):
    resp = iso.client.post("/api/deploys", json={"repo": "not-a-repo"})
    assert resp.status_code == 404


def test_trigger_default_branch_refused_when_mirror_missing(iso):
    # No mirror at all: the API refuses rather than guessing a ref.
    resp = iso.client.post("/api/deploys", json={"repo": PILOT})
    assert resp.status_code == 400
    assert "no git mirror" in resp.json()["detail"]


def test_trigger_branch_ref_refused_when_mirror_missing(iso):
    resp = iso.client.post("/api/deploys", json={"repo": PILOT, "ref": "main"})
    assert resp.status_code == 400
    assert "no git mirror" in resp.json()["detail"]


def test_trigger_dispatches_and_writes_receipt(iso, monkeypatch):
    seen: dict = {}
    done = threading.Event()

    def fake_post(payload, repo):
        seen.update(payload=payload, repo=repo.full_name)
        done.set()
        return {"http_status": 202, "body": {"deploy_id": "dep-9"}}

    monkeypatch.setattr(dc, "_post_to_receiver", fake_post)
    resp = iso.client.post("/api/deploys", json={"repo": PILOT, "ref": LATEST_SHA})
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "dispatched"
    assert body["repo"] == PILOT
    assert body["pr_sha"] == LATEST_SHA
    assert body["dry_run"] is False
    assert body["trigger_id"].startswith("trig-")
    assert body["receipt_id"]

    assert done.wait(timeout=5), "dispatch thread never ran"
    payload = seen["payload"]
    assert payload["pr_sha"] == LATEST_SHA
    assert payload["repository"] == PILOT
    assert payload["dry_run"] is False
    assert payload["deployer"].startswith("portal:")
    assert seen["repo"] == PILOT

    receipts = _receipts(iso)
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["action"] == "deploy.trigger"
    assert receipt["repository"] == PILOT
    assert receipt["result"] == "dispatched"
    assert receipt["receipt_id"] == body["receipt_id"]
    assert receipt["actor"]
    assert receipt["timestamp"]
    assert receipt["after"]["trigger_id"] == body["trigger_id"]
    assert receipt["after"]["requested_sha"] == LATEST_SHA


def test_trigger_dry_run_flags_payload(iso, monkeypatch):
    seen: dict = {}

    def fake_post(payload, repo):
        seen["payload"] = payload
        return {"http_status": 202, "body": {}}

    monkeypatch.setattr(dc, "_post_to_receiver", fake_post)
    resp = iso.client.post(
        "/api/deploys", json={"repo": PILOT, "ref": PREV_SHA, "dry_run": True}
    )
    assert resp.status_code == 202
    assert resp.json()["dry_run"] is True
    assert seen["payload"]["dry_run"] is True


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


def test_rollback_unknown_deploy_id_404(iso):
    _seed_releases(iso)
    resp = iso.client.post("/api/deploys/dep-nope/rollback", json={})
    assert resp.status_code == 404


def test_rollback_refuses_non_latest_deploy(iso):
    _seed_releases(iso)
    resp = iso.client.post("/api/deploys/dep-prev/rollback", json={})
    assert resp.status_code == 409
    assert "not the latest" in resp.json()["detail"]
    assert not iso.receipts.exists()


def test_rollback_refuses_when_live_link_moved(iso):
    _seed_releases(iso, live="prev")  # world moved underneath the deploy
    resp = iso.client.post("/api/deploys/dep-latest/rollback", json={})
    assert resp.status_code == 409
    assert "live release" in resp.json()["detail"]


def test_rollback_refuses_missing_previous_venv(iso):
    _seed_releases(iso, with_prev_venv=False)
    resp = iso.client.post("/api/deploys/dep-latest/rollback", json={})
    assert resp.status_code == 409
    assert "previous venv" in resp.json()["detail"]


def test_rollback_refuses_default_repo_from_inside_itself(iso):
    _seed_releases(
        iso,
        repository=DEFAULT_REPO,
        prefix="prismatic-engine",
        latest_id="dep-def-latest",
        prev_id="dep-def-prev",
    )
    resp = iso.client.post("/api/deploys/dep-def-latest/rollback", json={})
    assert resp.status_code == 409
    assert "gateway itself" in resp.json()["detail"]


def test_rollback_refuses_non_local_node(iso):
    iso.registry.write_text(
        json.dumps(
            {
                DEFAULT_REPO: {},
                "octo/remote": {
                    "target_node": "node1",
                    "release_prefix": "octo-remote",
                },
            }
        ),
        encoding="utf-8",
    )
    _seed_releases(
        iso,
        repository="octo/remote",
        prefix="octo-remote",
        latest_id="dep-r-latest",
        prev_id="dep-r-prev",
    )
    resp = iso.client.post("/api/deploys/dep-r-latest/rollback", json={})
    assert resp.status_code == 409
    assert "not supported in Phase 1" in resp.json()["detail"]


def _stub_rollback(iso, monkeypatch, *, succeed=True):
    """Wrap the real redeployer: real link reads, stubbed _rollback."""
    from pe.deploy.config import load_repo_registry

    repo = load_repo_registry().get(PILOT)
    redeployer = dc._redeployer_for_repo(repo)
    calls: dict = {}

    def fake_rollback(res, flipped_venv=True, flipped_current=True):
        calls["res"] = res
        calls["flipped"] = (flipped_venv, flipped_current)
        if succeed:
            prev_name = Path(res.previous_version_dir).name
            prev_venv_name = Path(res.previous_venv_dir).name
            for link, target in (
                (redeployer.current_link, prev_name),
                (redeployer.venv_link, prev_venv_name),
            ):
                if link.is_symlink() or link.exists():
                    link.unlink()
                link.symlink_to(target)
        return succeed

    monkeypatch.setattr(redeployer, "_rollback", fake_rollback)
    monkeypatch.setattr(dc, "_redeployer_for_repo", lambda r: redeployer)
    return calls


def test_rollback_happy_path_reuses_existing_rollback(iso, monkeypatch):
    latest_name, prev_name = _seed_releases(iso)
    calls = _stub_rollback(iso, monkeypatch, succeed=True)

    resp = iso.client.post("/api/deploys/dep-latest/rollback", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rolled_back"
    assert body["deploy_id"] == "dep-latest"
    assert body["repo"] == PILOT
    assert body["restored_release"] == prev_name
    assert body["restored_sha"] == PREV_SHA
    assert body["receipt_id"]

    # The existing machinery was invoked with its real input contract:
    # a GatewayDeployResult reconstructed from the manifest.
    res = calls["res"]
    assert isinstance(res, GatewayDeployResult)
    assert Path(res.previous_version_dir).name == prev_name
    assert Path(res.version_dir).name == latest_name
    assert calls["flipped"] == (True, True)

    receipts = _receipts(iso)
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["action"] == "deploy.rollback"
    assert receipt["repository"] == PILOT
    assert receipt["result"] == "rolled_back"
    assert receipt["before"]["live_release"] == latest_name
    assert receipt["after"]["restored_release"] == prev_name


def test_rollback_failure_writes_failed_receipt(iso, monkeypatch):
    _seed_releases(iso)
    _stub_rollback(iso, monkeypatch, succeed=False)

    resp = iso.client.post("/api/deploys/dep-latest/rollback", json={})
    assert resp.status_code == 500
    receipt = _receipts(iso)[-1]
    assert receipt["action"] == "deploy.rollback"
    assert receipt["result"] == "rollback_failed"


# ---------------------------------------------------------------------------
# History (alerts.log + manifest union)
# ---------------------------------------------------------------------------


def _alert(name, ts, details, severity="info", summary=""):
    return {
        "name": name,
        "timestamp": ts,
        "severity": severity,
        "summary": summary,
        "details": details,
    }


def _write_alerts(iso):
    rows = [
        _alert(
            "PostMergeDeploySucceeded",
            "2026-09-23T10:00:00Z",
            f"deploy_id=d1 pr_sha={LATEST_SHA} version_dir=testpilot-{LATEST_SHA}",
            summary="deploy d1 succeeded",
        ),
        _alert(
            "GatewayRollbackCompleted",
            "2026-09-23T11:05:00Z",
            f"deploy_id=d2 pr_sha={PREV_SHA} restored_release=testpilot-{PREV_SHA}",
            summary="rollback d2 completed",
        ),
        _alert(
            "PostMergeDeployFailed",
            "2026-09-23T12:00:00Z",
            f"deploy_id=d3 pr_sha={'c' * 40} failed_release=testpilot-{'c' * 40}",
            severity="critical",
            summary="deploy d3 failed",
        ),
        {"name": "UnrelatedEvent", "timestamp": "2026-09-23T12:05:00Z"},
        "this line is not json",
    ]
    iso.alerts.write_text(
        "\n".join(r if isinstance(r, str) else json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )


def test_history_parses_alerts_log_newest_first(iso):
    _write_alerts(iso)
    # Manifest record d1 pins the repo for its alert entry.
    _seed_releases(iso, latest_id="d1", prev_id="d0")
    resp = iso.client.get("/api/deploys")
    assert resp.status_code == 200
    body = resp.json()
    kinds = [(e["type"], e["deploy_id"], e["source"]) for e in body["deploys"]]
    # Unrelated + malformed lines skipped; manifest-only d0 unioned in.
    assert ("deploy_failed", "d3", "alerts.log") in kinds
    assert ("rollback_completed", "d2", "alerts.log") in kinds
    assert ("deploy_succeeded", "d1", "alerts.log") in kinds
    assert ("deploy_succeeded", "d0", "manifest") in kinds
    # Newest first.
    timestamps = [e["timestamp"] for e in body["deploys"]]
    assert timestamps == sorted(timestamps, reverse=True)
    by_id = {e["deploy_id"]: e for e in body["deploys"]}
    assert by_id["d1"]["repo"] == PILOT  # from the manifest record
    assert by_id["d2"]["repo"] == PILOT  # from the release prefix
    assert by_id["d3"]["pr_sha"] == "c" * 40
    assert by_id["d3"]["severity"] == "critical"


def test_history_unions_manifest_dry_runs(iso):
    store = DeployManifestStore(db_path=iso.db)
    store.record_deploy(
        DeployRecord(
            deploy_id="dry-1",
            pr_sha=LATEST_SHA,
            repository=PILOT,
            success=True,
            dry_run=True,
            deployed_at="2026-09-23T09:00:00+00:00",
        )
    )
    resp = iso.client.get("/api/deploys")
    assert resp.status_code == 200
    by_id = {e["deploy_id"]: e for e in resp.json()["deploys"]}
    assert by_id["dry-1"]["type"] == "dry_run"
    assert by_id["dry-1"]["source"] == "manifest"


def test_history_filters(iso):
    _write_alerts(iso)
    _seed_releases(iso, latest_id="d1", prev_id="d0")

    resp = iso.client.get("/api/deploys", params={"status": "rolled_back"})
    assert [e["deploy_id"] for e in resp.json()["deploys"]] == ["d2"]

    resp = iso.client.get("/api/deploys", params={"status": "failed"})
    assert [e["deploy_id"] for e in resp.json()["deploys"]] == ["d3"]

    resp = iso.client.get("/api/deploys", params={"repo": DEFAULT_REPO})
    assert resp.json()["deploys"] == []

    resp = iso.client.get("/api/deploys", params={"limit": 2})
    assert resp.json()["count"] == 2
    assert len(resp.json()["deploys"]) == 2


def test_history_rejects_unknown_status(iso):
    resp = iso.client.get("/api/deploys", params={"status": "bogus"})
    assert resp.status_code == 400


def test_history_empty_when_no_alert_log_or_manifest(iso):
    resp = iso.client.get("/api/deploys")
    assert resp.status_code == 200
    assert resp.json() == {"count": 0, "deploys": []}


# ---------------------------------------------------------------------------
# Releases
# ---------------------------------------------------------------------------


def test_releases_lists_per_repo_state(iso, monkeypatch):
    latest_name, prev_name = _seed_releases(iso)
    # Deterministic ordering: make the previous release older.
    old = 1700000000
    os.utime(iso.prismatic / "releases" / prev_name, (old, old))
    (iso.prismatic / "releases" / "junk.txt").write_text("x", encoding="utf-8")

    resp = iso.client.get("/api/deploys/releases")
    assert resp.status_code == 200
    repos = {r["repo"]: r for r in resp.json()["repos"]}
    assert set(repos) == {DEFAULT_REPO, PILOT}

    pilot = repos[PILOT]
    assert pilot["release_prefix"] == "testpilot"
    assert pilot["target_service"] == "test-pilot.service"
    assert pilot["target_node"] == "local"
    assert pilot["current_release"] == latest_name
    assert pilot["mirror_present"] is False
    assert pilot["secret_configured"] is False  # no secret in env
    names = [r["name"] for r in pilot["releases"]]
    assert names == [latest_name, prev_name]
    assert pilot["releases"][0]["is_current"] is True
    assert pilot["releases"][0]["is_dir"] is True
    assert pilot["releases"][0]["sha"] == LATEST_SHA
    assert pilot["releases"][1]["is_current"] is False
    assert "junk.txt" not in names
    latest = pilot["latest_deploy"]
    assert latest["deploy_id"] == "dep-latest"
    assert latest["success"] is True
    assert latest["pr_sha"] == LATEST_SHA

    default = repos[DEFAULT_REPO]
    assert default["current_release"] is None
    assert default["releases"] == []
    assert default["latest_deploy"] is None

    # With a secret present, secret_configured flips to True.
    monkeypatch.setenv("DEPLOY_HMAC_SECRET_TEST_PILOT", "s3cret")
    pilot2 = {
        r["repo"]: r for r in iso.client.get("/api/deploys/releases").json()["repos"]
    }[PILOT]
    assert pilot2["secret_configured"] is True
