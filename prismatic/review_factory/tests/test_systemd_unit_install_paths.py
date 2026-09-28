"""Contract test: the review-factory systemd units must reference the real install layout.

Regression guard for the Sep 2026 outage in which
``prismatic-review-factory.service`` pinned a release-specific path
(``/home/ubuntu/.prismatic/releases/prismatic-engine-cd5f4bd``). That release
was later garbage-collected, so every activation died in mount-namespacing
setup with ``226/NAMESPACE`` (``Failed to set up mount namespacing: ...: No
such file or directory``) and the auto-merge consult never ran.

The units therefore must point at the stable install layout -- venv_stable
plus the runtime checkout -- the same layout the sibling units use
(``scripts/prismatic-learn-loop.service`` et al.). This test is
grep-based and stdlib-only on purpose: it must keep working even when the
engine's heavier dependencies are not installed.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICE_FILE = REPO_ROOT / "scripts" / "prismatic-review-factory.service"
PATH_FILE = REPO_ROOT / "scripts" / "prismatic-review-factory.path"

VENV_PYTHON = "/home/ubuntu/.prismatic/venv_stable/bin/python3"
RUNTIME_DIR = "/home/ubuntu/.prismatic/runtime/prismatic-engine"
ENTRY_MODULE = "prismatic.review_factory.backlog_importer"

# Release-pinned layouts rot as soon as the pinned release is
# garbage-collected (see the cd5f4bd outage). Nothing in a shipped unit may
# reference them.
RELEASE_PINNED = re.compile(
    r"/home/ubuntu/\.prismatic/(releases|venvs)/prismatic-engine-[0-9a-f]+"
)

# Directives whose values systemd resolves as filesystem paths. Only these are
# scanned for release-pinned paths: a mention inside a comment or prose is
# harmless, but a pinned path here will break the unit when the release is
# garbage-collected.
_PATH_DIRECTIVES = frozenset(
    {
        "ExecStart",
        "ExecStartPre",
        "ExecStartPost",
        "ExecReload",
        "ExecStop",
        "ExecCondition",
        "WorkingDirectory",
        "RootDirectory",
        "ReadOnlyPaths",
        "ReadWritePaths",
        "InaccessiblePaths",
        "BindPaths",
        "BindReadOnlyPaths",
        "TemporaryFileSystem",
        "Environment",
        "EnvironmentFile",
        "Documentation",
        "PathExists",
        "PathExistsGlob",
        "PathChanged",
        "PathModified",
        "DirectoryNotEmpty",
    }
)


def _path_directive_values(text: str) -> list[tuple[str, str]]:
    """Return (directive, value) for path-valued directives, ignoring comments."""
    found: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";") or line.startswith("["):
            continue
        name, sep, value = line.partition("=")
        if not sep:
            continue
        name = name.strip()
        if name in _PATH_DIRECTIVES:
            found.append((name, value))
    return found


def _assert_no_release_pinned(values: list[tuple[str, str]], *, unit: str) -> None:
    for name, value in values:
        match = RELEASE_PINNED.search(value)
        assert match is None, (
            f"{unit}: {name}= references a release-pinned path {match.group(0)!r} "
            "that can be garbage-collected (this caused the 226/NAMESPACE outage)"
        )
# The exact stale unit text that caused the outage, captured from the live
# host on 2026-09-27. The negative test below proves this contract test
# rejects it.
STALE_SERVICE_TEXT = """\
[Unit]
Description=Prismatic Review/Merge Factory bounded one-shot drain
Documentation=file:///home/ubuntu/.prismatic/releases/prismatic-engine-cd5f4bd/deploy/systemd/REVIEW_FACTORY_RUNTIME.md
After=network-online.target

[Service]
Type=oneshot
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/.prismatic/releases/prismatic-engine-cd5f4bd
ExecStart=/home/ubuntu/.prismatic/venvs/prismatic-engine-cd5f4bd/bin/python -m prismatic.review_factory.backlog_importer --db-path /home/ubuntu/.prismatic/state/agy_completed_work.db --completed-work-db-path /home/ubuntu/.prismatic/state/agy_completed_work.db --inbox-dir /home/ubuntu/.prismatic/inbox --state-dir /home/ubuntu/.prismatic/state --workspace-dir /home/ubuntu/.prismatic/state/source-workspaces --max-items 20 --worker-id review-factory-runtime
Environment=PRISMATIC_STATE_DIR=/home/ubuntu/.prismatic/state
PrivateTmp=true
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=false
ReadOnlyPaths=/home/ubuntu/.prismatic/releases/prismatic-engine-cd5f4bd /home/ubuntu/.prismatic/venvs/prismatic-engine-cd5f4bd
ReadWritePaths=/home/ubuntu/.prismatic/state /home/ubuntu/.prismatic/inbox
UMask=0077
"""


def _read(path: Path) -> str:
    assert path.is_file(), f"unit file missing from repo: {path}"
    return path.read_text(encoding="utf-8")


def check_service_text(text: str) -> None:
    """Enforce the install-layout contract on service-unit text."""
    _assert_no_release_pinned(
        _path_directive_values(text), unit="prismatic-review-factory.service"
    )
    exec_starts = [
        line for line in text.splitlines() if line.startswith("ExecStart=")
    ]
    assert exec_starts, "service unit has no ExecStart="
    for line in exec_starts:
        assert VENV_PYTHON in line, (
            f"ExecStart= must use the stable venv interpreter {VENV_PYTHON}: {line}"
        )
        assert f"-m {ENTRY_MODULE}" in line, (
            f"ExecStart= must run the review-factory drain module {ENTRY_MODULE}: {line}"
        )
    wd_lines = [
        line for line in text.splitlines() if line.startswith("WorkingDirectory=")
    ]
    assert wd_lines, "service unit has no WorkingDirectory="
    for line in wd_lines:
        assert RUNTIME_DIR in line, (
            f"WorkingDirectory= must be the stable runtime checkout {RUNTIME_DIR}: {line}"
        )


def check_path_text(text: str) -> None:
    """Enforce the install-layout contract on path-unit text."""
    _assert_no_release_pinned(
        _path_directive_values(text), unit="prismatic-review-factory.path"
    )
    assert "Unit=prismatic-review-factory.service" in text, (
        "path unit must activate prismatic-review-factory.service"
    )


def test_service_unit_is_repo_managed_and_uses_stable_install() -> None:
    check_service_text(_read(SERVICE_FILE))


def test_path_unit_is_repo_managed_and_triggers_service() -> None:
    check_path_text(_read(PATH_FILE))


def test_entry_module_exists_in_repo() -> None:
    module_path = REPO_ROOT / (ENTRY_MODULE.replace(".", "/") + ".py")
    assert module_path.is_file(), (
        f"ExecStart= entry module {ENTRY_MODULE} has no source file at {module_path}"
    )


def test_stale_unit_text_is_rejected() -> None:
    """Negative path: the pre-R-1 unit text must fail this contract."""
    try:
        check_service_text(STALE_SERVICE_TEXT)
    except AssertionError:
        return
    raise AssertionError(
        "contract test accepted the stale release-pinned unit text; "
        "it would not have caught the 226/NAMESPACE outage"
    )
