"""Dependency-pin enforcement: every git+https requirement must be pinned to a
full 40-char commit SHA, never a floating branch like @main or @master.

Rationale: the signal suite resolves these from the internet on every CI run.
A floating ref means two runs can test different code. This test fails the
build if any git dependency ever floats again. (B5, 2026-09-28.)
"""

import re
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python 3.10: repo vendors tomli for <3.11
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"

SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _iter_git_urls():
    with open(PYPROJECT, "rb") as f:
        data = tomllib.load(f)
    project = data.get("project", {})
    groups = {"dependencies": project.get("dependencies", [])}
    for extra, reqs in project.get("optional-dependencies", {}).items():
        groups[f"optional-dependencies[{extra}]"] = reqs
    for group, reqs in groups.items():
        for req in reqs:
            if "git+https://" in req:
                # PEP 508 direct reference: "name @ git+https://...@<ref>"
                url = req.split()[-1]
                ref = url.rsplit("@", 1)[-1]
                yield group, req, url, ref


def test_all_git_dependencies_pinned_to_sha():
    floating = [
        (group, req)
        for group, req, _url, ref in _iter_git_urls()
        if not SHA_RE.match(ref)
    ]
    assert not floating, (
        "Floating git dependencies found (must be pinned to a 40-char SHA):\n"
        + "\n".join(f"  [{group}] {req}" for group, req in floating)
    )


def test_at_least_one_git_dependency_checked():
    urls = list(_iter_git_urls())
    assert urls, "Expected git+https dependencies in pyproject.toml; found none"
