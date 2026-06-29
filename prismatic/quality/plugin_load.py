"""Prismatic Quality Gates — Gap 13: Ship-Time Plugin Load Verification.

Layer 9 of the post-completion verification pipeline. Catches:

- Plugins with version constraints incompatible with the running core
- Plugins with missing or malformed manifests
- Plugins with broken entry_points (module can't be imported)
- Plugins rejected by capability / provider constraint validation
- Plugins that crash during on_init

This gate runs on the actual filesystem artifacts a PR ships. It
discovers every plugin under plugins/ and loads each through the real
``PluginLoader.scan_and_load_plugins()`` path. If any shipped plugin
fails to load in the current environment, the gate fails.

Reference: okf/operations/gap13-plugin-load-gate-spec-2026-06-29.md

Why a new module, not extension of smoke.py
--------------------------------------------
smoke.py verifies filesystem claims ("agent said 'I wrote X'" → does X
exist). That's a different concern from "does the shipped artifact
behave correctly at load time". Mixing them would conflate two
verification contracts.

This module uses the same dataclass + to_markdown() pattern as
SmokeTestResult so consumers can switch between them transparently.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


logger = logging.getLogger("prismatic.quality.plugin_load")


# ─────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────


@dataclass
class PluginLoadFinding:
    """One finding from the plugin load gate.

    status values:
        "loaded"             — plugin loaded successfully
        "version_mismatch"   — core_version_constraint excludes running core
        "missing_manifest"   — plugins/<name>/ exists but no plugin-manifest.yaml
        "broken_entry_point" — manifest entry_point can't be imported
        "capability_rejected"— required_capabilities not satisfied
        "provider_blocked"   — provider_constraints reject the runtime
        "on_init_crash"      — plugin loaded but on_init raised
        "unknown_error"      — anything else
    """

    plugin_name: str
    manifest_path: str
    status: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PluginLoadResult:
    """Result of the plugin load gate.

    passed is True iff every discovered shipped plugin appears in the
    loader's loaded_plugins after scan_and_load_plugins().
    """

    passed: bool
    plugins_dir: str
    core_version: str
    findings: list[PluginLoadFinding] = field(default_factory=list)
    loaded_count: int = 0
    failed_count: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "plugins_dir": self.plugins_dir,
            "core_version": self.core_version,
            "loaded_count": self.loaded_count,
            "failed_count": self.failed_count,
            "reason": self.reason,
            "findings": [f.to_dict() for f in self.findings],
        }

    def to_markdown(self) -> str:
        icon = "✅" if self.passed else "❌"
        lines = [
            f"## {icon} Plugin Load Gate: {'PASS' if self.passed else 'FAIL'}",
            "",
            f"**Plugins dir:** `{self.plugins_dir}`",
            f"**Core version:** `{self.core_version}`",
            f"**Loaded:** {self.loaded_count}",
            f"**Failed:** {self.failed_count}",
            f"**Reason:** {self.reason}",
            "",
        ]
        if self.findings:
            lines.append("### Findings")
            lines.append("")
            lines.append("| Plugin | Status | Detail |")
            lines.append("|---|---|---|")
            for f in self.findings:
                lines.append(f"| `{f.plugin_name}` | {f.status} | {f.detail} |")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# Core version discovery
# ─────────────────────────────────────────────────────────────────────


def read_core_version(repo_root: Path | None = None) -> str:
    """Read the engine version from pyproject.toml.

    Falls back to "0.0.0" if pyproject.toml is missing or malformed.
    Never raises — gate should fail with a clear message, not crash.
    """
    root = repo_root or _find_repo_root()
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return "0.0.0"
    try:
        text = pyproject.read_text()
        match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if match:
            return match.group(1)
    except OSError:
        pass
    return "0.0.0"


def _find_repo_root() -> Path:
    """Find the repo root by walking up looking for pyproject.toml.

    Falls back to current directory if not found.
    """
    cwd = Path.cwd()
    for candidate in [cwd, *cwd.parents]:
        if (candidate / "pyproject.toml").exists():
            return candidate
    return cwd


# ─────────────────────────────────────────────────────────────────────
# Plugin discovery
# ─────────────────────────────────────────────────────────────────────


def discover_shipped_plugins(plugins_dir: Path) -> list[Path]:
    """Find every plugin-manifest.yaml under plugins_dir/.

    Returns absolute paths to the manifest files. Subdirectories
    without a plugin-manifest.yaml are silently skipped (the loader
    will skip them too; the gate only checks plugins that LOOK like
    they want to ship).
    """
    if not plugins_dir.exists():
        return []
    manifests = []
    for entry in sorted(plugins_dir.iterdir()):
        if not entry.is_dir():
            continue
        manifest = entry / "plugin-manifest.yaml"
        if manifest.exists():
            manifests.append(manifest.resolve())
    return manifests


def _plugin_name_from_manifest(manifest_path: Path) -> str:
    """Extract the plugin name from the manifest, with a fallback."""
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(manifest_path.read_text())
        if isinstance(data, dict) and data.get("name"):
            return str(data["name"])
    except (ImportError, OSError):
        pass
    # Fallback: use the parent directory name
    return manifest_path.parent.name


# ─────────────────────────────────────────────────────────────────────
# The gate
# ─────────────────────────────────────────────────────────────────────


def verify_shipped_plugins_load(
    plugins_dir: Path | None = None,
    core_version: str | None = None,
    repo_root: Path | None = None,
) -> PluginLoadResult:
    """Load every shipped plugin via real PluginLoader and verify all loaded.

    Args:
        plugins_dir: Where to look for plugins. Defaults to
            ``<repo_root>/plugins/``.
        core_version: Engine version to validate against. Defaults to
            reading from pyproject.toml.
        repo_root: For ``core_version`` discovery. Defaults to walking
            up from CWD.

    Returns:
        PluginLoadResult with passed=True iff every shipped plugin
        appears in ``loader.loaded_plugins`` after
        ``scan_and_load_plugins``.
    """
    # Lazy imports — keep this module importable even if prismatic
    # internals aren't fully wired (e.g. during early bootstrap).
    try:
        from prismatic.core.registry import PluginLoader
        from prismatic.interface.plugin import PluginContext
    except ImportError as e:
        return PluginLoadResult(
            passed=False,
            plugins_dir=str(plugins_dir) if plugins_dir else "",
            core_version=core_version or "",
            reason=f"PluginLoader import failed: {e}",
            findings=[],
        )

    # Resolve defaults
    if repo_root is None:
        repo_root = _find_repo_root()
    if plugins_dir is None:
        plugins_dir = repo_root / "plugins"
    if core_version is None:
        core_version = read_core_version(repo_root)

    plugins_dir = Path(plugins_dir).resolve()
    if not plugins_dir.exists():
        return PluginLoadResult(
            passed=False,
            plugins_dir=str(plugins_dir),
            core_version=core_version,
            reason=f"plugins dir does not exist: {plugins_dir}",
            findings=[],
        )

    # Discover shipped plugin manifests
    manifests = discover_shipped_plugins(plugins_dir)
    if not manifests:
        return PluginLoadResult(
            passed=True,
            plugins_dir=str(plugins_dir),
            core_version=core_version,
            reason="no shipped plugins to verify",
            findings=[],
        )

    # Set up the loader
    loader = PluginLoader(
        core_version=core_version,
        plugins_dir=str(plugins_dir),
    )

    # Duck-typed context: PluginContext doesn't yet have review_registry
    # (that's a future Gap 11 follow-up). The hello-world plugin uses
    # getattr(context, 'review_registry', None) so this is safe.
    class _DuckContext(PluginContext):
        def __init__(self):
            super().__init__(config={}, db_connection=None, state_dir="/tmp")
            self.review_registry = None

    # Capture loader-side log records so we can map "plugin rejected"
    # back to the plugin that was rejected. The PluginLoader uses the
    # logger named "prismatic.loader" for both INFO success messages
    # and ERROR rejection messages. We capture both so we can match
    # plugin name + status from the captured stream.
    loader_log_records: list[tuple[str, str]] = []  # (level, message)
    loader_logger = logging.getLogger("prismatic.loader")

    class _CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            # Capture the rendered message AND any exception info.
            # The PluginLoader uses ``exc_info=True`` on rejection
            # errors, which means the exception type + message are in
            # record.exc_info but NOT in record.getMessage().
            msg = record.getMessage()
            if record.exc_info:
                exc_type, exc_value, _tb = record.exc_info
                if exc_type is not None and exc_value is not None:
                    msg = f"{msg} :: {exc_type.__name__}: {exc_value}"
            loader_log_records.append((record.levelname, msg))

    capture = _CaptureHandler(level=logging.DEBUG)
    loader_logger.addHandler(capture)
    try:
        loader.scan_and_load_plugins(_DuckContext())
    except Exception as e:
        loader_logger.removeHandler(capture)
        return PluginLoadResult(
            passed=False,
            plugins_dir=str(plugins_dir),
            core_version=core_version,
            reason=f"scan_and_load_plugins raised: {e}",
            findings=[],
        )
    finally:
        loader_logger.removeHandler(capture)

    # Compare discovered plugins to loaded plugins
    findings: list[PluginLoadFinding] = []
    loaded_names = set(loader.loaded_plugins.keys())

    # Build a name→module_alias map. The PluginLoader logs the
    # entry_point's first module segment (e.g. "buggy_plugin" for
    # entry_point "buggy_plugin.plugin:Class") rather than the manifest
    # name. So when matching log records, we try both the manifest
    # name AND the first module segment of the entry_point.
    name_to_module_alias: dict[str, str] = {}
    for m in manifests:
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(m.read_text())
            if isinstance(data, dict) and data.get("entry_point"):
                ep = str(data["entry_point"])
                # entry_point format: "module.path:ClassName"
                first_module = ep.split(":", 1)[0].split(".", 1)[0]
                name = str(data.get("name", m.parent.name))
                name_to_module_alias[name] = first_module
        except (ImportError, OSError):
            pass

    for manifest_path in manifests:
        name = _plugin_name_from_manifest(manifest_path)
        if name in loaded_names:
            findings.append(
                PluginLoadFinding(
                    plugin_name=name,
                    manifest_path=str(manifest_path),
                    status="loaded",
                    detail="loaded successfully",
                )
            )
        else:
            # Try the manifest name AND the entry_point module alias
            module_alias = name_to_module_alias.get(name, "")
            detail = _infer_failure_detail(name, loader_log_records)
            if not detail and module_alias:
                detail = _infer_failure_detail(module_alias, loader_log_records)
            status = _infer_failure_status(detail)
            findings.append(
                PluginLoadFinding(
                    plugin_name=name,
                    manifest_path=str(manifest_path),
                    status=status,
                    detail=detail or "no log record — check PluginLoader output",
                )
            )

    loaded_count = sum(1 for f in findings if f.status == "loaded")
    failed_count = len(findings) - loaded_count
    passed = failed_count == 0
    reason = (
        f"all {loaded_count} shipped plugins loaded successfully"
        if passed
        else f"{failed_count} of {len(findings)} shipped plugins failed to load"
    )

    return PluginLoadResult(
        passed=passed,
        plugins_dir=str(plugins_dir),
        core_version=core_version,
        findings=findings,
        loaded_count=loaded_count,
        failed_count=failed_count,
        reason=reason,
    )


def _infer_failure_detail(plugin_name: str, log_records: list[tuple[str, str]]) -> str:
    """Find the log record(s) that mention this plugin's failure.

    Looks for ERROR-level records first; if none, falls back to any
    record mentioning the plugin name. The PluginLoader logs
    ``Failed to load plugin from <entry_point_module>`` which doesn't
    include the manifest's ``name`` field — so we also fall back to
    matching the entry_point module name.
    """
    # First try ERROR-level records
    error_records = [
        msg for level, msg in log_records if level == "ERROR" and plugin_name in msg
    ]
    if error_records:
        return "; ".join(error_records[:3])

    # Then any level
    relevant = [msg for _, msg in log_records if plugin_name in msg]
    if relevant:
        return "; ".join(relevant[:3])

    return ""


def _infer_failure_status(detail: str) -> str:
    """Map a captured log message to a status enum value."""
    if not detail:
        return "unknown_error"
    detail_lower = detail.lower()
    if "does not satisfy constraint" in detail_lower or "core version" in detail_lower:
        return "version_mismatch"
    if "manifest" in detail_lower and (
        "missing" in detail_lower or "not found" in detail_lower
    ):
        return "missing_manifest"
    if (
        "entry_point" in detail_lower
        or "import" in detail_lower
        or "module" in detail_lower
    ):
        return "broken_entry_point"
    if "capability" in detail_lower:
        return "capability_rejected"
    if "provider" in detail_lower:
        return "provider_blocked"
    if "on_init" in detail_lower or "init" in detail_lower:
        return "on_init_crash"
    return "unknown_error"


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns 0 on pass, 1 on fail, 2 on bad args."""
    parser = argparse.ArgumentParser(
        description="Verify every shipped plugin loads in the current environment.",
    )
    parser.add_argument(
        "--plugins-dir",
        type=Path,
        default=None,
        help="Path to plugins/ directory (default: <repo_root>/plugins)",
    )
    parser.add_argument(
        "--core-version",
        default=None,
        help="Engine version to validate against (default: read from pyproject.toml)",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Path to repo root (default: walk up from CWD)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print the markdown report on FAILURE",
    )
    args = parser.parse_args(argv)

    result = verify_shipped_plugins_load(
        plugins_dir=args.plugins_dir,
        core_version=args.core_version,
        repo_root=args.repo_root,
    )

    if not args.quiet or not result.passed:
        print(result.to_markdown())

    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
