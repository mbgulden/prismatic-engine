"""
Prismatic Engine — Bolt-On Skill Manager
==========================================

Manage the skill marketplace: list, install, uninstall, info, and create.

Skills are self-contained agent capability packages stored in the
``skills/`` directory of the engine distribution, or in the user's
``~/.prismatic/skills/`` directory after installation.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


# ── Path resolution ──────────────────────────────────────────────────

def _engine_skills_dir() -> Path:
    """Return the built-in skills directory (shipped with the package)."""
    candidate = Path(__file__).resolve().parent / "skills"
    if candidate.is_dir():
        return candidate
    # Fallback: installed as egg/site-packages
    candidate2 = Path(sys.prefix) / "prismatic" / "skills"
    return candidate2 if candidate2.is_dir() else candidate


def _user_skills_dir() -> Path:
    """Return the user's installed skills directory (~/.prismatic/skills/)."""
    d = Path.home() / ".prismatic" / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Skill manifest loading ──────────────────────────────────────────

def _load_manifest(skill_dir: Path) -> dict[str, Any] | None:
    """Load and validate a skill manifest from *skill_dir*."""
    manifest_path = skill_dir / "manifest.yaml"
    if not manifest_path.is_file():
        return None
    try:
        raw = manifest_path.read_text(encoding="utf-8")
        if yaml is None:
            raise RuntimeError("PyYAML is required for skill management")
        data = yaml.safe_load(raw)
    except Exception:
        return None
    if not isinstance(data, dict) or "name" not in data:
        return None
    data["_path"] = str(skill_dir.resolve())
    return data


# ── Public API ──────────────────────────────────────────────────────

def list_skills(installed: bool = False) -> list[dict[str, Any]]:
    """Return all available (or installed) skills with their manifests.

    Parameters
    ----------
    installed :
        If True, list only skills in ``~/.prismatic/skills/``.
        Otherwise list all bundled skills.

    Returns
    -------
    list[dict]
        Each entry is the parsed manifest with an added ``_path`` key.
    """
    base = _user_skills_dir() if installed else _engine_skills_dir()
    results: list[dict[str, Any]] = []
    if not base.is_dir():
        return results
    for entry in sorted(base.iterdir()):
        if entry.is_dir():
            manifest = _load_manifest(entry)
            if manifest is not None:
                results.append(manifest)
    return results


def skill_info(name: str) -> dict[str, Any] | None:
    """Return manifest for a single skill by name.

    Checks installed directory first, then bundled skills.
    """
    for base in (_user_skills_dir(), _engine_skills_dir()):
        candidate = base / name
        if candidate.is_dir():
            manifest = _load_manifest(candidate)
            if manifest is not None:
                return manifest
    return None


def install_skill(name: str) -> bool:
    """Install a bundled skill into the user's skill directory.

    Returns True on success, False if the skill is not found or
    already installed.
    """
    src = _engine_skills_dir() / name
    if not src.is_dir():
        return False
    dst = _user_skills_dir() / name
    if dst.exists():
        return False  # already installed
    shutil.copytree(src, dst)

    # Run post-install hook if present
    hook = dst / "hooks" / "install.py"
    if hook.is_file():
        import runpy
        runpy.run_path(str(hook), init_globals={"skill_dir": str(dst)})

    return True


def uninstall_skill(name: str) -> bool:
    """Remove an installed skill.

    Returns True on success, False if not found.
    """
    dst = _user_skills_dir() / name
    if not dst.is_dir():
        return False

    # Run pre-uninstall hook if present
    hook = dst / "hooks" / "uninstall.py"
    if hook.is_file():
        import runpy
        runpy.run_path(str(hook), init_globals={"skill_dir": str(dst)})

    shutil.rmtree(dst)
    return True


def create_skill(name: str, *, force: bool = False) -> bool:
    """Scaffold a new skill under the user directory.

    Creates a minimal ``manifest.yaml``, ``README.md``, and
    ``templates/`` directory.

    Returns True on success.
    """
    dst = _user_skills_dir() / name
    if dst.exists():
        if not force:
            return False
        shutil.rmtree(dst)
    dst.mkdir(parents=True)

    # Minimal manifest
    manifest = {
        "name": name,
        "version": "0.1.0",
        "description": "Describe what this skill does",
        "author": "Your Name",
        "category": "custom",
        "labels": ["agent:custom"],
        "config": {},
        "dependencies": [],
    }
    manifest_text = yaml.safe_dump(manifest, sort_keys=False) if yaml else str(manifest)

    (dst / "manifest.yaml").write_text(manifest_text, encoding="utf-8")
    (dst / "README.md").write_text(
        f"# {name}\n\nDescribe how to use this skill.\n",
        encoding="utf-8",
    )
    (dst / "templates").mkdir()
    return True


# ── CLI subcommand ──────────────────────────────────────────────────

def _print_table(rows: list[list[str]]) -> None:
    """Print a simple aligned table to stdout."""
    if not rows:
        return
    widths = [max(len(str(c)) for c in col) for col in zip(*rows)]
    for i, row in enumerate(rows):
        line = "  ".join(str(c).ljust(w) for c, w in zip(row, widths))
        print(line)
        if i == 0:
            print("  ".join("-" * w for w in widths))


def cli_skills(args: list[str]) -> int:
    """Entry point for ``prismatic-engine skills <subcommand> ...``.

    Returns exit code (0 = success).
    """
    if not args or args[0] in ("-h", "--help", "help"):
        print("Usage: prismatic-engine skills <command> [options]")
        print()
        print("Commands:")
        print("  list                  List available (bundled) skills")
        print("  list --installed      List installed skills")
        print("  info <name>           Show skill manifest details")
        print("  install <name>        Install a bundled skill")
        print("  uninstall <name>      Remove an installed skill")
        print("  create <name>         Scaffold a new skill")
        return 0

    cmd = args[0]
    rest = args[1:]

    if cmd == "list":
        installed = "--installed" in rest
        skills = list_skills(installed=installed)
        if not skills:
            print("No skills found.")
            return 0
        header = ["Name", "Version", "Category", "Description"]
        rows = [header]
        for s in skills:
            rows.append([
                s.get("name", "?"),
                s.get("version", "?"),
                s.get("category", "?"),
                s.get("description", "")[:60],
            ])
        _print_table(rows)
        return 0

    if cmd == "info":
        if not rest:
            print("Usage: prismatic-engine skills info <name>")
            return 1
        name = rest[0]
        m = skill_info(name)
        if m is None:
            print(f"Skill '{name}' not found.")
            return 1
        for key in ("name", "version", "description", "author", "category", "labels", "_path"):
            if key in m:
                print(f"{key:20s}  {m[key]}")
        return 0

    if cmd == "install":
        if not rest:
            print("Usage: prismatic-engine skills install <name>")
            return 1
        name = rest[0]
        ok = install_skill(name)
        if ok:
            print(f"✅ Installed skill '{name}'")
        else:
            print(f"❌ Could not install '{name}' — not found or already installed")
        return 0 if ok else 1

    if cmd == "uninstall":
        if not rest:
            print("Usage: prismatic-engine skills uninstall <name>")
            return 1
        name = rest[0]
        ok = uninstall_skill(name)
        if ok:
            print(f"🗑️  Uninstalled skill '{name}'")
        else:
            print(f"❌ Could not uninstall '{name}' — not found")
        return 0 if ok else 1

    if cmd == "create":
        if not rest:
            print("Usage: prismatic-engine skills create <name> [--force]")
            return 1
        name = rest[0]
        force = "--force" in rest
        ok = create_skill(name, force=force)
        if ok:
            print(f"📦 Created skill scaffold at ~/.prismatic/skills/{name}/")
            print("  Edit manifest.yaml and README.md to get started.")
        else:
            print(f"❌ Skill '{name}' already exists (use --force to overwrite)")
        return 0 if ok else 1

    print(f"Unknown subcommand: {cmd}")
    print("Run 'prismatic-engine skills help' for usage.")
    return 1
