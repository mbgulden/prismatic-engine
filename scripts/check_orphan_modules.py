#!/usr/bin/env python3
"""Recurring orphan-module check (report-only).

Replicates the Sep 2026 wiring audit (PR #505) that found merged-but-never-called
modules: it builds the static import graph over ``prismatic/`` and reports every
module that nothing else imports.

A module is NOT an orphan when any of the following hold:
  - it is a package ``__init__.py`` of an imported package;
  - it is a test file (``tests/`` dirs, ``test_*.py`` / ``*_test.py``);
  - it has an ``if __name__ == "__main__":`` entry-point block;
  - another module references its dotted path as a string literal
    (covers importlib / dynamic loading);
  - it is listed in ORPHAN_ALLOWLIST below (entry points by design).

This script is REPORT-ONLY: it always exits 0. CI uploads the report as an
artifact; a human decides whether a flagged module should be wired, extracted,
or removed. Pass ``--strict`` to exit 1 when orphans are found (for local use).
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import sys
from pathlib import Path

# Entry points by design (wired via manifest/config/CLI/discovery, not via
# static import). Exact relative paths or fnmatch globs. Keep sorted.
ORPHAN_ALLOWLIST: dict[str, str] = {
    # plugin-manifest entry points (prismatic/quality/plugin_load.py loads these
    # dynamically from plugin-manifest.yaml "entry_point")
    "prismatic/shipped_plugins/*/plugin.py": "manifest-declared plugin entry point",
    "prismatic/shipped_plugins/*/dashboard/plugin_api.py": "dashboard plugin API entry point",
}

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "prismatic"
# Entrypoint dirs whose static imports also count as "wired"
# (repo convention: scripts/ hold runnable wrappers around prismatic/).
IMPORTER_DIRS = [PKG, ROOT / "scripts"]


def dotted(path: Path) -> str:
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def iter_modules() -> list[Path]:
    return sorted(PKG.rglob("*.py"))


def imports_of(path: Path, modname: str) -> set[str]:
    """Static imports made by this module, resolved to absolute dotted names."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    found: set[str] = set()
    # for __init__.py, level-1 relative imports mean the package itself;
    # for regular modules they mean the containing package.
    pkg = modname if path.name == "__init__.py" else modname.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                found.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg
                for _ in range(node.level - 1):
                    base = base.rpartition(".")[0]
                name = (base + "." + (node.module or "")).strip(".")
            else:
                name = node.module or ""
            if name:
                found.add(name)
                for a in node.names:
                    if a.name != "*":
                        found.add(name + "." + a.name)
    return found


def has_main_block(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = ast.dump(node.test)
            if '__name__' in test and '__main__' in test:
                return True
    return False


def string_refs(path: Path) -> set[str]:
    """Dotted paths appearing as string literals (dynamic-loading heuristic)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    refs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if v.startswith("prismatic.") and len(v) > 10:
                refs.add(v)
    return refs


def is_test(path: Path) -> bool:
    return (
        "tests" in path.parts
        or path.name.startswith("test_")
        or path.name.endswith("_test.py")
        or path.name == "conftest.py"
    )


def allowlisted(rel: str) -> str | None:
    for pat, reason in ORPHAN_ALLOWLIST.items():
        if fnmatch.fnmatch(rel, pat):
            return reason
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 when orphan candidates are found")
    args = ap.parse_args()

    modules = [p for p in iter_modules() if not is_test(p)]
    modnames = {dotted(p): p for p in modules}

    importers: list[Path] = []
    for d in IMPORTER_DIRS:
        if d.is_dir():
            importers.extend(
                p for p in sorted(d.rglob("*.py")) if not is_test(p)
            )

    imported: set[str] = set()
    strrefs: set[str] = set()
    for p in importers:
        name = dotted(p)
        for imp in imports_of(p, name):
            imported.add(imp)
            # importing a.b.c also executes packages a and a.b
            parts = imp.split(".")
            for i in range(1, len(parts)):
                imported.add(".".join(parts[:i]))
        strrefs |= string_refs(p)

    orphans: list[tuple[str, str]] = []
    for name, path in sorted(modnames.items()):
        rel = str(path.relative_to(ROOT))
        if path.name == "__init__.py":
            continue
        if has_main_block(path):
            continue  # CLI entry point by design
        if name in imported or any(s == name or s.startswith(name + ".") for s in strrefs):
            continue
        reason = allowlisted(rel)
        if reason:
            continue
        orphans.append((rel, "no static importer found"))

    print(f"# orphan-module check — {len(modules)} modules scanned, "
          f"{len(orphans)} orphan candidates")
    if orphans:
        print()
        print("| module | finding |")
        print("|---|---|")
        for rel, finding in orphans:
            print(f"| `{rel}` | {finding} |")
        print()
        print("These are REPORT-ONLY. Wire them, extract them, or remove them —")
        print("a human decides. See PR #505 for the wiring pattern.")
    else:
        print("clean: every module is imported, dynamically referenced, or allowlisted.")

    if args.strict and orphans:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
