"""Workspace optimization for fresh Prismatic Engine installs.

The optimizer keeps freshly cloned workspaces small enough for agent dispatch by
creating context-ignore files and disabling known high-overhead AGY plugins.
It is intentionally stdlib-only so ``install.sh`` can call it immediately after
package installation.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

IGNORE_FILENAMES = (".gitignore", ".geminiignore", ".antigravityignore")
HIGH_OVERHEAD_PLUGINS = ("search-documents", "visualization-server")
MANAGED_BLOCK_START = "# >>> prismatic-engine optimize-workspace >>>"
MANAGED_BLOCK_END = "# <<< prismatic-engine optimize-workspace <<<"

IGNORE_PATTERNS = (
    "# VCS and generated metadata",
    ".git/",
    ".hg/",
    ".svn/",
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".tox/",
    ".nox/",
    "htmlcov/",
    ".coverage*",
    "",
    "# Dependency/build output",
    "node_modules/",
    ".venv/",
    "venv/",
    "env/",
    "dist/",
    "build/",
    ".next/",
    ".turbo/",
    "coverage/",
    "*.egg-info/",
    "",
    "# Local agent/runtime state that explodes dispatch context",
    ".antigravity/",
    ".gemini/",
    ".hermes/",
    ".cache/",
    "prismatic_state/",
    "*.db",
    "*.sqlite",
    "*.sqlite3",
    "*.log",
    "",
    "# Large media/binary outputs are not needed for first-run routing",
    "*.mp4",
    "*.mov",
    "*.webm",
    "*.wav",
    "*.mp3",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.webp",
)


@dataclass
class OptimizationResult:
    """Summary of a workspace optimization run."""

    workspace: Path
    ignore_files: list[Path] = field(default_factory=list)
    disabled_plugins: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "workspace": str(self.workspace),
            "ignore_files": [str(path) for path in self.ignore_files],
            "disabled_plugins": list(self.disabled_plugins),
            "warnings": list(self.warnings),
        }


def _managed_block() -> str:
    body = "\n".join(IGNORE_PATTERNS).rstrip()
    return f"{MANAGED_BLOCK_START}\n{body}\n{MANAGED_BLOCK_END}\n"


def _upsert_managed_block(path: Path, block: str) -> bool:
    """Create or replace the managed ignore block in *path*.

    Returns True when the file content changed.
    """

    original = path.read_text() if path.exists() else ""
    if MANAGED_BLOCK_START in original and MANAGED_BLOCK_END in original:
        before, rest = original.split(MANAGED_BLOCK_START, 1)
        _old, after = rest.split(MANAGED_BLOCK_END, 1)
        prefix = before.rstrip()
        suffix = after.lstrip("\n")
        updated = (prefix + "\n\n" if prefix else "") + block + suffix
    else:
        separator = "\n\n" if original.strip() else ""
        updated = original.rstrip() + separator + block

    if updated != original:
        path.write_text(updated)
        return True
    return False


def ensure_ignore_files(workspace: Path) -> list[Path]:
    """Ensure the three first-run ignore files exist in *workspace*."""

    workspace.mkdir(parents=True, exist_ok=True)
    block = _managed_block()
    touched: list[Path] = []
    for filename in IGNORE_FILENAMES:
        path = workspace / filename
        _upsert_managed_block(path, block)
        touched.append(path)
    return touched


def _strip_settings_permissions(settings_path: Path) -> list[str]:
    """Remove high-overhead plugin allow entries from AGY settings.json."""

    if not settings_path.exists():
        return []

    try:
        settings = json.loads(settings_path.read_text() or "{}")
    except json.JSONDecodeError:
        return []

    permissions = settings.get("permissions")
    if not isinstance(permissions, dict):
        return []

    allow = permissions.get("allow")
    if not isinstance(allow, list):
        return []

    removed: list[str] = []
    kept: list[Any] = []
    for item in allow:
        if isinstance(item, str) and any(
            plugin in item for plugin in HIGH_OVERHEAD_PLUGINS
        ):
            removed.append(item)
        else:
            kept.append(item)

    if removed:
        permissions["allow"] = kept
        settings["permissions"] = permissions
        settings_path.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n")
    return removed


def _try_disable_agy_plugins() -> list[str]:
    """Best-effort disable through the AGY plugin manager when available."""

    agy_bin = os.environ.get("AGY_BIN") or os.path.expanduser("~/.local/bin/agy-bin")
    if not Path(agy_bin).exists():
        return []

    disabled: list[str] = []
    for plugin in HIGH_OVERHEAD_PLUGINS:
        result = subprocess.run(
            [agy_bin, "plugin", "disable", plugin],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode == 0:
            disabled.append(plugin)
    return disabled


def disable_high_overhead_plugins(workspace: Path) -> list[str]:
    """Persist first-run plugin disables for known high-overhead AGY plugins."""

    disabled: set[str] = set(HIGH_OVERHEAD_PLUGINS)
    settings_path = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"
    stripped = _strip_settings_permissions(settings_path)
    disabled.update(item for item in stripped if item in HIGH_OVERHEAD_PLUGINS)
    disabled.update(_try_disable_agy_plugins())

    state_dir = workspace / ".prismatic"
    state_dir.mkdir(parents=True, exist_ok=True)
    manifest = state_dir / "disabled_plugins.json"
    manifest.write_text(
        json.dumps(
            {
                "disabled_plugins": sorted(disabled),
                "reason": "prismatic-engine optimize-workspace first-run token overhead guard",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return sorted(disabled)


def optimize_workspace(path: str | os.PathLike[str]) -> OptimizationResult:
    """Optimize *path* for low-overhead Prismatic Engine dispatch."""

    workspace = Path(path).expanduser().resolve()
    result = OptimizationResult(workspace=workspace)
    result.ignore_files = ensure_ignore_files(workspace)
    result.disabled_plugins = disable_high_overhead_plugins(workspace)
    return result


def main(argv: list[str] | None = None) -> int:
    """CLI entry point used by ``prismatic-engine optimize-workspace``."""

    import argparse

    parser = argparse.ArgumentParser(
        description="Optimize a Prismatic workspace for agent dispatch"
    )
    parser.add_argument(
        "workspace",
        nargs="?",
        default=os.environ.get("PRISMATIC_HOME", os.getcwd()),
        help="Workspace root to optimize (default: PRISMATIC_HOME or current directory)",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print machine-readable JSON"
    )
    args = parser.parse_args(argv)

    result = optimize_workspace(args.workspace)
    if args.json:
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    else:
        print(f"Optimized workspace: {result.workspace}")
        print("Ignore files:")
        for path in result.ignore_files:
            print(f"  - {path}")
        print("Disabled high-overhead plugins:")
        for plugin in result.disabled_plugins:
            print(f"  - {plugin}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
