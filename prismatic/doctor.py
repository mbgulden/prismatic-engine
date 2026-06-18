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

import os
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
    status: str = "unknown"  # "connected" | "disconnected" | "auth_failed" | "skipped" | "n/a"
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
        }


@dataclass
class CapabilityReport:
    """Per-capability status: registered? check_status() result."""

    name: str
    status: str  # "ok" | "error" | "missing_registry"
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "message": self.message}


@dataclass
class DoctorReport:
    """Aggregated doctor report. The CLI layer iterates these."""

    system: SystemInfo = field(default_factory=SystemInfo)
    config: ConfigInfo = field(default_factory=ConfigInfo)
    providers: list[ProviderReport] = field(default_factory=list)
    capabilities: list[CapabilityReport] = field(default_factory=list)
    verdict: str = "OK"  # "OK" | "WARN" | "ERROR"

    def to_dict(self) -> dict[str, Any]:
        return {
            "system": self.system.__dict__,
            "config": self.config.__dict__,
            "providers": [p.to_dict() for p in self.providers],
            "capabilities": [c.to_dict() for c in self.capabilities],
            "verdict": self.verdict,
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
            report.remediation = "LinearTaskProvider failed to initialize; check LINEAR_API_KEY."
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
            reports.append(
                CapabilityReport(name=cap_name, status="missing_registry")
            )
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
            reports.append(
                CapabilityReport(name=cap_name, status="error", message=msg)
            )
    return reports


def _compute_verdict(
    providers: list[ProviderReport],
    capabilities: list[CapabilityReport],
) -> str:
    """Roll up the report verdict.

    ERROR: any provider is disconnected/auth_failed AND required for the
    golden flow (GitHub, Linear).
    WARN: any other provider is disconnected OR any capability is not ok.
    OK: everything green.
    """
    golden_required = {"github", "linear"}
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


def run_doctor(
    provider: str | None = None,
    capability_names: list[str] | None = None,
) -> DoctorReport:
    """Probe the engine, providers, and capabilities.

    Args:
        provider: If given, only that provider is probed. If ``None``,
            every provider is probed. Currently supports
            ``"github"`` and ``"linear"``; unknown names yield an
            empty providers list.
        capability_names: If given, only these capabilities are
            checked. Defaults to ``DEFAULT_CAPABILITY_NAMES``.

    Returns:
        A ``DoctorReport`` with the verdict, system info, config
        info, per-provider reports, and per-capability reports.

    The function is pure: no prints, no side effects, no I/O outside
    the provider's own verify calls. The CLI layer owns presentation.
    """
    if capability_names is None:
        capability_names = DEFAULT_CAPABILITY_NAMES

    report = DoctorReport()
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

    report.capabilities = _probe_capabilities(capability_names)
    report.verdict = _compute_verdict(report.providers, report.capabilities)
    return report
