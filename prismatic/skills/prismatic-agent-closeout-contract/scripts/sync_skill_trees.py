#!/usr/bin/env python3
"""Safely synchronize the Prismatic closeout-contract skill trees.

Canonical source: ``.agents/skills/prismatic-agent-closeout-contract``.
Packaged mirror: ``prismatic/skills/prismatic-agent-closeout-contract``.

``--check`` is read-only and exits non-zero on any path/content/mode drift.
Default sync refuses source/target aliasing, copies through a temporary sibling,
and replaces the target only after the staged tree matches the source exactly.
"""

from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

SKILL_NAME = "prismatic-agent-closeout-contract"


def _repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    raise RuntimeError(f"repository root not found from {start}")


def _trees(root: Path) -> tuple[Path, Path]:
    source = (root / ".agents" / "skills" / SKILL_NAME).resolve()
    target = (root / "prismatic" / "skills" / SKILL_NAME).resolve()
    if source == target:
        raise RuntimeError(f"refusing aliased source/target: {source}")
    return source, target


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _inventory(root: Path) -> dict[str, tuple[str, int]]:
    if not root.is_dir():
        raise FileNotFoundError(root)
    inventory: dict[str, tuple[str, int]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise RuntimeError(f"symlinks are not allowed in skill tree: {path}")
        if path.is_file():
            inventory[relative] = ("file", _mode(path))
        elif path.is_dir():
            inventory[relative] = ("dir", _mode(path))
        else:
            raise RuntimeError(f"unsupported filesystem entry: {path}")
    return inventory


def _drift(source: Path, target: Path) -> list[str]:
    source_inventory = _inventory(source)
    try:
        target_inventory = _inventory(target)
    except FileNotFoundError:
        return [f"missing target tree: {target}"]
    errors: list[str] = []
    for relative in sorted(set(source_inventory) | set(target_inventory)):
        source_entry = source_inventory.get(relative)
        target_entry = target_inventory.get(relative)
        if source_entry != target_entry:
            errors.append(
                f"inventory mismatch {relative}: source={source_entry} target={target_entry}"
            )
            continue
        if source_entry and source_entry[0] == "file":
            if not filecmp.cmp(source / relative, target / relative, shallow=False):
                errors.append(f"content mismatch: {relative}")
    return errors


def check(source: Path, target: Path) -> None:
    errors = _drift(source, target)
    if errors:
        raise RuntimeError("skill-tree drift:\n  - " + "\n  - ".join(errors))


def sync(source: Path, target: Path) -> None:
    _inventory(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=f".{SKILL_NAME}-", dir=target.parent))
    try:
        shutil.rmtree(staged)
        shutil.copytree(source, staged, copy_function=shutil.copy2)
        check(source, staged)
        backup = target.with_name(f".{target.name}.previous-{os.getpid()}")
        if backup.exists():
            shutil.rmtree(backup)
        if target.exists():
            target.rename(backup)
        try:
            staged.rename(target)
            check(source, target)
        except Exception:
            if target.exists():
                shutil.rmtree(target)
            if backup.exists():
                backup.rename(target)
            raise
        else:
            if backup.exists():
                shutil.rmtree(backup)
    finally:
        if staged.exists():
            shutil.rmtree(staged)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify without mutation")
    args = parser.parse_args(argv)
    root = _repo_root(Path(__file__).resolve())
    source, target = _trees(root)
    if args.check:
        check(source, target)
        print("STATUS=SYNC_CHECK_PASS")
    else:
        sync(source, target)
        print("STATUS=SYNC_COMPLETE")
    print(f"SOURCE={source}")
    print(f"TARGET={target}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"STATUS=BLOCKED\nREASON={exc}", file=sys.stderr)
        raise SystemExit(1) from exc
