"""P0 #4: ``GET /api/deploy/status`` returns real deploy state.

Hermetic: every test uses a tmp ``PRISMATIC_STATE_DIR`` and a loopback
test server (or a closed port) for the receiver probe. The real
``~/.prismatic`` release links are never touched.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from prismatic.gateway import deploy_status as ds


# ── fixtures ──────────────────────────────────────────────────────────────


def _write_alerts(base: Path, entries: list) -> None:
    with (base / "alerts.log").open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(entry if isinstance(entry, str) else json.dumps(entry))
            handle.write("\n")


def _alert(name, ts, details, summary=""):
    return {
        "timestamp": ts,
        "name": name,
        "summary": summary or f"{name} {details[:40]}",
        "severity": "info" if name.endswith("Succeeded") else "critical",
        "details": details,
    }


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    base = tmp_path / "prismatic"
    releases = base / "releases"
    versions = base / "versions"
    releases.mkdir(parents=True)
    versions.mkdir(parents=True)
    (versions / "engine-aaa111").mkdir()
    (versions / "pilot-bbb222").mkdir()
    os.symlink(versions / "engine-aaa111", releases / "engine")
    os.symlink(versions / "pilot-bbb222", releases / "pilot")
    (base / "deploy-repos.json").write_text(
        json.dumps({"acme/engine": {}, "acme/pilot": {"release_prefix": "pilot"}}),
        encoding="utf-8",
    )
    _write_alerts(
        base,
        [
            _alert(
                "PostMergeDeploySucceeded",
                "2026-09-22T10:00:00+00:00",
                "deploy_id=deploy-aaa111 pr_sha=aaa111aaa111 version_dir=engine-aaa111 duration_ms=100",
                "deploy deploy-aaa111 succeeded",
            ),
            _alert(
                "PostMergeDeployFailed",
                "2026-09-22T11:00:00+00:00",
                "deploy_id=deploy-bbb222 pr_sha=bbb222bbb222 version_dir=pilot-bbb222 failure_reason=boom",
                "deploy deploy-bbb222 failed: boom",
            ),
            # Newer engine deploy on a version dir that is not the active link.
            _alert(
                "PostMergeDeploySucceeded",
                "2026-09-22T12:00:00+00:00",
                "deploy_id=deploy-aaa112 pr_sha=aaa112aaa112 version_dir=engine-aaa112 duration_ms=100",
                "deploy deploy-aaa112 succeeded",
            ),
            "this line is not json and must be skipped",
            {"name": "SomeOtherAlert", "details": "deploy_id=deploy-zzz version_dir=engine-zzz"},
            _alert(
                "PostMergeDeployFailed",
                "2026-09-22T13:00:00+00:00",
                "deploy_id=deploy-orphan pr_sha=deadbeef failure_reason=no repo attribution",
                "deploy deploy-orphan failed",
            ),
        ],
    )
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(base))
    return base


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        assert self.path == "/health"
        body = b'{"status":"ok","port":9460}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def health_server():
    server = HTTPServer(("127.0.0.1", 0), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    thread.join()


def _closed_port() -> int:
    probe = HTTPServer(("127.0.0.1", 0), _HealthHandler)
    port = probe.server_address[1]
    probe.server_close()
    return port


# ── receiver probe ────────────────────────────────────────────────────────


def test_probe_receiver_up(health_server):
    health = ds.probe_receiver_health(port=health_server, timeout=2.0)
    assert health["reachable"] is True
    assert health["port"] == health_server
    assert health["http_status"] == 200
    assert health["body"] == {"status": "ok", "port": 9460}
    assert health["error"] is None
    assert health["latency_ms"] is not None


def test_probe_receiver_down():
    health = ds.probe_receiver_health(port=_closed_port(), timeout=1.0)
    assert health["reachable"] is False
    assert health["http_status"] is None
    assert health["body"] is None
    assert health["error"]


# ── active releases: read-only ─────────────────────────────────────────────


def test_active_releases_read(state_dir):
    releases = ds.read_active_releases(state_dir)
    assert set(releases) == {"engine", "pilot"}
    assert releases["engine"]["active_release"] == "engine-aaa111"
    assert releases["engine"]["release_target_exists"] is True
    assert releases["pilot"]["active_release"] == "pilot-bbb222"


def test_active_releases_never_written(state_dir):
    before = {
        name: os.readlink(state_dir / "releases" / name) for name in ("engine", "pilot")
    }
    ds.build_deploy_status(base=state_dir, probe=False)
    after = {
        name: os.readlink(state_dir / "releases" / name) for name in ("engine", "pilot")
    }
    assert before == after


def test_broken_release_link_listed_with_null_release(tmp_path, monkeypatch):
    base = tmp_path / "prismatic"
    (base / "releases").mkdir(parents=True)
    os.symlink(base / "versions" / "gone", base / "releases" / "ghost")
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(base))
    releases = ds.read_active_releases(base)
    assert releases["ghost"]["release_target_exists"] is False


def test_missing_releases_dir(tmp_path, monkeypatch):
    base = tmp_path / "empty"
    base.mkdir()
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(base))
    assert ds.read_active_releases(base) == {}
    payload = ds.build_deploy_status(base=base, probe=False)
    assert payload["repos"] == {}
    assert payload["has_deploys"] is False


# ── last deploy per repo ──────────────────────────────────────────────────


def test_last_deploy_per_repo(state_dir):
    last = ds.read_last_deploys(state_dir, ["engine", "pilot"])
    assert set(last) == {"engine", "pilot"}
    # Newest engine entry wins (even though its version dir is not the active link).
    assert last["engine"]["deploy_id"] == "deploy-aaa112"
    assert last["engine"]["success"] is True
    assert last["engine"]["name"] == "PostMergeDeploySucceeded"
    # Pilot's only entry is a failure.
    assert last["pilot"]["deploy_id"] == "deploy-bbb222"
    assert last["pilot"]["success"] is False
    assert last["pilot"]["name"] == "PostMergeDeployFailed"
    # Malformed lines, foreign alert names, and unattributed entries are dropped.
    assert "orphan" not in json.dumps(last)


def test_empty_history(state_dir):
    (state_dir / "alerts.log").unlink()
    assert ds.read_last_deploys(state_dir, ["engine", "pilot"]) == {}
    payload = ds.build_deploy_status(base=state_dir, probe=False)
    assert payload["has_deploys"] is False
    assert payload["latest_deploy_id"] == ""
    assert payload["repos"]["engine"]["last_deploy"] is None


def test_repo_path_attribution(tmp_path, monkeypatch):
    """Entries without version_dir attribute via the repos/<owner>/<name> path."""
    base = tmp_path / "prismatic"
    (base / "releases").mkdir(parents=True)
    _write_alerts(
        base,
        [
            _alert(
                "PostMergeDeployFailed",
                "2026-09-22T14:00:00+00:00",
                "deploy_id=deploy-q1 pr_sha=q1q1 failure_reason=bad sha in "
                "/home/ubuntu/.prismatic/repos/acme/engine; refusing",
                "deploy deploy-q1 failed",
            ),
        ],
    )
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(base))
    last = ds.read_last_deploys(base, ["engine"])
    assert last["engine"]["deploy_id"] == "deploy-q1"


# ── assembled payload ─────────────────────────────────────────────────────


def test_build_deploy_status_shape(state_dir, health_server, monkeypatch):
    monkeypatch.setenv("PRISMATIC_DEPLOY_RECEIVER_PORT", str(health_server))
    payload = ds.build_deploy_status()
    assert payload["status"] == "active"
    assert payload["mode"] == "standalone"
    assert payload["has_deploys"] is True
    assert payload["latest_deploy_id"] == "deploy-aaa112"
    receiver = payload["receiver"]
    assert receiver["reachable"] is True
    assert receiver["port"] == health_server
    engine = payload["repos"]["engine"]
    assert engine["repo"] == "acme/engine"
    assert engine["active_release"] == "engine-aaa111"
    assert engine["last_deploy"]["deploy_id"] == "deploy-aaa112"
    assert isinstance(payload["timestamp"], float)


def test_api_deploy_status_route_shape(state_dir, health_server, monkeypatch):
    """The live route returns the v1 schema with backward-compat keys."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from prismatic.deploy.routes import create_deploy_router

    monkeypatch.setenv("PRISMATIC_DEPLOY_RECEIVER_PORT", str(health_server))
    app = FastAPI()
    app.include_router(create_deploy_router())
    client = TestClient(app)
    resp = client.get("/deploy/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "active"
    assert "has_deploys" in data and "latest_deploy_id" in data
    assert data["receiver"]["reachable"] is True
    assert set(data["repos"]) == {"engine", "pilot"}
    assert data["repos"]["pilot"]["last_deploy"]["success"] is False
