"""
Prismatic Engine — Doctor CLI handler
======================================

CLI presentation layer for ``prismatic.doctor.run_doctor``. Reads a
``DoctorReport`` and prints the human-readable diagnostics page that
the legacy inline ``cmd_doctor`` produced. Returns an exit code.

This module owns the printed output. ``prismatic.doctor`` is pure;
this layer is the only place the byte-for-byte legacy format lives.

Backward compatibility contract: the output of this function MUST
match the output of the previous inline ``cmd_doctor`` for the
default no-args invocation, modulo any newly added provider sections.
The legacy test in ``tests/test_doctor_command.py`` enforces the
shape of the report; this handler's output is verified manually
and via the new ``tests/test_doctor_module.py`` smoke test.
"""

from __future__ import annotations

from typing import Any

from prismatic.doctor import DoctorReport, ProviderReport, run_doctor


# Exit codes
EXIT_OK = 0
EXIT_ERROR = 1


def _print_system(report: DoctorReport) -> None:
    """Print the [System] section."""
    s = report.system
    print(f"[System] Python version: {s.python_version}")
    print(f"[System] Git version: {s.git_version}")
    print(f"[System] gh CLI version: {s.gh_cli_version}")


def _print_config(report: DoctorReport) -> None:
    """Print the [Config] section."""
    c = report.config
    print()
    print(f"[Config] PRISMATIC_HOME: {c.prismatic_home}")
    print(
        f"[Config] User Config path: {c.user_config_path} "
        f"({'exists' if c.user_config_exists else 'missing'})"
    )
    print(
        f"[Config] Database path: {c.database_path} "
        f"({'exists' if c.database_exists else 'missing'})"
    )


def _print_github_provider(provider: ProviderReport) -> None:
    """Print the [GitHub] section for a single provider report."""
    print("\n[GitHub] Verifying GitHub API Connection...")
    print(f"  Credential Source: {provider.credential_source}")
    if provider.status == "disconnected":
        print("  Status: ✗ DISCONNECTED (No token discovered)")
        if provider.remediation:
            print(f"  Remediation: {provider.remediation}")
        print("  Workflow Impact: AGY/Jules CLI workflow is BLOCKED.")
        return
    if provider.status == "auth_failed":
        print("  Status: ✗ API AUTHENTICATION FAILED")
        if provider.error_detail:
            print(f"  Error Detail: {provider.error_detail}")
        if provider.api_message:
            print(f"  API Message: {provider.api_message}")
        print("  Workflow Impact: AGY/Jules CLI workflow is BLOCKED.")
        return
    if provider.status != "connected":
        # unknown / n/a / skipped — print a minimal line and bail
        print(f"  Status: {provider.status}")
        return

    # Connected
    print("  Status: ✓ CONNECTED")
    print(
        f"  API User: @{provider.user} ({provider.user_name or 'N/A'})"
    )
    print(
        f"  Available Scopes: "
        f"{', '.join(provider.scopes) if provider.scopes else 'none'}"
    )
    if provider.missing_scopes:
        print(
            f"  Warning: Missing recommended scopes: "
            f"{', '.join(provider.missing_scopes)}"
        )
        if provider.remediation:
            print(f"  Remediation: {provider.remediation}")
    else:
        print("  Token Scopes: ✓ SUFFICIENT for AGY + Jules workflow")

    # Target repository
    print(f"  Target Repository: {provider.target_repo or 'Not specified'}")
    if not provider.target_repo:
        print("  Repo Access: ✗ NOT CONFIGURED (No repo target discovered)")
        if provider.remediation:
            print(f"  Remediation: {provider.remediation}")
        return
    if provider.repo_access_verified:
        perms = provider.repo_permissions or {}
        p_str = (
            f"push={perms.get('push', False)}, "
            f"pull={perms.get('pull', False)}"
        )
        print(f"  Repo Access: ✓ VERIFIED ({p_str})")
    else:
        print("  Repo Access: ✗ FAILED to read repository info")
        if provider.error_detail:
            print(f"  Error Detail: {provider.error_detail}")
        if provider.api_message:
            print(f"  API Message: {provider.api_message}")
        print("  Workflow Impact: Cannot target repository.")


def _print_linear_provider(provider: ProviderReport) -> None:
    """Print the [Linear] section for a single provider report."""
    print("\n[Linear] Verifying Linear API Connection...")
    if provider.status == "disconnected":
        print(f"  Status: ✗ DISCONNECTED ({provider.credential_source})")
        if provider.remediation:
            print(f"  Remediation: {provider.remediation}")
        return
    if provider.status == "auth_failed":
        print(f"  Status: ✗ FAILED to connect: {provider.error_detail}")
        return
    print("  Status: ✓ CONNECTED (LINEAR_API_KEY set)")
    rate_info = getattr(provider, "rate_limit_info", None)
    if rate_info:
        remaining = rate_info.get("remaining", 2500.0)
        limit = rate_info.get("limit", 2500)
        consumed = rate_info.get("consumed", 0)
        pct = rate_info.get("utilization_pct", 0.0)
        print(f"  Rate Limit: {remaining:.1f}/{limit} tokens remaining ({consumed} consumed last hour, {pct:.2f}% utilization)")
    else:
        print("  Rate Limit: Unknown / not initialized")


def _print_capabilities(report: DoctorReport) -> None:
    """Print the [Capabilities] section."""
    print("\n[Capabilities] Verifying registered capabilities...")
    report_lines: list[str] = []
    for cap in report.capabilities:
        if cap.status == "ok":
            line = f"{cap.name}:ok"
        elif cap.status == "missing_registry":
            line = f"{cap.name}:missing_registry"
        else:
            line = f"{cap.name}:error ({cap.message})"
        print(f"  {line}")
        report_lines.append(line)

    # Comma-separated report line, preserved for backward compat with
    # scripts and tests that scan for the literal "Reports: " prefix.
    print(f"\nReports: {', '.join(report_lines)}")


def _print_header() -> None:
    """Print the doctor banner."""
    print("=========================================================")
    print("Prismatic Engine — Capability Status & Diagnostics (Doctor)")
    print("=========================================================")


def run(args: Any) -> int:
    """Entry point for the ``doctor`` subcommand.

    Args:
        args: argparse-like object. Recognized attributes:
            - ``provider`` (str | None): if set, only that provider
              is probed. If None, every provider is probed.

    Returns:
        Exit code: 0 on OK/WARN, 1 on ERROR.
    """
    provider_filter = getattr(args, "provider", None)
    report = run_doctor(provider=provider_filter)

    _print_header()
    _print_system(report)
    _print_config(report)

    # Provider sections in the legacy order
    for prov in report.providers:
        if prov.name == "github":
            _print_github_provider(prov)
        elif prov.name == "linear":
            _print_linear_provider(prov)

    _print_capabilities(report)

    print("\nDiagnostics complete.")
    return EXIT_ERROR if report.verdict == "ERROR" else EXIT_OK
