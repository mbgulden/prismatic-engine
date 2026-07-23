from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from prismatic.core.registry import PluginLoader
from prismatic.gateway import server
from prismatic.interface.plugin import PluginContext, PrismaticPlugin
from prismatic.plugin_architecture import (
    MEDIA_CAPABILITY_CLASSES,
    discover_plugin_manifests,
    future_plugin_blueprint,
    get_shipped_plugins_dir,
    load_manifest,
    plugin_catalog,
    validate_manifest_payload,
    write_blueprint,
)
from prismatic.plugin_policy import preview_policy
from prismatic.pwp_integration import integration_status

REPO_ROOT = Path(__file__).resolve().parents[1]


FUTURE_CLASSES = ["video", "images", "music-sfx", "game-assets", "asset-forge-3d"]


def test_future_media_blueprints_validate_for_all_requested_plugin_families() -> None:
    for capability_class in FUTURE_CLASSES:
        manifest = future_plugin_blueprint(f"demo-{capability_class}", capability_class)
        validation = validate_manifest_payload(manifest)
        assert validation["errors"] == [], (capability_class, validation)
        assert "mcp_servers" in manifest
        assert (
            manifest["asset_domains"]
            == MEDIA_CAPABILITY_CLASSES[capability_class]["asset_domains"]
        )
        assert "gateway-api" in manifest["automation_surfaces"]
        assert "mcp" in manifest["integration_points"]
        assert manifest["endpoints"]
        assert manifest["provenance_required"] is True
        assert manifest["approval_gates"]
        assert "needs_approval" in manifest["job_lifecycle"]
        assert "artifact provenance" in " ".join(manifest["policy_checks"])


def test_asset_forge_3d_blueprint_is_external_service_with_http_mcp() -> None:
    manifest = future_plugin_blueprint("asset-forge-3d", "asset-forge-3d")
    assert manifest["plugin_type"] == "external-service"
    assert manifest["external_service"]["base_url_env"] == "ASSET_FORGE_3D_BASE_URL"
    assert manifest["external_service"]["api_key_env"] == "ASSET_FORGE_3D_API_KEY"
    assert manifest["mcp_servers"][0]["transport"] == "http"
    assert manifest["mcp_servers"][0]["url"] == "${ASSET_FORGE_3D_MCP_URL}"
    assert "forge_3d_asset" in manifest["mcp_servers"][0]["tools"]
    assert "model/gltf-binary" in manifest["artifact_types"]


def test_scaffolded_plugin_loads_and_registers_future_surfaces(tmp_path: Path) -> None:
    output = write_blueprint("demo-video", "video", tmp_path / "plugins")
    manifest_path = Path(output["manifest"])
    manifest = load_manifest(manifest_path)
    assert manifest.name == "prismatic-demo-video"
    assert manifest.mcp_servers[0].resources

    loader = PluginLoader(core_version="0.2.0", plugins_dir=str(tmp_path / "plugins"))
    ctx = PluginContext(
        config={"environment_capabilities": {"network", "filesystem-write"}},
        db_connection=None,
        state_dir=str(tmp_path / "state"),
    )
    loader.scan_and_load_plugins(ctx)
    assert "prismatic-demo-video" in loader.loaded_plugins
    assert "prismatic-demo-video" in loader.registered_capability_contracts
    assert loader.registered_mcp_servers[0]["plugin"] == "prismatic-demo-video"
    assert loader.registered_mcp_servers[0]["name"] == "demo-video-mcp"


def test_plugin_interface_optional_methods_are_available() -> None:
    class DemoPlugin(PrismaticPlugin):
        def on_init(self, context: PluginContext) -> None:
            self.context = context

        def register_tools(self) -> list[dict]:
            return []

    plugin = DemoPlugin()
    assert plugin.capability_contract() == {}
    assert plugin.connection_contract() == {}
    assert plugin.register_mcp_servers() == []
    assert plugin.register_api_routes() == []
    assert plugin.register_artifact_types() == []


def test_live_plugin_catalog_exposes_pwp_and_media_capability_classes() -> None:
    catalog = plugin_catalog(REPO_ROOT / "plugins")
    names = {item["name"] for item in catalog["plugins"]}
    assert "pwp-design-token-plugin" in names
    assert "asset-forge-3d" in catalog["media_capability_classes"]
    assert catalog["core_integration_points"]
    assert catalog["count"] >= 1
    pwp = next(
        item for item in catalog["plugins"] if item["name"] == "pwp-design-token-plugin"
    )
    assert pwp["status"] == "ready"
    assert "pwp.credentials" in catalog["capability_index"]
    assert catalog["governance_summary"]["requires_approval"] >= 1
    assert pwp["governance"]["readiness_state"] in {"ready", "warning"}
    assert pwp["governance"]["credential_redaction"] == "env-names-only"
    assert pwp["governance"]["approval_gates"]
    assert pwp["governance"]["surface_coverage"]["api_routes"] >= 4


def test_plugins_env_is_exclusive_operator_override(
    tmp_path: Path,
    monkeypatch,
) -> None:
    external = tmp_path / "external"
    shutil.copytree(get_shipped_plugins_dir() / "example_plugin", external / "operator")
    monkeypatch.setenv("PRISMATIC_PLUGINS_DIR", str(external))

    manifests = discover_plugin_manifests()
    assert len(manifests) == 1
    assert all(path.is_relative_to(external) for path in manifests)
    assert [item["name"] for item in plugin_catalog()["plugins"]] == ["example-plugin"]


def test_duplicate_plugin_names_fail_closed(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "external"
    source = get_shipped_plugins_dir() / "example_plugin"
    shutil.copytree(source, root / "first")
    shutil.copytree(source, root / "second")
    monkeypatch.setenv("PRISMATIC_PLUGINS_DIR", str(root))

    catalog = plugin_catalog()
    matches = [item for item in catalog["plugins"] if item["name"] == "example-plugin"]
    assert len(matches) == 2
    assert all(item["status"] == "invalid" for item in matches)
    assert all(
        any("duplicate plugin name" in error for error in item["validation"]["errors"])
        for item in matches
    )
    decision = preview_policy(
        "job_request",
        plugin_name="example-plugin",
        action="smoke_validate",
    )
    assert decision["decision"] == "block"


def test_plugin_governance_blocks_raw_secrets(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins" / "bad_secret"
    plugin_dir.mkdir(parents=True)
    fake_secret = "sk" + "-" + "liveplaceholderbodyforrawsecrettest"
    (plugin_dir / "plugin-manifest.yaml").write_text(
        f"""
schema_version: '1.1.0'
name: bad-secret-plugin
version: '0.1.0'
entry_point: bad_secret.plugin:BadSecretPlugin
core_version_constraint: '>=0.2.0, <2.0.0'
plugin_type: external-service
capabilities: []
asset_domains: [image]
artifact_types: [image/png]
integration_points: [manifest, loader, api]
automation_surfaces: [gateway-api]
external_service:
  name: Bad Secret
  api_key: {fake_secret}
connect_points: [connect]
""".strip(),
        encoding="utf-8",
    )
    catalog = plugin_catalog(tmp_path / "plugins")
    plugin = catalog["plugins"][0]
    assert plugin["governance"]["readiness_state"] == "blocked"
    assert plugin["governance"]["credential_redaction"] == "blocked"
    assert any(
        "raw secret" in b["message"]
        for b in plugin["governance"]["production_blockers"]
    )


def test_gateway_plugin_architecture_endpoints() -> None:
    client = TestClient(server.app)
    catalog = client.get("/api/plugins/catalog")
    assert catalog.status_code == 200
    assert "pwp-design-token-plugin" in {
        item["name"] for item in catalog.json()["plugins"]
    }

    architecture = client.get("/api/plugins/architecture")
    assert architecture.status_code == 200
    body = architecture.json()
    assert body["proven_future_plugin_classes"] == FUTURE_CLASSES
    assert "asset-forge-3d" in body["media_capability_classes"]
    assert "MCP server" in " ".join(body["recommended_surfaces"])

    governance = client.get("/api/plugins/governance")
    assert governance.status_code == 200
    governance_body = governance.json()
    assert governance_body["summary"]["requires_approval"] >= 1
    pwp = next(
        item
        for item in governance_body["plugins"]
        if item["name"] == "pwp-design-token-plugin"
    )
    assert pwp["governance"]["credential_redaction"] == "env-names-only"
    assert pwp["governance"]["approval_gates"]
    assert pwp["governance"]["surface_coverage"]["api_routes"] >= 4


def test_pwp_status_exposes_catalog_governance(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(
        "PRISMATIC_PWP_INTEGRATION_STATE", str(tmp_path / "pwp_state.json")
    )
    payload = integration_status()
    assert payload["risk_level"] == "low"
    assert payload["approval_gates"]
    assert payload["policy_checks"]
    assert payload["surface_coverage"]["api_routes"] >= 4
    assert "application/x-prismatic-theme+json" in payload["artifact_types"]


def test_dashboard_contains_generic_plugin_governance_surface() -> None:
    html = (REPO_ROOT / "prismatic/gateway/templates/dashboard.html").read_text(
        encoding="utf-8"
    )
    for marker in [
        "tab-btn-plugins",
        "section-plugins",
        "Plugin Governance Catalog",
        "/api/plugins/governance",
        "renderPluginGovernance",
        "approval_gates",
        "surface_coverage",
    ]:
        assert marker in html


def test_plugin_architecture_cli_blueprint_validate_and_catalog(tmp_path: Path) -> None:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    blueprint = subprocess.run(
        [
            sys.executable,
            "scripts/plugin_architecture",
            "blueprint",
            "asset-forge-3d",
            "--class",
            "asset-forge-3d",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert blueprint.returncode == 0, blueprint.stderr
    payload = json.loads(blueprint.stdout)
    assert payload["external_service"]["name"] == "Asset Forge 3D"

    scaffold = subprocess.run(
        [
            sys.executable,
            "scripts/plugin_architecture",
            "scaffold",
            "prismatic-images",
            "--class",
            "images",
            "--target-dir",
            str(tmp_path / "blueprints"),
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert scaffold.returncode == 0, scaffold.stderr
    out = json.loads(scaffold.stdout)
    validate = subprocess.run(
        [sys.executable, "scripts/plugin_architecture", "validate", out["manifest"]],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert validate.returncode == 0, validate.stdout + validate.stderr
    catalog = subprocess.run(
        [
            sys.executable,
            "scripts/plugin_architecture",
            "catalog",
            "--plugins-dir",
            str(tmp_path / "blueprints"),
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert catalog.returncode == 0, catalog.stderr
    catalog_payload = json.loads(catalog.stdout)
    assert catalog_payload["ready_count"] == 1


def test_checked_in_blueprint_manifests_validate() -> None:
    for capability_class, rel in {
        "video": "docs/plugin-blueprints/prismatic_video/plugin-manifest.yaml",
        "images": "docs/plugin-blueprints/prismatic_images/plugin-manifest.yaml",
        "music-sfx": "docs/plugin-blueprints/prismatic_music_sfx/plugin-manifest.yaml",
        "game-assets": "docs/plugin-blueprints/prismatic_game_assets/plugin-manifest.yaml",
        "asset-forge-3d": "docs/plugin-blueprints/asset_forge_3d/plugin-manifest.yaml",
    }.items():
        manifest = load_manifest(REPO_ROOT / rel)
        validation = validate_manifest_payload(manifest.raw, REPO_ROOT / rel)
        assert validation["errors"] == [], (capability_class, validation)
        assert manifest.mcp_servers
        assert set(
            MEDIA_CAPABILITY_CLASSES[capability_class]["asset_domains"]
        ).issubset(set(manifest.asset_domains))
