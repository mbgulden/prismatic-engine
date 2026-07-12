from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from prismatic.plugin_architecture import plugin_catalog

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - yaml is present in PE runtime/tests
    yaml = None

PWP_PLUGIN_ID = "pwp-design-token-plugin"
PWP_PACKAGE = "pwp"
CONNECTION_CONNECTED = "connected"
CONNECTION_DISCONNECTED = "disconnected"
Action = Literal["connect", "disconnect", "refresh"]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_state_dir() -> Path:
    return Path(os.environ.get("PRISMATIC_STATE_DIR", repo_root() / "prismatic_state")).expanduser()


def default_state_path() -> Path:
    return Path(os.environ.get("PRISMATIC_PWP_INTEGRATION_STATE", default_state_dir() / "pwp_integration.json")).expanduser()


def plugin_root() -> Path:
    return repo_root() / "plugins" / PWP_PACKAGE


def manifest_path() -> Path:
    return plugin_root() / "plugin-manifest.yaml"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if yaml is None:
        data: dict[str, Any] = {}
        for line in text.splitlines():
            if ":" not in line or line.startswith(" "):
                continue
            key, value = line.split(":", 1)
            data[key.strip()] = value.strip().strip('"')
        return data
    loaded = yaml.safe_load(text) or {}
    return loaded if isinstance(loaded, dict) else {}


def _atomic_write_json(path: Path, payload: Any) -> None:
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


@dataclass
class PWPConnectionState:
    state: str = CONNECTION_DISCONNECTED
    connected_at: str | None = None
    disconnected_at: str | None = None
    last_action_at: str | None = None
    last_action: str | None = None
    last_verified_at: str | None = None
    last_error: str | None = None
    acknowledged_contract_version: str | None = None


@dataclass
class PWPCapability:
    id: str
    label: str
    category: str
    description: str
    connect_point: str
    disconnect_behavior: str
    dashboard_surface: str
    governance: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    workflows: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PWP_CAPABILITIES: list[PWPCapability] = [
    PWPCapability(
        id="pwp.theme-system",
        label="Portable website/theme system",
        category="website-production",
        description="Validate, diff, and compile portable PWP themes, tokens, modules, and starter templates without baking them into PE core.",
        connect_point="PE dashboard reads the PWP capability registry and shows theme/schema/compiler readiness under the PWP Plugin tab.",
        disconnect_behavior="Disconnect hides PWP capabilities from dashboard/guidance while leaving plugin files and site artifacts untouched.",
        dashboard_surface="PWP tab: capability inventory, schemas, CLI health, and governance blockers.",
        tools=["pwp theme validate", "pwp theme diff", "pwp theme check-compat"],
        workflows=["theme validation", "theme compatibility diff", "template rendering"],
        artifacts=["plugins/pwp/schemas/*.json", "plugins/pwp/templates/*", "plugins/pwp/docs/pwp-ai-theme-system-master-plan.md"],
        governance=["additive plugin boundary", "schema-backed theme contracts", "no core mutation required to remove PWP"],
    ),
    PWPCapability(
        id="pwp.credentials",
        label="Credential provider bridge",
        category="ops-automation",
        description="Rotate and verify provider credentials through PWP tools, currently Ubersuggest OAuth, with secret-redacted status surfaces.",
        connect_point="PE-native crons and agents call scripts/pwp credentials status/refresh instead of profile-local scripts.",
        disconnect_behavior="Disconnect marks provider bridge inactive and keeps PE crons/tools visible as requiring PWP reconnection.",
        dashboard_surface="PWP tab: credential provider list, CLI command, and secret-free provider status metadata.",
        tools=["pwp_credentials_refresh", "pwp_credentials_status", "pwp credentials refresh", "pwp credentials status"],
        workflows=["Ubersuggest refresh-token rotation", "credential smoke verification", "secret-redacted dashboard status"],
        artifacts=["plugins/pwp/oauth_credentials.py", "plugins/pwp/docs/credential-providers.md"],
        governance=["never expose token material", "state reports token lengths only", "fallback to manual OAuth only on invalid_grant/missing refresh"],
    ),
    PWPCapability(
        id="pwp.visual-governance",
        label="Visual/governance augmentation",
        category="quality-governance",
        description="Provide plugin-carried website governance patterns such as culture/diacritics checks and portable visual proof workflows that PE agents can invoke.",
        connect_point="PWP capability contract advertises governance checks for agents and dashboards; PE remains the scheduler/orchestrator.",
        disconnect_behavior="Disconnect removes PWP-specific governance hints while core PE queues, crons, and dashboards continue normally.",
        dashboard_surface="PWP tab: governance checklist and production blockers.",
        workflows=["PWP visual QA proof", "cultural diacritics/search compatibility", "page artifact governance"],
        governance=["operator-visible blockers", "additive checks only", "site-specific policies stay outside PE core"],
    ),
]


class PWPIntegrationStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_state_path()

    def load_state(self) -> PWPConnectionState:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return PWPConnectionState()
        except Exception as exc:
            return PWPConnectionState(last_error=f"state_load_error: {exc}")
        return PWPConnectionState(**{k: v for k, v in raw.items() if k in PWPConnectionState.__dataclass_fields__})

    def save_state(self, state: PWPConnectionState) -> None:
        _atomic_write_json(self.path, asdict(state))

    def mutate(self, action: Action) -> dict[str, Any]:
        state = self.load_state()
        now = _now()
        state.last_action = action
        state.last_action_at = now
        state.last_error = None
        if action == "connect":
            blockers = production_blockers(include_connection=False)
            hard = [b for b in blockers if b.get("severity") == "blocking"]
            if hard:
                state.state = CONNECTION_DISCONNECTED
                state.last_error = "; ".join(b["message"] for b in hard)
                self.save_state(state)
                return integration_status(store=self, state=state)
            state.state = CONNECTION_CONNECTED
            state.connected_at = now
            state.disconnected_at = None
            state.acknowledged_contract_version = contract_version()
            state.last_verified_at = now
        elif action == "disconnect":
            state.state = CONNECTION_DISCONNECTED
            state.disconnected_at = now
        elif action == "refresh":
            state.last_verified_at = now
        else:  # pragma: no cover - type guard
            raise ValueError(f"unsupported action {action}")
        self.save_state(state)
        return integration_status(store=self, state=state)


def contract_version() -> str:
    manifest = _read_yaml(manifest_path())
    return str(manifest.get("version") or "0.0.0")


def manifest_summary() -> dict[str, Any]:
    manifest = _read_yaml(manifest_path())
    return {
        "path": str(manifest_path()),
        "exists": manifest_path().exists(),
        "name": manifest.get("name") or PWP_PLUGIN_ID,
        "version": manifest.get("version") or "0.0.0",
        "description": manifest.get("description") or "",
        "entry_point": manifest.get("entry_point") or "",
        "core_version_constraint": manifest.get("core_version_constraint") or "",
        "hooks": manifest.get("hooks") or [],
        "capability_contract": manifest.get("capabilities") or [],
    }


def cli_status() -> dict[str, Any]:
    script = repo_root() / "scripts" / "pwp"
    if not script.exists():
        return {"ok": False, "script": str(script), "error": "scripts/pwp missing"}
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=repo_root(),
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    output = (result.stdout + result.stderr).strip()
    return {
        "ok": result.returncode == 2 and "pwp credentials" in output and "pwp theme" in output,
        "script": str(script),
        "exit_code": result.returncode,
        "usage": output,
    }


def production_blockers(include_connection: bool = True, connection_state: PWPConnectionState | None = None) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    manifest = manifest_summary()
    if not manifest["exists"]:
        blockers.append({"severity": "blocking", "system": "manifest", "message": "plugins/pwp/plugin-manifest.yaml is missing"})
    if manifest.get("name") != PWP_PLUGIN_ID:
        blockers.append({"severity": "warning", "system": "manifest", "message": f"PWP manifest name is {manifest.get('name')!r}, expected {PWP_PLUGIN_ID!r}"})
    entry = manifest.get("entry_point") or ""
    if entry != "pwp.plugin:PWPDesignTokenPlugin":
        blockers.append({"severity": "blocking", "system": "manifest", "message": "PWP manifest entry_point does not point at pwp.plugin:PWPDesignTokenPlugin"})
    for rel in ["schemas/pwp-theme.schema.json", "schemas/pwp-module.schema.json", "schemas/pwp-token.schema.json", "oauth_credentials.py", "compiler.py"]:
        if not (plugin_root() / rel).exists():
            blockers.append({"severity": "blocking", "system": "plugin-files", "message": f"plugins/pwp/{rel} missing"})
    cli = cli_status()
    if not cli.get("ok"):
        blockers.append({"severity": "blocking", "system": "cli", "message": "scripts/pwp CLI shim is not usable"})
    if include_connection and (connection_state or PWPIntegrationStore().load_state()).state != CONNECTION_CONNECTED:
        blockers.append({"severity": "warning", "system": "connection", "message": "PWP is installed but currently disconnected from PE additive capability surface"})
    return blockers


def integration_status(store: PWPIntegrationStore | None = None, state: PWPConnectionState | None = None) -> dict[str, Any]:
    store = store or PWPIntegrationStore()
    state = state or store.load_state()
    blockers = production_blockers(include_connection=False)
    hard_blockers = [b for b in blockers if b.get("severity") == "blocking"]
    connected = state.state == CONNECTION_CONNECTED and not hard_blockers
    connection_blockers = production_blockers(include_connection=True, connection_state=state)
    catalog_item = next((item for item in plugin_catalog(repo_root() / "plugins").get("plugins", []) if item.get("name") == PWP_PLUGIN_ID), {})
    catalog_governance = catalog_item.get("governance", {})
    return {
        "plugin_id": PWP_PLUGIN_ID,
        "package": PWP_PACKAGE,
        "state": state.state,
        "connected": connected,
        "status": "connected" if connected else ("blocked" if hard_blockers else "disconnected"),
        "state_path": str(store.path),
        "manifest": manifest_summary(),
        "capabilities": [cap.to_dict() for cap in PWP_CAPABILITIES],
        "connect_points": [cap.connect_point for cap in PWP_CAPABILITIES],
        "disconnect_points": [cap.disconnect_behavior for cap in PWP_CAPABILITIES],
        "dashboard_surfaces": sorted({cap.dashboard_surface for cap in PWP_CAPABILITIES}),
        "tool_names": sorted({tool for cap in PWP_CAPABILITIES for tool in cap.tools}),
        "workflows": sorted({wf for cap in PWP_CAPABILITIES for wf in cap.workflows}),
        "governance": sorted({rule for cap in PWP_CAPABILITIES for rule in cap.governance}),
        "catalog_governance": catalog_governance,
        "risk_level": catalog_governance.get("risk_level", "low"),
        "approval_gates": catalog_governance.get("approval_gates", []),
        "policy_checks": catalog_governance.get("policy_checks", []),
        "surface_coverage": catalog_governance.get("surface_coverage", {}),
        "artifact_types": catalog_item.get("artifact_types", []),
        "cli": cli_status(),
        "production_blockers": blockers + connection_blockers[len(blockers):],
        "connection": asdict(state),
        "generated_at": _now(),
    }


def connect_pwp(store: PWPIntegrationStore | None = None) -> dict[str, Any]:
    return (store or PWPIntegrationStore()).mutate("connect")


def disconnect_pwp(store: PWPIntegrationStore | None = None) -> dict[str, Any]:
    return (store or PWPIntegrationStore()).mutate("disconnect")


def refresh_pwp(store: PWPIntegrationStore | None = None) -> dict[str, Any]:
    return (store or PWPIntegrationStore()).mutate("refresh")
