from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from prismatic.gateway import server
from prismatic.plugin_artifacts import PluginArtifactStore, safe_local_artifact_path
from prismatic.plugin_jobs import PluginJobStore

PWP = "pwp-design-token-plugin"


def test_artifact_store_hashes_safe_local_file_and_persists(
    tmp_path: Path, monkeypatch
) -> None:
    state_dir = tmp_path / "state"
    artifact_path = state_dir / "pwp" / "theme-report.json"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text('{"ok": true}', encoding="utf-8")
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(state_dir))
    store = PluginArtifactStore(state_dir / "plugin_artifacts.json")

    artifact = store.create_artifact(
        plugin_name=PWP,
        job_id="plugjob_demo",
        artifact_type="application/x-prismatic-theme+json",
        mime_type="application/json",
        path_or_url=str(artifact_path),
        metadata={"api_token": "sk-demo-secret", "summary": "ok"},
        provenance={"provider": "pwp", "authorization": "Bearer demo"},
        input_summary={"prompt": "validate", "password": "secret"},
        provider_or_service="pwp",
    )

    expected = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    assert artifact["sha256"] == expected
    assert artifact["size_bytes"] == artifact_path.stat().st_size
    assert artifact["metadata"]["api_token"] == "[REDACTED]"
    assert artifact["provenance"]["authorization"] == "[REDACTED]"
    assert artifact["input_summary"]["password"] == "[REDACTED]"
    assert artifact["asset_id"] == artifact["artifact_id"]

    reloaded = PluginArtifactStore(state_dir / "plugin_artifacts.json")
    reloaded_artifact = reloaded.get_artifact(artifact["artifact_id"])
    assert reloaded_artifact is not None
    assert reloaded_artifact["sha256"] == expected
    assert reloaded.summary()["artifact_count"] == 1
    assert reloaded.summary()["by_plugin"][PWP] == 1


def test_artifact_store_allows_url_without_fetch_and_blocks_unsafe_local_read(
    tmp_path: Path,
) -> None:
    store = PluginArtifactStore(tmp_path / "plugin_artifacts.json")

    url_artifact = store.create_artifact(
        plugin_name=PWP,
        artifact_type="image/png",
        path_or_url="https://cdn.example.test/artifact.png",
    )
    assert url_artifact["sha256"] is None
    assert url_artifact["size_bytes"] is None

    unsafe = store.create_artifact(
        plugin_name=PWP,
        artifact_type="text/plain",
        path_or_url="/etc/passwd",
    )
    assert safe_local_artifact_path("/etc/passwd") is None
    assert unsafe["path_or_url"] == "/etc/passwd"
    assert unsafe["sha256"] is None
    assert unsafe["size_bytes"] is None


def test_artifact_store_lifecycle_states(tmp_path: Path) -> None:
    store = PluginArtifactStore(tmp_path / "plugin_artifacts.json")
    artifact = store.create_artifact(
        plugin_name=PWP,
        artifact_type="text/html",
        approval_state="pending",
        provenance={"provider": "pytest"},
    )

    approved = store.set_approval(
        artifact["artifact_id"], "approved", actor="michael", note="looks good"
    )
    assert approved is not None
    assert approved["approval_state"] == "approved"
    assert approved["metadata"]["operator_notes"][0]["actor"] == "michael"

    ready = store.mark_publish_ready(artifact["artifact_id"], actor="kai", note="ready")
    assert ready is not None
    assert ready["publish_state"] == "publish_ready"
    assert ready["export_history"][0]["event"] == "publish_ready"

    exported = store.add_export(artifact["artifact_id"], target="preview", actor="kai")
    assert exported is not None
    assert exported["export_history"][-1]["target"] == "preview"


def test_artifact_gateway_endpoints(monkeypatch, tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    artifact_file = state_dir / "artifact.html"
    artifact_file.parent.mkdir(parents=True)
    artifact_file.write_text("<h1>demo</h1>", encoding="utf-8")
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(state_dir))
    monkeypatch.setenv(
        "PRISMATIC_PLUGIN_ARTIFACTS_STATE", str(state_dir / "plugin_artifacts.json")
    )
    client = TestClient(server.app)

    create = client.post(
        "/api/plugins/artifacts",
        json={
            "plugin_name": PWP,
            "job_id": "plugjob_api",
            "artifact_type": "text/html",
            "mime_type": "text/html",
            "path_or_url": str(artifact_file),
            "metadata": {"summary": "ok"},
        },
    )
    assert create.status_code == 201
    artifact = create.json()
    assert artifact["sha256"]
    assert artifact["size_bytes"] == artifact_file.stat().st_size

    list_response = client.get("/api/plugins/artifacts")
    assert list_response.status_code == 200
    assert list_response.json()["summary"]["artifact_count"] == 1

    detail = client.get(f"/api/plugins/artifacts/{artifact['artifact_id']}")
    assert detail.status_code == 200
    assert detail.json()["artifact_id"] == artifact["artifact_id"]

    approve = client.post(
        f"/api/plugins/artifacts/{artifact['artifact_id']}/approve",
        json={"actor": "michael"},
    )
    assert approve.status_code == 200
    assert approve.json()["approval_state"] == "approved"

    ready = client.post(
        f"/api/plugins/artifacts/{artifact['artifact_id']}/publish-ready",
        json={"actor": "kai"},
    )
    assert ready.status_code == 200
    assert ready.json()["publish_state"] == "publish_ready"

    missing = client.get("/api/plugins/artifacts/plugart_missing")
    assert missing.status_code == 404
    bad = client.post("/api/plugins/artifacts", json={"artifact_type": "text/plain"})
    assert bad.status_code == 400


def test_job_artifact_emitted_creates_universal_artifact(
    monkeypatch, tmp_path: Path
) -> None:
    state_dir = tmp_path / "state"
    artifact_file = state_dir / "pwp" / "out.html"
    artifact_file.parent.mkdir(parents=True)
    artifact_file.write_text("<p>artifact</p>", encoding="utf-8")
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(state_dir))
    monkeypatch.setenv(
        "PRISMATIC_PLUGIN_JOBS_STATE", str(state_dir / "plugin_jobs.json")
    )
    monkeypatch.setenv(
        "PRISMATIC_PLUGIN_ARTIFACTS_STATE", str(state_dir / "plugin_artifacts.json")
    )

    job_store = PluginJobStore(state_dir / "plugin_jobs.json")
    job = job_store.create_job(PWP, "theme_validate", input_summary={"theme": "demo"})
    emitted = job_store.append_event(
        job["job_id"],
        "artifact_emitted",
        actor="worker",
        source="pwp",
        artifact={
            "artifact_type": "text/html",
            "mime_type": "text/html",
            "path_or_url": str(artifact_file),
            "metadata": {"summary": "ok"},
            "provenance": {"provider": "pwp"},
        },
    )
    assert emitted is not None
    assert emitted["artifact_ids"]
    linked = emitted["artifacts"][0]
    assert linked["sha256"] == hashlib.sha256(artifact_file.read_bytes()).hexdigest()
    assert linked["provenance"]["source_plugin"] == PWP
    assert linked["provenance"]["source_job"] == job["job_id"]

    artifact_store = PluginArtifactStore(state_dir / "plugin_artifacts.json")
    assert artifact_store.summary()["artifact_count"] == 1
    stored_linked = artifact_store.get_artifact(linked["artifact_id"])
    assert stored_linked is not None
    assert stored_linked["job_id"] == job["job_id"]


def test_dashboard_contains_artifact_provenance_surface() -> None:
    html = (
        Path(__file__).resolve().parents[1]
        / "prismatic/gateway/templates/dashboard.html"
    ).read_text(encoding="utf-8")
    for marker in [
        "plugin-artifact-summary",
        "plugin-artifacts-table",
        "/api/plugins/artifacts",
        "renderPluginArtifacts",
        "Universal Plugin Artifacts / Provenance Registry",
        "publish_state",
    ]:
        assert marker in html
