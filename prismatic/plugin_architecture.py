from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

PLUGIN_SCHEMA_VERSION = "1.1.0"
PLUGIN_MANIFEST_NAME = "plugin-manifest.yaml"

PluginKind = Literal[
    "automation",
    "asset-generation",
    "asset-library",
    "creative-media",
    "external-service",
    "governance",
    "website-production",
]

MEDIA_CAPABILITY_CLASSES: dict[str, dict[str, Any]] = {
    "video": {
        "asset_domains": ["video", "motion", "timeline"],
        "tools": ["generate_video", "edit_video", "transcode_video", "analyze_video"],
        "artifact_types": [
            "video/mp4",
            "video/webm",
            "application/x-prismatic-timeline+json",
        ],
        "mcp_resources": ["video.jobs", "video.assets", "video.timeline"],
    },
    "images": {
        "asset_domains": ["image", "texture", "reference"],
        "tools": ["generate_image", "edit_image", "upscale_image", "analyze_image"],
        "artifact_types": [
            "image/png",
            "image/jpeg",
            "image/webp",
            "application/x-prismatic-layered-image+json",
        ],
        "mcp_resources": ["image.jobs", "image.assets", "image.references"],
    },
    "music-sfx": {
        "asset_domains": ["music", "sfx", "audio"],
        "tools": ["generate_music", "generate_sfx", "stem_audio", "analyze_audio"],
        "artifact_types": [
            "audio/wav",
            "audio/mpeg",
            "audio/ogg",
            "application/x-prismatic-cue-sheet+json",
        ],
        "mcp_resources": ["audio.jobs", "audio.assets", "audio.cues"],
    },
    "game-assets": {
        "asset_domains": ["game-asset", "sprite", "prefab", "material", "level"],
        "tools": [
            "generate_game_asset",
            "validate_game_asset",
            "pack_asset_bundle",
            "export_engine_asset",
        ],
        "artifact_types": [
            "model/gltf-binary",
            "application/x-prismatic-prefab+json",
            "application/x-unitypackage",
            "application/x-godot-resource",
        ],
        "mcp_resources": ["game.assets", "game.bundles", "game.engine_exports"],
    },
    "asset-forge-3d": {
        "asset_domains": ["3d-model", "mesh", "rig", "material", "texture", "scene"],
        "tools": [
            "forge_3d_asset",
            "retopologize_mesh",
            "bake_textures",
            "rig_model",
            "export_3d_asset",
        ],
        "artifact_types": [
            "model/gltf-binary",
            "model/gltf+json",
            "application/x-fbx",
            "application/x-blender",
            "application/x-prismatic-asset-forge-job+json",
        ],
        "mcp_resources": [
            "asset_forge.jobs",
            "asset_forge.assets",
            "asset_forge.scenes",
            "asset_forge.exports",
        ],
        "external_service": True,
    },
}

CORE_INTEGRATION_POINTS = [
    "manifest",
    "loader",
    "tools",
    "api",
    "dashboard",
    "mcp",
    "asset-index",
    "artifact-store",
    "credential-provider",
    "native-cron",
    "governance",
]


@dataclass
class PluginEndpointSpec:
    method: str
    path: str
    description: str = ""
    auth: str = "operator"


@dataclass
class MCPServerSpec:
    name: str
    transport: str = "stdio"
    command: str | None = None
    url: str | None = None
    resources: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    auth_env: list[str] = field(default_factory=list)


@dataclass
class PluginArchitectureManifest:
    path: str
    name: str
    version: str
    entry_point: str
    schema_version: str = "1.0.0"
    description: str = ""
    plugin_type: str = "automation"
    categories: list[str] = field(default_factory=list)
    capabilities: list[dict[str, Any]] = field(default_factory=list)
    required_capabilities: list[str] = field(default_factory=list)
    asset_domains: list[str] = field(default_factory=list)
    artifact_types: list[str] = field(default_factory=list)
    integration_points: list[str] = field(default_factory=list)
    automation_surfaces: list[str] = field(default_factory=list)
    endpoints: list[PluginEndpointSpec] = field(default_factory=list)
    mcp_servers: list[MCPServerSpec] = field(default_factory=list)
    dashboard_surfaces: list[str] = field(default_factory=list)
    credential_providers: list[str] = field(default_factory=list)
    governance: list[str] = field(default_factory=list)
    connect_points: list[str] = field(default_factory=list)
    disconnect_points: list[str] = field(default_factory=list)
    external_service: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["proven_path_ready"] = not validate_manifest_payload(
            self.raw, Path(self.path)
        )["errors"]
        return payload


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def get_shipped_plugins_dir() -> Path:
    """Return the canonical path to package-shipped plugin resources."""
    return Path(__file__).resolve().parent / "shipped_plugins"


def default_plugins_dir() -> Path:
    """Return the primary plugin discovery directory.

    Respects PRISMATIC_PLUGINS_DIR environment variable if set by operator.
    Otherwise returns the canonical shipped plugins directory.
    """
    env_dir = os.environ.get("PRISMATIC_PLUGINS_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    return get_shipped_plugins_dir()


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _str_list(value: Any) -> list[str]:
    return [str(item) for item in _list(value) if item is not None]


def _endpoint_specs(raw: Any) -> list[PluginEndpointSpec]:
    specs: list[PluginEndpointSpec] = []
    for item in _list(raw):
        if not isinstance(item, dict):
            continue
        specs.append(
            PluginEndpointSpec(
                method=str(item.get("method", "GET")).upper(),
                path=str(item.get("path", "")),
                description=str(item.get("description", "")),
                auth=str(item.get("auth", "operator")),
            )
        )
    return specs


def _mcp_specs(raw: Any) -> list[MCPServerSpec]:
    specs: list[MCPServerSpec] = []
    for item in _list(raw):
        if not isinstance(item, dict):
            continue
        specs.append(
            MCPServerSpec(
                name=str(item.get("name", "")),
                transport=str(item.get("transport", "stdio")),
                command=item.get("command"),
                url=item.get("url"),
                resources=_str_list(item.get("resources")),
                tools=_str_list(item.get("tools")),
                auth_env=_str_list(item.get("auth_env")),
            )
        )
    return specs


def load_manifest(path: Path) -> PluginArchitectureManifest:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raw = {}
    return PluginArchitectureManifest(
        path=str(path),
        name=str(raw.get("name") or path.parent.name),
        version=str(raw.get("version") or "0.0.0"),
        entry_point=str(raw.get("entry_point") or ""),
        schema_version=str(raw.get("schema_version") or "1.0.0"),
        description=str(raw.get("description") or ""),
        plugin_type=str(raw.get("plugin_type") or raw.get("kind") or "automation"),
        categories=_str_list(raw.get("categories")),
        capabilities=[c for c in _list(raw.get("capabilities")) if isinstance(c, dict)],
        required_capabilities=_str_list(raw.get("required_capabilities")),
        asset_domains=_str_list(raw.get("asset_domains")),
        artifact_types=_str_list(raw.get("artifact_types")),
        integration_points=_str_list(raw.get("integration_points")),
        automation_surfaces=_str_list(raw.get("automation_surfaces")),
        endpoints=_endpoint_specs(raw.get("endpoints")),
        mcp_servers=_mcp_specs(raw.get("mcp_servers")),
        dashboard_surfaces=_str_list(raw.get("dashboard_surfaces")),
        credential_providers=_str_list(raw.get("credential_providers")),
        governance=_str_list(raw.get("governance")),
        connect_points=_str_list(raw.get("connect_points")),
        disconnect_points=_str_list(raw.get("disconnect_points")),
        external_service=raw.get("external_service")
        if isinstance(raw.get("external_service"), dict)
        else None,
        raw=raw,
    )


def discover_plugin_manifests(plugins_dir: Path | None = None) -> list[Path]:
    # Explicit arguments and PRISMATIC_PLUGINS_DIR are exclusive discovery
    # boundaries. Shipped resources are only the fallback when no override is
    # present, preventing silent shadowing by an identically named shipped
    # plugin.
    root = (
        Path(plugins_dir).expanduser().resolve()
        if plugins_dir is not None
        else default_plugins_dir()
    )

    manifests: list[Path] = []
    seen: set[Path] = set()
    if root.exists():
        for path in sorted(root.rglob(PLUGIN_MANIFEST_NAME)):
            if ".git" not in path.parts:
                resolved = path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    manifests.append(path)
    return sorted(manifests)


def validate_manifest_payload(
    raw: dict[str, Any], path: Path | None = None
) -> dict[str, list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    required = [
        "schema_version",
        "name",
        "version",
        "entry_point",
        "core_version_constraint",
    ]
    for field_name in required:
        if not raw.get(field_name):
            errors.append(f"missing required field: {field_name}")
    if raw.get("entry_point") and ":" not in str(raw["entry_point"]):
        errors.append("entry_point must use module.path:ClassName")
    schema = str(raw.get("schema_version") or "")
    if schema and schema not in {"1.0.0", PLUGIN_SCHEMA_VERSION}:
        warnings.append(f"schema_version {schema!r} is newer/unknown to this core")
    plugin_type = raw.get("plugin_type") or raw.get("kind")
    if plugin_type in {
        "creative-media",
        "asset-generation",
        "asset-library",
        "external-service",
    }:
        for field_name in [
            "capabilities",
            "asset_domains",
            "artifact_types",
            "integration_points",
            "automation_surfaces",
        ]:
            if not raw.get(field_name):
                errors.append(
                    f"media/asset plugin missing required field: {field_name}"
                )
        if not raw.get("mcp_servers"):
            warnings.append(
                "media/asset plugin has no mcp_servers; PE automation may be limited"
            )
    if raw.get("external_service") and not raw.get("connect_points"):
        errors.append("external_service plugins must declare connect_points")
    if raw.get("mcp_servers"):
        for idx, server in enumerate(_list(raw.get("mcp_servers"))):
            if not isinstance(server, dict):
                errors.append(f"mcp_servers[{idx}] must be an object")
                continue
            if not server.get("name"):
                errors.append(f"mcp_servers[{idx}] missing name")
            if server.get("transport") == "stdio" and not server.get("command"):
                warnings.append(f"mcp_servers[{idx}] stdio server has no command yet")
            if server.get("transport") in {"http", "sse"} and not server.get("url"):
                warnings.append(f"mcp_servers[{idx}] network server has no url yet")
    if path and path.name != PLUGIN_MANIFEST_NAME:
        warnings.append(f"manifest file should be named {PLUGIN_MANIFEST_NAME}")
    return {"errors": errors, "warnings": warnings}


def _contains_secret_value(value: Any) -> bool:
    """Return True when a manifest value appears to contain raw secret material."""
    if isinstance(value, dict):
        return any(_contains_secret_value(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_secret_value(v) for v in value)
    if not isinstance(value, str):
        return False
    upper = value.upper()
    if upper.endswith("_ENV") or (
        upper.isidentifier()
        and any(token in upper for token in ["API_KEY", "TOKEN", "SECRET", "PASSWORD"])
    ):
        return False
    risky_keys = ["sk-", "ghp_", "xoxb-", "AIza", "-----BEGIN", "Bearer "]
    return any(token in value for token in risky_keys)


def plugin_governance_summary(
    manifest: PluginArchitectureManifest, validation: dict[str, list[str]]
) -> dict[str, Any]:
    raw = manifest.raw
    blockers = list(validation.get("errors", []))
    warnings = list(validation.get("warnings", []))
    plugin_type = manifest.plugin_type
    risk_level = str(
        raw.get("risk_level")
        or (
            "high"
            if plugin_type == "external-service"
            else "medium"
            if manifest.mcp_servers
            else "low"
        )
    )
    approval_gates = _str_list(raw.get("approval_gates"))
    permissions = _str_list(raw.get("permissions") or raw.get("required_capabilities"))
    provenance_required = bool(
        raw.get(
            "provenance_required",
            bool(manifest.artifact_types or manifest.asset_domains),
        )
    )
    audit_events = _str_list(raw.get("audit_events"))
    job_lifecycle = _str_list(raw.get("job_lifecycle"))
    policy_checks = _str_list(raw.get("policy_checks"))

    if _contains_secret_value(raw):
        blockers.append(
            "manifest appears to contain raw secret material; use env var names only"
        )
    if (
        plugin_type
        in {"external-service", "creative-media", "asset-generation", "asset-library"}
        and not approval_gates
    ):
        warnings.append("media/service plugin should declare approval_gates")
    if provenance_required and not manifest.artifact_types:
        blockers.append("provenance_required is true but artifact_types is empty")
    if manifest.mcp_servers and not manifest.dashboard_surfaces:
        warnings.append("MCP/service plugin should declare dashboard_surfaces")
    if manifest.external_service and not any(
        server.auth_env for server in manifest.mcp_servers
    ):
        warnings.append("external service plugin should declare MCP auth_env names")
    if manifest.endpoints and not any(
        surface in manifest.automation_surfaces
        for surface in ["gateway-api", "dashboard"]
    ):
        warnings.append(
            "endpoints declared without gateway-api/dashboard automation surface"
        )
    if risk_level in {"high", "critical"} and not policy_checks:
        warnings.append("high-risk plugin should declare policy_checks")

    readiness_state = "blocked" if blockers else "warning" if warnings else "ready"
    return {
        "readiness_state": readiness_state,
        "risk_level": risk_level,
        "permissions": permissions,
        "approval_gates": approval_gates,
        "policy_checks": policy_checks,
        "provenance_required": provenance_required,
        "audit_events": audit_events,
        "job_lifecycle": job_lifecycle,
        "credential_redaction": "blocked"
        if _contains_secret_value(raw)
        else "env-names-only",
        "surface_coverage": {
            "tools": sum(
                len(_str_list(cap.get("tools"))) for cap in manifest.capabilities
            ),
            "mcp_servers": len(manifest.mcp_servers),
            "api_routes": len(manifest.endpoints),
            "artifact_types": len(manifest.artifact_types),
            "dashboard_surfaces": len(manifest.dashboard_surfaces),
            "connect_points": len(manifest.connect_points),
            "disconnect_points": len(manifest.disconnect_points),
        },
        "production_blockers": [
            {"severity": "blocking", "message": msg} for msg in blockers
        ]
        + [{"severity": "warning", "message": msg} for msg in warnings],
    }


def plugin_catalog(plugins_dir: Path | None = None) -> dict[str, Any]:
    manifests = [load_manifest(path) for path in discover_plugin_manifests(plugins_dir)]
    items = []
    for manifest in manifests:
        validation = validate_manifest_payload(manifest.raw, Path(manifest.path))
        item = manifest.to_dict()
        item["validation"] = validation
        item["governance"] = plugin_governance_summary(manifest, validation)
        item["status"] = (
            "ready"
            if item["governance"]["readiness_state"] in {"ready", "warning"}
            and not validation["errors"]
            else "invalid"
        )
        items.append(item)
    name_counts: dict[str, int] = {}
    for item in items:
        name = str(item.get("name") or "")
        name_counts[name] = name_counts.get(name, 0) + 1
    for item in items:
        name = str(item.get("name") or "")
        if name_counts.get(name, 0) <= 1:
            continue
        error = f"duplicate plugin name {name!r} in discovery boundary"
        item["validation"]["errors"].append(error)
        item["governance"]["readiness_state"] = "blocked"
        item["governance"]["production_blockers"].append(
            {"severity": "blocking", "message": error}
        )
        item["status"] = "invalid"
    capability_index: dict[str, list[str]] = {}
    for item in items:
        for cap in item.get("capabilities", []):
            cap_id = str(cap.get("id") or cap.get("label") or "unknown")
            capability_index.setdefault(cap_id, []).append(item["name"])
        for domain in item.get("asset_domains", []):
            capability_index.setdefault(f"asset-domain:{domain}", []).append(
                item["name"]
            )
    governance_summary = {
        "ready": sum(
            1 for item in items if item["governance"]["readiness_state"] == "ready"
        ),
        "warning": sum(
            1 for item in items if item["governance"]["readiness_state"] == "warning"
        ),
        "blocked": sum(
            1 for item in items if item["governance"]["readiness_state"] == "blocked"
        ),
        "high_risk": sum(
            1
            for item in items
            if item["governance"]["risk_level"] in {"high", "critical"}
        ),
        "requires_approval": sum(
            1 for item in items if item["governance"]["approval_gates"]
        ),
    }
    return {
        "schema_version": PLUGIN_SCHEMA_VERSION,
        "plugins_dir": str(plugins_dir or default_plugins_dir()),
        "count": len(items),
        "ready_count": sum(1 for item in items if item["status"] == "ready"),
        "invalid_count": sum(1 for item in items if item["status"] == "invalid"),
        "core_integration_points": CORE_INTEGRATION_POINTS,
        "media_capability_classes": MEDIA_CAPABILITY_CLASSES,
        "capability_index": capability_index,
        "governance_summary": governance_summary,
        "plugins": items,
    }


def future_plugin_blueprint(slug: str, capability_class: str) -> dict[str, Any]:
    if capability_class not in MEDIA_CAPABILITY_CLASSES:
        raise ValueError(f"unknown capability_class {capability_class!r}")
    cls = MEDIA_CAPABILITY_CLASSES[capability_class]
    safe_slug = re.sub(r"[^a-z0-9_-]+", "-", slug.lower()).strip("-")
    class_name = (
        "".join(part.capitalize() for part in safe_slug.replace("_", "-").split("-"))
        + "Plugin"
    )
    service_block = None
    if cls.get("external_service"):
        service_block = {
            "name": "Asset Forge 3D",
            "kind": "external-app-service",
            "base_url_env": "ASSET_FORGE_3D_BASE_URL",
            "api_key_env": "ASSET_FORGE_3D_API_KEY",
            "webhook_secret_env": "ASSET_FORGE_3D_WEBHOOK_SECRET",
        }
    return {
        "schema_version": PLUGIN_SCHEMA_VERSION,
        "name": f"prismatic-{safe_slug}",
        "version": "0.1.0",
        "description": f"Prismatic {capability_class} plugin scaffold.",
        "plugin_type": "external-service" if service_block else "creative-media",
        "entry_point": f"{safe_slug.replace('-', '_')}.plugin:{class_name}",
        "core_version_constraint": ">=0.2.0, <2.0.0",
        "categories": ["creative-media", capability_class],
        "risk_level": "high" if service_block else "medium",
        "permissions": ["network", "filesystem-write"],
        "approval_gates": [
            "operator approval for publish/export",
            "cost review for large batch jobs",
        ]
        if service_block
        else ["operator approval for publish/export"],
        "policy_checks": [
            "credential redaction",
            "artifact provenance",
            "rate/cost limits",
            "destructive action approval",
        ],
        "provenance_required": True,
        "audit_events": [
            "connect",
            "disconnect",
            "job_create",
            "job_complete",
            "asset_export",
        ],
        "job_lifecycle": [
            "queued",
            "running",
            "needs_approval",
            "completed",
            "failed",
            "cancelled",
        ],
        "required_capabilities": ["network", "filesystem-write"],
        "asset_domains": cls["asset_domains"],
        "artifact_types": cls["artifact_types"],
        "integration_points": [
            "manifest",
            "loader",
            "tools",
            "api",
            "dashboard",
            "mcp",
            "asset-index",
            "artifact-store",
            "governance",
        ],
        "automation_surfaces": [
            "agent-tools",
            "gateway-api",
            "dashboard",
            "native-cron",
            "mcp-server",
        ],
        "capabilities": [
            {
                "id": f"{capability_class}.generation",
                "label": f"{capability_class} generation and management",
                "category": capability_class,
                "tools": cls["tools"],
            }
        ],
        "endpoints": [
            {
                "method": "GET",
                "path": f"/api/plugins/{safe_slug}/status",
                "description": "Read plugin/service readiness.",
            },
            {
                "method": "POST",
                "path": f"/api/plugins/{safe_slug}/jobs",
                "description": "Create an AI automation job.",
            },
            {
                "method": "GET",
                "path": f"/api/plugins/{safe_slug}/assets",
                "description": "List generated/imported assets.",
            },
        ],
        "mcp_servers": [
            {
                "name": f"{safe_slug}-mcp",
                "transport": "stdio" if not service_block else "http",
                "command": f"python3 -m {safe_slug.replace('-', '_')}.mcp_server"
                if not service_block
                else None,
                "url": "${ASSET_FORGE_3D_MCP_URL}" if service_block else None,
                "resources": cls["mcp_resources"],
                "tools": cls["tools"],
                "auth_env": ["ASSET_FORGE_3D_API_KEY"] if service_block else [],
            }
        ],
        "dashboard_surfaces": [
            f"{capability_class} jobs",
            f"{capability_class} asset library",
            "provider health",
        ],
        "governance": [
            "secret-redacted status",
            "artifact provenance required",
            "agent jobs must emit durable asset IDs",
        ],
        "connect_points": [
            "PluginLoader validates and loads this manifest.",
            "Gateway exposes status/job/asset endpoints.",
            "Agents use registered tools or MCP tools to automate jobs.",
            "Generated assets enter the universal asset index with provenance.",
        ],
        "disconnect_points": [
            "Disconnect hides readiness/job creation without deleting assets.",
            "Core PE queues and unrelated plugins continue operating.",
        ],
        "external_service": service_block,
    }


def write_blueprint(
    slug: str, capability_class: str, target_dir: Path
) -> dict[str, str]:
    manifest = future_plugin_blueprint(slug, capability_class)
    package = str(manifest["entry_point"]).split(":", 1)[0].split(".", 1)[0]
    plugin_dir = target_dir / package
    plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = plugin_dir / PLUGIN_MANIFEST_NAME
    plugin_py = plugin_dir / "plugin.py"
    readme = plugin_dir / "README.md"
    class_name = str(manifest["entry_point"]).split(":", 1)[1]
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    plugin_py.write_text(
        "from __future__ import annotations\n\n"
        "from typing import Any\n\n"
        "from prismatic.interface.plugin import PluginContext, PrismaticPlugin\n\n\n"
        f"class {class_name}(PrismaticPlugin):\n"
        "    def on_init(self, context: PluginContext) -> None:\n"
        "        self.context = context\n\n"
        "    def register_tools(self) -> list[dict[str, Any]]:\n"
        "        return []\n\n"
        "    def capability_contract(self) -> dict[str, Any]:\n"
        f"        return {{'plugin_id': {manifest['name']!r}, 'capabilities': {manifest['capabilities']!r}}}\n\n"
        "    def register_mcp_servers(self) -> list[dict[str, Any]]:\n"
        f"        return {manifest['mcp_servers']!r}\n",
        encoding="utf-8",
    )
    readme.write_text(
        f"# {manifest['name']}\n\nGenerated Prismatic Engine plugin blueprint for `{capability_class}`.\n\n"
        "Move this directory under `plugins/` only when the plugin implementation is ready to load.\n",
        encoding="utf-8",
    )
    return {
        "plugin_dir": str(plugin_dir),
        "manifest": str(manifest_path),
        "plugin_py": str(plugin_py),
        "readme": str(readme),
    }


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
