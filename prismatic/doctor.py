"""
Prismatic Engine — Doctor (diagnostics core)
============================================

Pure-function diagnostic engine. Walks the capability registry, checks
each registered capability, and probes provider state. Returns a typed
``DoctorReport`` dataclass that the CLI handler in
``prismatic.cli.doctor`` formats for human display.

Design contract:

- No ``print`` calls in this module. The CLI layer owns presentation.
- No state in this module — every call is a fresh probe.
- No side effects (no Linear comments, no Telegram pings, no
  filesystem writes). The doctor is *observation only*.
- The shape of the report is stable across versions; new sections
  are added as fields with default values, never by removing or
  renaming existing fields.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Typed report ─────────────────────────────────────────────────────


@dataclass
class SystemInfo:
    """System-level diagnostics (Python, git, gh CLI versions)."""

    python_version: str = ""
    git_version: str = ""
    gh_cli_version: str = ""


@dataclass
class ConfigInfo:
    """Path diagnostics for PRISMATIC_HOME, config.yaml, event router DB."""

    prismatic_home: str = ""
    user_config_path: str = ""
    user_config_exists: bool = False
    database_path: str = ""
    database_exists: bool = False


@dataclass
class ProviderReport:
    """Per-provider status: credential discovery, auth, scope check, repo access."""

    name: str
    credential_source: str = "Not found"
    status: str = (
        "unknown"  # "connected" | "disconnected" | "auth_failed" | "skipped" | "n/a"
    )
    user: str = ""
    user_name: str = ""
    scopes: list[str] = field(default_factory=list)
    missing_scopes: list[str] = field(default_factory=list)
    target_repo: str = ""
    repo_access_verified: bool = False
    repo_permissions: dict[str, bool] = field(default_factory=dict)
    error_detail: str = ""
    api_message: str = ""
    remediation: str = ""
    rate_limit_info: dict[str, Any] = field(default_factory=dict)
    required: bool = False
    role: str = "optional_transport"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "credential_source": self.credential_source,
            "status": self.status,
            "user": self.user,
            "user_name": self.user_name,
            "scopes": self.scopes,
            "missing_scopes": self.missing_scopes,
            "target_repo": self.target_repo,
            "repo_access_verified": self.repo_access_verified,
            "repo_permissions": self.repo_permissions,
            "error_detail": self.error_detail,
            "api_message": self.api_message,
            "remediation": self.remediation,
            "rate_limit_info": self.rate_limit_info,
            "required": self.required,
            "role": self.role,
        }


@dataclass
class CapabilityReport:
    """Per-capability status: registered? check_status() result."""

    name: str
    status: str  # "ok" | "error" | "missing_registry"
    message: str = ""
    required: bool = False
    role: str = "optional_capability"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "required": self.required,
            "role": self.role,
        }


@dataclass
class DoctorReport:
    """Aggregated doctor report. The CLI layer iterates these."""

    system: SystemInfo = field(default_factory=SystemInfo)
    config: ConfigInfo = field(default_factory=ConfigInfo)
    providers: list[ProviderReport] = field(default_factory=list)
    capabilities: list[CapabilityReport] = field(default_factory=list)
    native_components: list[CapabilityReport] = field(default_factory=list)
    verdict: str = "OK"  # "OK" | "WARN" | "ERROR"
    acceptance_authority: str = "native_provider_neutral_receipt"
    required_providers: list[str] = field(default_factory=list)
    hosted_ci_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "system": self.system.__dict__,
            "config": self.config.__dict__,
            "providers": [p.to_dict() for p in self.providers],
            "capabilities": [c.to_dict() for c in self.capabilities],
            "native_components": [c.to_dict() for c in self.native_components],
            "verdict": self.verdict,
            "acceptance_authority": self.acceptance_authority,
            "required_providers": self.required_providers,
            "hosted_ci_required": self.hosted_ci_required,
        }


# ── Probes ───────────────────────────────────────────────────────────


def _probe_system() -> SystemInfo:
    """Detect Python, git, and gh CLI versions."""
    info = SystemInfo()
    info.python_version = sys.version.split()[0]
    try:
        res = subprocess.run(
            ["git", "--version"], capture_output=True, text=True, check=False
        )
        info.git_version = res.stdout.strip() if res.returncode == 0 else "Not found"
    except Exception:
        info.git_version = "Error executing"
    try:
        res = subprocess.run(
            ["gh", "--version"], capture_output=True, text=True, check=False
        )
        info.gh_cli_version = (
            res.stdout.splitlines()[0]
            if (res.returncode == 0 and res.stdout)
            else "Not found"
        )
    except Exception:
        info.gh_cli_version = "Not found"
    return info


def _probe_config_paths() -> tuple[Path, Path, Path]:
    """Resolve PRISMATIC_HOME, user_config_path, database_path.

    Returns the three paths as Path objects. The caller wraps them in
    a ``ConfigInfo``.
    """
    prismatic_home = Path(os.environ.get("PRISMATIC_HOME", os.path.expanduser("~")))
    config_dir = prismatic_home / ".prismatic"
    user_config_path = config_dir / "config.yaml"
    db_path = config_dir / "db" / "event_router.db"
    return prismatic_home, user_config_path, db_path


def _probe_github(user_config_path: Path) -> ProviderReport:
    """Probe the GitHub provider. Returns a populated ProviderReport."""
    report = ProviderReport(name="github")
    report.credential_source = "Not found"

    if os.environ.get("GITHUB_TOKEN"):
        report.credential_source = "GITHUB_TOKEN env var"
    elif os.environ.get("GH_TOKEN"):
        report.credential_source = "GH_TOKEN env var"
    elif os.environ.get("PRISMATIC_GITHUB_TOKEN"):
        report.credential_source = "PRISMATIC_GITHUB_TOKEN env var"
    elif user_config_path.exists():
        try:
            import yaml

            with open(user_config_path) as f:
                cfg = yaml.safe_load(f) or {}
            if cfg.get("github", {}).get("token"):
                report.credential_source = "config.yaml (github.token)"
        except Exception:
            pass

    if report.credential_source == "Not found":
        try:
            res = subprocess.run(
                ["gh", "auth", "token"], capture_output=True, text=True, check=False
            )
            if res.returncode == 0 and res.stdout.strip():
                report.credential_source = "gh CLI authentication"
        except Exception:
            pass

    # Lazy import to keep the doctor fast when the provider is missing.
    from prismatic.providers.github import GitHubProvider

    provider = GitHubProvider()
    if not provider.has_credentials():
        report.status = "disconnected"
        report.remediation = (
            "Please export GITHUB_TOKEN or set github.token in config.yaml."
        )
        return report

    success, user_info, scopes = provider.verify_auth()
    if not success:
        report.status = "auth_failed"
        report.error_detail = user_info.get("error", "")
        if "detail" in user_info:
            report.api_message = user_info["detail"].get("message", "")
        report.remediation = "Verify GITHUB_TOKEN is valid and has the required scopes."
        return report

    report.status = "connected"
    report.user = user_info.get("login", "")
    report.user_name = user_info.get("name") or "N/A"
    report.scopes = list(scopes) if scopes else []

    required = {"repo"}
    missing = required - set(report.scopes)
    if missing:
        report.missing_scopes = sorted(missing)
        report.remediation = (
            f"Update GITHUB_TOKEN to include '{', '.join(missing)}' scope(s)."
        )
    else:
        report.missing_scopes = []

    # Target repository access check
    report.target_repo = provider.repo or ""
    if not report.target_repo:
        report.repo_access_verified = False
        if not report.remediation:
            report.remediation = (
                "Set GITHUB_REPOSITORY env or run in a repository with a remote."
            )
    else:
        ok_access, repo_info = provider.verify_repo_access(report.target_repo)
        if ok_access:
            report.repo_access_verified = True
            report.repo_permissions = dict(repo_info.get("permissions", {}))
        else:
            report.repo_access_verified = False
            report.error_detail = repo_info.get("error", "")
            if "detail" in repo_info:
                report.api_message = repo_info["detail"].get("message", "")

    return report


def _probe_linear() -> ProviderReport:
    """Probe the Linear provider. Returns a populated ProviderReport."""
    report = ProviderReport(name="linear")
    linear_token = os.environ.get("LINEAR_API_KEY", "")
    if not linear_token:
        report.status = "disconnected"
        report.credential_source = "LINEAR_API_KEY env var (missing)"
        report.remediation = "Set LINEAR_API_KEY in the environment."
        return report

    report.credential_source = "LINEAR_API_KEY env var"
    try:
        from prismatic.providers.tasks.linear import LinearTaskProvider

        linear_p = LinearTaskProvider()
        if getattr(linear_p, "_api_key", None):
            report.status = "connected"
            try:
                from prismatic.linear.budget import linear_budget

                util = linear_budget.get_current_utilization("prismatic.dispatcher")
                report.rate_limit_info = {
                    "remaining": util["current_tokens"],
                    "limit": util["hourly_rate_limit"],
                    "consumed": util["consumed_last_hour"],
                    "utilization_pct": util["utilization_percentage"],
                }
            except Exception:
                pass
        else:
            report.status = "disconnected"
            report.remediation = (
                "LinearTaskProvider failed to initialize; check LINEAR_API_KEY."
            )
    except Exception as exc:
        report.status = "auth_failed"
        report.error_detail = str(exc)
    return report


def _probe_capabilities(names: list[str] | None = None) -> list[CapabilityReport]:
    """Walk the capability registry and check each named capability.

    Default order matches the legacy cmd_doctor: linear, vcs.github,
    agy, jules, telegram. Operators can pass a custom list (e.g. for
    a doctor that only checks AGY-related capabilities).
    """
    from prismatic.capabilities import registry

    if names is None:
        names = ["linear", "vcs.github", "agy", "jules", "telegram"]

    reports: list[CapabilityReport] = []
    for cap_name in names:
        cap = registry.get(cap_name)
        if cap is None:
            reports.append(CapabilityReport(name=cap_name, status="missing_registry"))
            continue
        try:
            ok, msg = cap.check_status()
        except Exception as exc:  # pragma: no cover - defensive
            reports.append(
                CapabilityReport(
                    name=cap_name, status="error", message=f"check_status raised: {exc}"
                )
            )
            continue
        if ok:
            reports.append(CapabilityReport(name=cap_name, status="ok", message=msg))
        else:
            reports.append(CapabilityReport(name=cap_name, status="error", message=msg))
    return reports


def _probe_canonical_consumer(package_root: Path) -> tuple[bool, str]:
    """Inspect the configured runtime inventory for the exact one-shot consumer."""
    configured_path = os.environ.get("PRISMATIC_RUNTIME_SERVICES_CONFIG")
    if configured_path:
        manifest_path = Path(configured_path)
        if not manifest_path.is_absolute():
            return False, "PRISMATIC_RUNTIME_SERVICES_CONFIG must be absolute"
    else:
        manifest_path = package_root.parent / "config" / "runtime-services.json"
    if not manifest_path.is_file():
        return (
            False,
            f"runtime service inventory missing at {manifest_path}; installed-wheel runtimes must set PRISMATIC_RUNTIME_SERVICES_CONFIG",
        )
    try:
        import json

        with manifest_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        return False, f"runtime service inventory invalid JSON: {exc}"
    if (
        not isinstance(data, dict)
        or type(data.get("schema_version")) is not int
        or data["schema_version"] != 1
        or not isinstance(data.get("components"), list)
    ):
        return False, "runtime service inventory structure invalid"
    consumers = [
        component
        for component in data["components"]
        if isinstance(component, dict) and component.get("id") == "consumer"
    ]
    if len(consumers) != 1:
        return False, "runtime service inventory must contain exactly one consumer"
    consumer = consumers[0]
    expected_consumer = {
        "id": "consumer",
        "owner": "prismatic-engine",
        "project": "prismatic-engine",
        "deployment_mode": "immutable-release",
        "release_binding": "engine",
        "separately_versioned": False,
        "release_path_template": "/home/ubuntu/.prismatic/releases/{release_id}",
        "virtualenv_path_template": "/home/ubuntu/.prismatic/releases/{release_id}/.venv",
        "executable_path": "/home/ubuntu/.prismatic/releases/{release_id}/.venv/bin/python3",
        "module_path": "prismatic.task_admission_consumer",
        "source_path": "/home/ubuntu/.prismatic/releases/{release_id}/prismatic/task_admission_consumer.py",
        "working_directory": "/home/ubuntu/.prismatic/releases/{release_id}",
        "import_paths": ["/home/ubuntu/.prismatic/releases/{release_id}"],
        "state_paths": [
            "/home/ubuntu/.prismatic/bus/event_log.sqlite",
            "/home/ubuntu/.prismatic/config/task_admission_policy.json",
            "/home/ubuntu/.prismatic/config/task_admission_launchers.json",
        ],
        "environment_files": ["/home/ubuntu/.prismatic/env.d/consumer.env"],
    }
    if set(consumer) != set(expected_consumer):
        missing = sorted(set(expected_consumer) - set(consumer))
        unexpected = sorted(set(consumer) - set(expected_consumer))
        return (
            False,
            f"declared consumer fields differ from canonical inventory (missing={missing}, unexpected={unexpected})",
        )
    for field_name, expected in expected_consumer.items():
        actual = consumer[field_name]
        if type(actual) is not type(expected) or actual != expected:
            return (
                False,
                f"declared consumer has non-canonical {field_name}: {actual!r}",
            )
    return (
        True,
        "cap-1 task-admission one-shot consumer declared as canonical runtime service",
    )


def _is_canonical_one_shot_exec(command: str) -> bool:
    """Accept only the exact Python module argv and known consumer CLI options."""
    if not command or command.count("argv[]=") > 1:
        return False
    argv_text = command
    if "argv[]=" in command:
        argv_text = command.split("argv[]=", 1)[1].split(" ;", 1)[0].strip()
    try:
        argv = shlex.split(argv_text)
    except ValueError:
        return False
    if (
        len(argv) < 3
        or re.fullmatch(r"python(?:3(?:\.\d+)?)?", Path(argv[0]).name) is None
    ):
        return False
    if argv[1:3] != ["-m", "prismatic.task_admission_consumer"]:
        return False
    allowed_options = {"--db", "--policy", "--launcher-config", "--identity"}
    seen: set[str] = set()
    index = 3
    while index < len(argv):
        option = argv[index]
        if option not in allowed_options or option in seen or index + 1 >= len(argv):
            return False
        value = argv[index + 1]
        if not value or value.startswith("--"):
            return False
        seen.add(option)
        index += 2
    return True


def _probe_legacy_consumer_service() -> tuple[bool, str]:
    """Inspect the legacy unit's state and command with strict systemd parsing."""
    try:
        result = subprocess.run(
            [
                "systemctl",
                "show",
                "prismatic-consumer.service",
                "--property=LoadState",
                "--property=ActiveState",
                "--property=UnitFileState",
                "--property=ExecStart",
                "--no-pager",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        return (
            False,
            f"systemd inspection support unavailable: unable to inspect prismatic-consumer.service ({exc})",
        )
    if result.returncode != 0:
        return (
            False,
            f"systemd inspection support unavailable: systemctl show failed ({result.stderr.strip() or 'no detail'})",
        )
    properties: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in properties:
            return (
                False,
                f"systemd inspection support unavailable: duplicate {key} property",
            )
        properties[key] = value.strip()
    required = {"LoadState", "ActiveState", "UnitFileState", "ExecStart"}
    if set(properties) != required:
        missing = (
            ", ".join(sorted(required - set(properties))) or "unexpected properties"
        )
        return (
            False,
            f"systemd inspection support unavailable: incomplete properties ({missing})",
        )
    load_state = properties["LoadState"].lower()
    active_state = properties["ActiveState"].lower()
    unit_state = properties["UnitFileState"].lower()
    canonical_one_shot = _is_canonical_one_shot_exec(properties["ExecStart"])
    if (
        load_state == "not-found"
        and active_state == "inactive"
        and unit_state
        in {
            "disabled",
            "not-found",
        }
    ):
        return True, "legacy prismatic-consumer.service is absent"
    if load_state == "masked" and active_state == "inactive" and unit_state == "masked":
        return True, "legacy prismatic-consumer.service is safely inactive and masked"
    if (
        load_state == "loaded"
        and active_state == "inactive"
        and unit_state == "disabled"
        and canonical_one_shot
    ):
        return (
            True,
            "prismatic-consumer.service is inactive/disabled and bound to the canonical one-shot consumer",
        )
    return (
        False,
        "prismatic-consumer.service is not safely contained "
        f"(load={load_state or 'missing'}, active={active_state or 'missing'}, "
        f"unit={unit_state or 'missing'}, command={'canonical-one-shot' if canonical_one_shot else 'legacy-or-unknown'})",
    )


def _probe_native_components() -> list[CapabilityReport]:
    """Probe required provider-neutral control-plane contracts without writes."""

    package_root = Path(__file__).resolve().parent
    from prismatic.verification.receipt_store import (
        verification_receipt_store_path,
        verification_revocation_store_path,
    )

    receipt_db = verification_receipt_store_path()
    revocation_store = verification_revocation_store_path()
    revocation_state_ready = not receipt_db.exists() or (
        revocation_store.is_file() and not revocation_store.is_symlink()
    )

    canonical_consumer_ok, canonical_consumer_msg = _probe_canonical_consumer(
        package_root
    )
    legacy_service_ok, legacy_service_msg = _probe_legacy_consumer_service()

    checks = [
        (
            "native.receipt_store",
            importlib.util.find_spec("prismatic.verification.receipt_store")
            is not None,
            "immutable receipt store and native acceptance read model",
        ),
        (
            "native.revocation_state",
            revocation_state_ready,
            "fail-closed revocation state beside an existing receipt database",
        ),
        (
            "native.dashboard",
            (package_root / "gateway" / "templates" / "dashboard.html").is_file(),
            "canonical dashboard artifact",
        ),
        (
            "native.event_queue",
            importlib.util.find_spec("prismatic.agent_raw_output_queue") is not None,
            "durable raw-output event queue",
        ),
        (
            "native.exact_tree_verifier",
            importlib.util.find_spec("prismatic.verification.source_acquisition")
            is not None,
            "exact source acquisition plus native Git binding verifier",
        ),
        (
            "native.release_verifier",
            importlib.util.find_spec("prismatic.merge_candidate_manifest") is not None,
            "release evidence contract",
        ),
        (
            "native.production_health_contract",
            (package_root / "gateway" / "server.py").is_file(),
            "production health API contract; live health not claimed",
        ),
        (
            "native.canonical_consumer",
            canonical_consumer_ok,
            canonical_consumer_msg,
        ),
        (
            "native.legacy_consumer_service",
            legacy_service_ok,
            legacy_service_msg,
        ),
    ]
    return [
        CapabilityReport(
            name=name,
            status="ok" if available else "error",
            message=message
            if available
            or message.startswith(
                ("declared", "legacy", "systemd", "consumer", "runtime")
            )
            else f"missing required {message}",
            required=True,
            role="native_required",
        )
        for name, available, message in checks
    ]


def _compute_verdict(
    providers: list[ProviderReport],
    capabilities: list[CapabilityReport],
    required_providers: set[str] | None = None,
    native_components: list[CapabilityReport] | None = None,
) -> str:
    """Roll up the report verdict.

    ERROR: a required native component fails, or a provider explicitly required
    by local policy is disconnected/authentication failed.
    WARN: any optional provider is disconnected OR any optional capability is not ok.
    OK: required native components and observed optional signals are green.
    """
    for component in native_components or []:
        if component.required and component.status != "ok":
            return "ERROR"
    golden_required = required_providers or set()
    for prov in providers:
        if prov.name in golden_required and prov.status in (
            "disconnected",
            "auth_failed",
        ):
            return "ERROR"
    for prov in providers:
        if prov.status in ("disconnected", "auth_failed"):
            return "WARN"
    for cap in capabilities:
        if cap.status != "ok":
            return "WARN"
    return "OK"


# ── Public API ───────────────────────────────────────────────────────


# Capability names the doctor probes by default. Kept as a module-level
# constant so the CLI layer and tests can reference the canonical order
# without duplicating the string list.
DEFAULT_CAPABILITY_NAMES = ["linear", "vcs.github", "agy", "jules", "telegram"]


def _required_providers_from_environment() -> set[str]:
    """Return explicit provider requirements; default is provider-neutral."""

    raw = os.environ.get("PRISMATIC_REQUIRED_PROVIDERS", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def run_doctor(
    provider: str | None = None,
    capability_names: list[str] | None = None,
    required_providers: set[str] | None = None,
) -> DoctorReport:
    """Probe the engine, providers, and capabilities.

    Args:
        provider: If given, only that provider is probed. If ``None``,
            every provider is probed. Currently supports
            ``"github"`` and ``"linear"``; unknown names yield an
            empty providers list.
        capability_names: If given, only these capabilities are
            checked. Defaults to ``DEFAULT_CAPABILITY_NAMES``.
        required_providers: Explicit provider names that can produce ERROR.
            When omitted, ``PRISMATIC_REQUIRED_PROVIDERS`` is used; its
            default is empty so hosted providers remain optional transport.

    Returns:
        A ``DoctorReport`` with the verdict, system info, config
        info, per-provider reports, and per-capability reports.

    The function is pure: no prints, no side effects, no I/O outside
    the provider's own verify calls. The CLI layer owns presentation.
    """
    if capability_names is None:
        capability_names = DEFAULT_CAPABILITY_NAMES

    explicit_required = (
        _required_providers_from_environment()
        if required_providers is None
        else {item.lower() for item in required_providers}
    )
    report = DoctorReport(required_providers=sorted(explicit_required))
    report.system = _probe_system()

    prismatic_home, user_config_path, db_path = _probe_config_paths()
    report.config = ConfigInfo(
        prismatic_home=str(prismatic_home),
        user_config_path=str(user_config_path),
        user_config_exists=user_config_path.exists(),
        database_path=str(db_path),
        database_exists=db_path.exists(),
    )

    if not provider or provider.lower() == "github":
        report.providers.append(_probe_github(user_config_path))
    if not provider or provider.lower() == "linear":
        report.providers.append(_probe_linear())

    for provider_report in report.providers:
        provider_report.required = provider_report.name in explicit_required
        provider_report.role = (
            "explicit_required_provider"
            if provider_report.required
            else "optional_transport"
        )

    report.capabilities = _probe_capabilities(capability_names)
    report.native_components = _probe_native_components()
    report.verdict = _compute_verdict(
        report.providers,
        report.capabilities,
        explicit_required,
        report.native_components,
    )
    return report
