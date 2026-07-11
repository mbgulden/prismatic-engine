# SPDX-License-Identifier: AGPL-3.0-only
"""Workspace optimization helpers for AGY/Prismatic sandboxes.

The optimizer reduces context-window inflation by writing ignore files that
exclude heavyweight dependency/build/cache directories from agent indexing.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

IGNORE_RULES = """# Prismatic Engine workspace optimizer
# Keeps agent file indexing focused on source and operating context.

# Dependencies
node_modules/
bower_components/
jspm_packages/

# Python environments
.venv/
venv/
env/
ENV/
ENV/pyvenv.cfg
__pycache__/
*.py[cod]

# Build outputs
dist/
build/
out/
.next/
.nuxt/
target/
bin/
obj/
coverage/
htmlcov/

# VCS and editor/system noise
.git/
.svn/
.hg/
.DS_Store
.idea/
.vscode/

# Caches
.cache/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.tox/
.nox/

# Logs and temp
*.log
*.tmp
.tmp/
tmp/
"""

IGNORE_FILES = (".antigravityignore", ".geminiignore", ".aiexclude")
HIGH_OVERHEAD_PLUGINS = ("search-documents", "visualization-server")


def _merge_ignore_rules(existing: str) -> tuple[str, bool]:
    """Append missing standard rules while preserving user edits."""
    changed = False
    lines = existing.splitlines()
    existing_rules = {
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    }

    missing: list[str] = []
    for line in IGNORE_RULES.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped not in existing_rules:
            missing.append(stripped)

    if not existing.strip():
        return IGNORE_RULES.rstrip() + "\n", True

    if missing:
        changed = True
        merged = (
            existing.rstrip()
            + "\n\n# Added by prismatic-engine optimize-workspace\n"
            + "\n".join(missing)
            + "\n"
        )
        return merged, changed

    return existing if existing.endswith("\n") else existing + "\n", False


def enforce_exclusions(workspace_root: Path) -> list[dict[str, Any]]:
    """Create/update standard ignore files under *workspace_root* idempotently."""
    workspace_root = workspace_root.expanduser().resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    for name in IGNORE_FILES:
        path = workspace_root / name
        existed = path.exists()
        old = path.read_text(encoding="utf-8") if existed else ""
        new, changed = _merge_ignore_rules(old)
        if changed or not existed or old != new:
            path.write_text(new, encoding="utf-8")
        results.append(
            {"file": str(path), "existed": existed, "changed": changed or not existed}
        )

    return results


def strip_unnecessary_plugins() -> list[dict[str, Any]]:
    """Best-effort disable of high-overhead AGY plugins.

    Missing ``agy`` binary or already-disabled plugins are non-fatal. The CLI
    returns JSON describing the attempted action instead of failing the whole
    workspace optimization.
    """
    results: list[dict[str, Any]] = []
    for plugin in HIGH_OVERHEAD_PLUGINS:
        try:
            proc = subprocess.run(
                ["agy", "plugin", "disable", plugin],
                check=False,
                text=True,
                capture_output=True,
                timeout=20,
            )
            graceful = (
                proc.returncode == 0 or "already" in (proc.stdout + proc.stderr).lower()
            )
            results.append(
                {
                    "plugin": plugin,
                    "attempted": True,
                    "returncode": proc.returncode,
                    "ok": graceful,
                    "stdout": proc.stdout.strip(),
                    "stderr": proc.stderr.strip(),
                }
            )
        except FileNotFoundError:
            results.append(
                {
                    "plugin": plugin,
                    "attempted": False,
                    "ok": True,
                    "reason": "agy executable not found; skipped",
                }
            )
        except Exception as exc:  # pragma: no cover - defensive reporting
            results.append(
                {"plugin": plugin, "attempted": True, "ok": False, "reason": str(exc)}
            )
    return results


def optimize_workspace(
    workspace_root: str | Path, *, disable_plugins: bool = True
) -> dict[str, Any]:
    """Optimize a workspace and return a machine-parseable result."""
    root = Path(workspace_root).expanduser().resolve()
    files = enforce_exclusions(root)
    plugins = strip_unnecessary_plugins() if disable_plugins else []
    ok = all(item.get("ok", True) for item in plugins)
    return {"ok": ok, "workspace": str(root), "ignore_files": files, "plugins": plugins}


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="prismatic-engine optimize-workspace")
    parser.add_argument("workspace", help="Workspace directory to optimize")
    parser.add_argument(
        "--no-plugin-disable", action="store_true", help="Only write ignore files"
    )
    args = parser.parse_args(argv)

    try:
        result = optimize_workspace(
            args.workspace, disable_plugins=not args.no_plugin_disable
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("ok") else 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        return 1
