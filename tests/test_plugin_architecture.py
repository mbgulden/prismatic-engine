from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from prismatic.core.registry import PluginLoader
from prismatic.gateway import server
from prismatic.interface.plugin import PluginContext, PrismaticPlugin
from prismatic.plugin_architecture import (
    MEDIA_CAPABILITY_CLASSES,
    future_plugin_blueprint,
    load_manifest,
    plugin_catalog,
    validate_manifest_payload,
    write_blueprint,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


FUTURE_CLASSES = ["video", "images", "music-sfx", "game-assets", "asset-forge-3d"]


def test_future_media_blueprints_validate_for_all_requested_plugin_families() -> None:
    for capability_class in FUTURE_CLASSES:
        manifest = future_plugin_blueprint(f"demo-{capability_class}", capability_class)
        validation = validate_manifest_payload(manifest)
        assert validation["errors"] == [], (capability_class, validation)
        assert "mcp_servers" in manifest
        assert manifest["asset_domains"] == MEDIA_CAPABILITY_CLASSES[capability_class]["asset_domains"]
        assert "gateway-api" in manifest["automation_surfaces"]
        assert "mcp" in manifest["integration_points"]
        assert manifest["endpoints"]


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
    pwp = next(item for item in catalog["plugins"] if item["name"] == "pwp-design-token-plugin")
    assert pwp["status"] == "ready"
    assert "pwp.credentials" in catalog["capability_index"]


def test_gateway_plugin_architecture_endpoints() -> None:
    client = TestClient(server.app)
    catalog = client.get("/api/plugins/catalog")
    assert catalog.status_code == 200
    assert "pwp-design-token-plugin" in {item["name"] for item in catalog.json()["plugins"]}

    architecture = client.get("/api/plugins/architecture")
    assert architecture.status_code == 200
    body = architecture.json()
    assert body["proven_future_plugin_classes"] == FUTURE_CLASSES
    assert "asset-forge-3d" in body["media_capability_classes"]
    assert "MCP server" in " ".join(body["recommended_surfaces"])


def test_plugin_architecture_cli_blueprint_validate_and_catalog(tmp_path: Path) -> None:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    blueprint = subprocess.run(
        [sys.executable, "scripts/plugin_architecture", "blueprint", "asset-forge-3d", "--class", "asset-forge-3d"],
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
        [sys.executable, "scripts/plugin_architecture", "scaffold", "prismatic-images", "--class", "images", "--target-dir", str(tmp_path / "blueprints")],
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
        [sys.executable, "scripts/plugin_architecture", "catalog", "--plugins-dir", str(tmp_path / "blueprints")],
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
        assert set(MEDIA_CAPABILITY_CLASSES[capability_class]["asset_domains"]).issubset(set(manifest.asset_domains))
