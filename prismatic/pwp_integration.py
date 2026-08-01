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

from prismatic.plugin_architecture import get_shipped_plugins_dir, plugin_catalog
from prismatic.plugin_artifacts import (
    PluginArtifactStore,
    store_from_env as artifact_store_from_env,
)
from prismatic.plugin_jobs import PluginJobStore, store_from_env as job_store_from_env

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
    return Path(
        os.environ.get("PRISMATIC_STATE_DIR", repo_root() / "prismatic_state")
    ).expanduser()


def default_state_path() -> Path:
    return Path(
        os.environ.get(
            "PRISMATIC_PWP_INTEGRATION_STATE",
            default_state_dir() / "pwp_integration.json",
        )
    ).expanduser()


def plugin_root() -> Path:
    return get_shipped_plugins_dir() / PWP_PACKAGE


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
        workflows=[
            "theme validation",
            "theme compatibility diff",
            "template rendering",
        ],
        artifacts=[
            "plugins/pwp/schemas/*.json",
            "plugins/pwp/templates/*",
            "plugins/pwp/docs/pwp-ai-theme-system-master-plan.md",
        ],
        governance=[
            "additive plugin boundary",
            "schema-backed theme contracts",
            "no core mutation required to remove PWP",
        ],
    ),
    PWPCapability(
        id="pwp.credentials",
        label="Credential provider bridge",
        category="ops-automation",
        description="Rotate and verify provider credentials through PWP tools, currently Ubersuggest OAuth, with secret-redacted status surfaces.",
        connect_point="PE-native crons and agents call scripts/pwp credentials status/refresh instead of profile-local scripts.",
        disconnect_behavior="Disconnect marks provider bridge inactive and keeps PE crons/tools visible as requiring PWP reconnection.",
        dashboard_surface="PWP tab: credential provider list, CLI command, and secret-free provider status metadata.",
        tools=[
            "pwp_credentials_refresh",
            "pwp_credentials_status",
            "pwp credentials refresh",
            "pwp credentials status",
        ],
        workflows=[
            "Ubersuggest refresh-token rotation",
            "credential smoke verification",
            "secret-redacted dashboard status",
        ],
        artifacts=[
            "plugins/pwp/oauth_credentials.py",
            "plugins/pwp/docs/credential-providers.md",
        ],
        governance=[
            "never expose token material",
            "state reports token lengths only",
            "fallback to manual OAuth only on invalid_grant/missing refresh",
        ],
    ),
    PWPCapability(
        id="pwp.visual-governance",
        label="Visual/governance augmentation",
        category="quality-governance",
        description="Provide plugin-carried website governance patterns such as culture/diacritics checks and portable visual proof workflows that PE agents can invoke.",
        connect_point="PWP capability contract advertises governance checks for agents and dashboards; PE remains the scheduler/orchestrator.",
        disconnect_behavior="Disconnect removes PWP-specific governance hints while core PE queues, crons, and dashboards continue normally.",
        dashboard_surface="PWP tab: governance checklist and production blockers.",
        workflows=[
            "PWP visual QA proof",
            "cultural diacritics/search compatibility",
            "page artifact governance",
        ],
        governance=[
            "operator-visible blockers",
            "additive checks only",
            "site-specific policies stay outside PE core",
        ],
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
        return PWPConnectionState(
            **{
                k: v
                for k, v in raw.items()
                if k in PWPConnectionState.__dataclass_fields__
            }
        )

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
        "ok": result.returncode == 2
        and "pwp credentials" in output
        and "pwp theme" in output,
        "script": str(script),
        "exit_code": result.returncode,
        "usage": output,
    }


def production_blockers(
    include_connection: bool = True, connection_state: PWPConnectionState | None = None
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    manifest = manifest_summary()
    if not manifest["exists"]:
        blockers.append(
            {
                "severity": "blocking",
                "system": "manifest",
                "message": "plugins/pwp/plugin-manifest.yaml is missing",
            }
        )
    if manifest.get("name") != PWP_PLUGIN_ID:
        blockers.append(
            {
                "severity": "warning",
                "system": "manifest",
                "message": f"PWP manifest name is {manifest.get('name')!r}, expected {PWP_PLUGIN_ID!r}",
            }
        )
    entry = manifest.get("entry_point") or ""
    if entry != "pwp.plugin:PWPDesignTokenPlugin":
        blockers.append(
            {
                "severity": "blocking",
                "system": "manifest",
                "message": "PWP manifest entry_point does not point at pwp.plugin:PWPDesignTokenPlugin",
            }
        )
    for rel in [
        "schemas/pwp-theme.schema.json",
        "schemas/pwp-module.schema.json",
        "schemas/pwp-token.schema.json",
        "oauth_credentials.py",
        "compiler.py",
    ]:
        if not (plugin_root() / rel).exists():
            blockers.append(
                {
                    "severity": "blocking",
                    "system": "plugin-files",
                    "message": f"plugins/pwp/{rel} missing",
                }
            )
    cli = cli_status()
    if not cli.get("ok"):
        blockers.append(
            {
                "severity": "blocking",
                "system": "cli",
                "message": "scripts/pwp CLI shim is not usable",
            }
        )
    if (
        include_connection
        and (connection_state or PWPIntegrationStore().load_state()).state
        != CONNECTION_CONNECTED
    ):
        blockers.append(
            {
                "severity": "warning",
                "system": "connection",
                "message": "PWP is installed but currently disconnected from PE additive capability surface",
            }
        )
    return blockers


def integration_status(
    store: PWPIntegrationStore | None = None, state: PWPConnectionState | None = None
) -> dict[str, Any]:
    store = store or PWPIntegrationStore()
    state = state or store.load_state()
    blockers = production_blockers(include_connection=False)
    hard_blockers = [b for b in blockers if b.get("severity") == "blocking"]
    connected = state.state == CONNECTION_CONNECTED and not hard_blockers
    connection_blockers = production_blockers(
        include_connection=True, connection_state=state
    )
    catalog_item = next(
        (
            item
            for item in plugin_catalog().get("plugins", [])
            if item.get("name") == PWP_PLUGIN_ID
        ),
        {},
    )
    catalog_governance = catalog_item.get("governance", {})
    return {
        "plugin_id": PWP_PLUGIN_ID,
        "package": PWP_PACKAGE,
        "state": state.state,
        "connected": connected,
        "status": "connected"
        if connected
        else ("blocked" if hard_blockers else "disconnected"),
        "state_path": str(store.path),
        "manifest": manifest_summary(),
        "capabilities": [cap.to_dict() for cap in PWP_CAPABILITIES],
        "connect_points": [cap.connect_point for cap in PWP_CAPABILITIES],
        "disconnect_points": [cap.disconnect_behavior for cap in PWP_CAPABILITIES],
        "dashboard_surfaces": sorted(
            {cap.dashboard_surface for cap in PWP_CAPABILITIES}
        ),
        "tool_names": sorted({tool for cap in PWP_CAPABILITIES for tool in cap.tools}),
        "workflows": sorted({wf for cap in PWP_CAPABILITIES for wf in cap.workflows}),
        "governance": sorted(
            {rule for cap in PWP_CAPABILITIES for rule in cap.governance}
        ),
        "catalog_governance": catalog_governance,
        "risk_level": catalog_governance.get("risk_level", "low"),
        "approval_gates": catalog_governance.get("approval_gates", []),
        "policy_checks": catalog_governance.get("policy_checks", []),
        "surface_coverage": catalog_governance.get("surface_coverage", {}),
        "artifact_types": catalog_item.get("artifact_types", []),
        "lifecycle_summary": pwp_lifecycle_summary(),
        "cli": cli_status(),
        "production_blockers": blockers + connection_blockers[len(blockers) :],
        "connection": asdict(state),
        "generated_at": _now(),
    }


def pwp_lifecycle_summary(
    *,
    job_store: PluginJobStore | None = None,
    artifact_store: PluginArtifactStore | None = None,
) -> dict[str, Any]:
    """Return PWP-specific lifecycle history from the universal registries."""
    jobs = (job_store or job_store_from_env()).list_jobs(plugin_name=PWP_PLUGIN_ID)
    artifacts = (artifact_store or artifact_store_from_env()).list_artifacts(
        plugin_name=PWP_PLUGIN_ID
    )
    latest_job = jobs[0] if jobs else None
    latest_artifact = artifacts[0] if artifacts else None
    pending_approvals = [
        artifact
        for artifact in artifacts
        if artifact.get("approval_state") == "pending"
        or artifact.get("publish_state") == "draft"
    ]
    return {
        "reference_plugin": True,
        "lifecycle_steps": [
            "connect",
            "run_job",
            "produce_artifact",
            "register_provenance",
            "dashboard_history",
            "approval_before_publish",
            "safe_disconnect",
        ],
        "jobs_total": len(jobs),
        "artifacts_total": len(artifacts),
        "pending_approvals": len(pending_approvals),
        "latest_job": latest_job,
        "latest_artifact": latest_artifact,
        "history": {
            "jobs": jobs[:10],
            "artifacts": artifacts[:10],
        },
    }


def _write_reference_artifact(job_id: str, state_dir: Path | None = None) -> Path:
    """Write a tiny credential-free PWP reference artifact under PE state."""
    root = (state_dir or default_state_dir()) / "pwp" / "reference_lifecycle"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{job_id}.html"
    path.write_text(
        "\n".join(
            [
                "<!doctype html>",
                '<html lang="en">',
                "<head>",
                '  <meta charset="utf-8">',
                "  <title>PWP reference lifecycle artifact</title>",
                "</head>",
                "<body>",
                "  <main>",
                "    <h1>PWP reference lifecycle artifact</h1>",
                "    <p>This credential-free artifact demonstrates connect → job → provenance → approval → publish/export → safe disconnect.</p>",
                "  </main>",
                "</body>",
                "</html>",
            ]
        ),
        encoding="utf-8",
    )
    return path


def run_pwp_reference_lifecycle(
    *,
    actor: str = "pwp-reference-demo",
    disconnect_after: bool = True,
    store: PWPIntegrationStore | None = None,
    job_store: PluginJobStore | None = None,
    artifact_store: PluginArtifactStore | None = None,
) -> dict[str, Any]:
    """Run PWP through the full public reference plugin lifecycle.

    This is intentionally local-only and credential-free. It uses PE Core's
    universal job/artifact/provenance registries rather than a PWP-specific
    private state format, making PWP the canonical lifecycle example for future
    plugins.
    """
    store = store or PWPIntegrationStore()
    job_store = job_store or job_store_from_env()
    artifact_store = artifact_store or artifact_store_from_env()

    connect = connect_pwp(store)
    if not connect.get("connected"):
        return {
            "ok": False,
            "plugin_id": PWP_PLUGIN_ID,
            "failed_step": "connect",
            "connection": connect,
            "lifecycle_summary": pwp_lifecycle_summary(
                job_store=job_store, artifact_store=artifact_store
            ),
        }

    job = job_store.create_job(
        PWP_PLUGIN_ID,
        "pwp_reference_lifecycle_publish",
        actor=actor,
        source="pwp_reference_lifecycle",
        input_summary={
            "demo": "pwp full lifecycle reference",
            "expected_steps": [
                "connect",
                "run_job",
                "produce_artifact",
                "register_provenance",
                "approval_before_publish",
                "safe_disconnect",
            ],
        },
        metadata={"reference_plugin": True, "public_demo": True},
    )
    job_id = job["job_id"]
    steps: list[dict[str, Any]] = [
        {
            "step": "connect",
            "state": connect.get("state"),
            "ok": connect.get("connected"),
        }
    ]

    if job.get("approval_state") == "pending":
        job = (
            job_store.approve_job(
                job_id,
                actor=actor,
                note="Reference lifecycle approves queued PWP demo job",
            )
            or job
        )
    started, start_policy = job_store.start_job(
        job_id, actor=actor, message="PWP reference lifecycle job started"
    )
    job = started or job
    steps.append(
        {
            "step": "run_job",
            "job_id": job_id,
            "status": job.get("status"),
            "policy_decision": (start_policy or {}).get("decision"),
            "ok": job.get("status") == "running",
        }
    )

    artifact_path = _write_reference_artifact(job_id)
    artifact_payload = {
        "artifact_type": "text/html",
        "mime_type": "text/html",
        "path_or_url": str(artifact_path),
        "asset_id": f"pwp-reference-{job_id}",
        "metadata": {
            "title": "PWP reference lifecycle artifact",
            "dashboard_history": True,
            "requires_approval_before_publish": True,
        },
        "provenance": {
            "source_plugin": PWP_PLUGIN_ID,
            "source_job": job_id,
            "generated_by": "run_pwp_reference_lifecycle",
            "registry": "universal-plugin-artifacts",
        },
        "input_summary": {"source": "credential-free PWP lifecycle demo"},
        "provider_or_service": "pwp-local-reference",
        "approval_state": "pending",
        "publish_state": "draft",
    }
    job = (
        job_store.append_event(
            job_id,
            "artifact_emitted",
            actor=actor,
            source="pwp_reference_lifecycle",
            message="PWP reference artifact emitted and registered with provenance",
            artifact=artifact_payload,
        )
        or job
    )
    artifact_id = (job.get("artifact_ids") or [None])[-1]
    artifact = artifact_store.get_artifact(artifact_id) if artifact_id else None
    steps.append(
        {
            "step": "produce_artifact",
            "artifact_id": artifact_id,
            "path_or_url": str(artifact_path),
            "ok": bool(artifact),
        }
    )

    blocked_publish = (
        artifact_store.mark_publish_ready(
            artifact_id,
            actor=actor,
            note="Expected block before artifact approval",
        )
        if artifact_id
        else None
    )
    steps.append(
        {
            "step": "approval_before_publish",
            "artifact_id": artifact_id,
            "blocked_before_approval": (blocked_publish or {})
            .get("policy_result", {})
            .get("decision")
            != "allow",
            "policy_decision": (blocked_publish or {})
            .get("policy_result", {})
            .get("decision"),
        }
    )

    approved_artifact = (
        artifact_store.set_approval(
            artifact_id,
            "approved",
            actor=actor,
            note="Approved PWP reference artifact for publish-ready demo",
        )
        if artifact_id
        else None
    )
    publish_ready = (
        artifact_store.mark_publish_ready(
            artifact_id,
            actor=actor,
            note="Approved artifact may become publish-ready",
        )
        if artifact_id
        else None
    )
    export = (
        artifact_store.add_export(
            artifact_id,
            target="pwp-reference-demo://dashboard-history",
            actor=actor,
            note="Reference export demonstrates approved artifact export history",
        )
        if artifact_id
        else None
    )
    steps.append(
        {
            "step": "register_artifact_provenance",
            "artifact_id": artifact_id,
            "approval_state": (approved_artifact or {}).get("approval_state"),
            "publish_state": (publish_ready or {}).get("publish_state"),
            "export_allowed": bool(
                (export or {}).get("export_history", [{}])[-1].get("allowed")
            ),
            "ok": (publish_ready or {}).get("publish_state") == "publish_ready",
        }
    )

    completed = job_store.update_status(
        job_id,
        "completed",
        actor=actor,
        message="PWP reference lifecycle completed with approved artifact",
    )
    steps.append(
        {
            "step": "dashboard_history",
            "job_id": job_id,
            "status": (completed or {}).get("status"),
            "artifact_id": artifact_id,
            "ok": (completed or {}).get("status") == "completed",
        }
    )

    disconnect = (
        disconnect_pwp(store) if disconnect_after else integration_status(store=store)
    )
    steps.append(
        {
            "step": "safe_disconnect",
            "state": disconnect.get("state"),
            "artifacts_preserved": bool(
                artifact_id and artifact_store.get_artifact(artifact_id)
            ),
            "ok": disconnect.get("state") == CONNECTION_DISCONNECTED
            and bool(artifact_id and artifact_store.get_artifact(artifact_id)),
        }
    )

    lifecycle_summary = pwp_lifecycle_summary(
        job_store=job_store, artifact_store=artifact_store
    )
    ok = all(step.get("ok", True) for step in steps) and steps[3].get(
        "blocked_before_approval"
    )
    return {
        "ok": bool(ok),
        "plugin_id": PWP_PLUGIN_ID,
        "reference_plugin": True,
        "job_id": job_id,
        "artifact_id": artifact_id,
        "artifact_path": str(artifact_path),
        "steps": steps,
        "connection": disconnect,
        "job": job_store.get_job(job_id),
        "artifact": artifact_store.get_artifact(artifact_id) if artifact_id else None,
        "lifecycle_summary": lifecycle_summary,
    }


def connect_pwp(store: PWPIntegrationStore | None = None) -> dict[str, Any]:
    return (store or PWPIntegrationStore()).mutate("connect")


def disconnect_pwp(store: PWPIntegrationStore | None = None) -> dict[str, Any]:
    return (store or PWPIntegrationStore()).mutate("disconnect")


def refresh_pwp(store: PWPIntegrationStore | None = None) -> dict[str, Any]:
    return (store or PWPIntegrationStore()).mutate("refresh")
