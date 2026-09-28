"""tests/test_cron_renderer_prismatic_path.py — Section B: cron renderers must bake the
absolute `prismatic` entry point into crontab lines.

Background: cron runs with a minimal PATH where a bare `prismatic` resolves to
nothing, so engine.doctor (and the core worktree-janitor crons) failed with
exit 127. The renderers now substitute the entry point sitting next to the
running interpreter, falling back to the bare name where none exists (CI/dev).
"""

from __future__ import annotations

import sys
from pathlib import Path

from prismatic.native_crons import NativeCron, NativeCronStore, export_system_crontab_lines
from prismatic.worktree_janitor import crontab_lines, prismatic_entrypoint


def _fake_venv(tmp_path: Path, monkeypatch, with_entrypoint: bool = True) -> Path:
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    if with_entrypoint:
        (venv_bin / "prismatic").write_text("#!/bin/sh\n")
    monkeypatch.setattr(sys, "executable", str(venv_bin / "python"))
    return venv_bin


# ── prismatic_entrypoint() ──────────────────────────────────────────────


def test_entrypoint_uses_absolute_path_when_present(tmp_path: Path, monkeypatch) -> None:
    venv_bin = _fake_venv(tmp_path, monkeypatch, with_entrypoint=True)
    assert prismatic_entrypoint() == str(venv_bin / "prismatic")


def test_entrypoint_falls_back_to_bare_name(tmp_path: Path, monkeypatch) -> None:
    _fake_venv(tmp_path, monkeypatch, with_entrypoint=False)
    assert prismatic_entrypoint() == "prismatic"


# ── native_crons: engine.doctor line ────────────────────────────────────


def _store_with(tmp_path: Path, cron: NativeCron) -> NativeCronStore:
    store = NativeCronStore(path=tmp_path / "native_crons.json")
    store.save([cron])
    return store


def _doctor_cron() -> NativeCron:
    return NativeCron(
        id="engine.doctor",
        name="Engine health — Weekly doctor",
        schedule="0 7 * * 1",
        command=["prismatic", "doctor"],
        cwd=".",
    )


def test_doctor_line_bakes_absolute_entrypoint(tmp_path: Path, monkeypatch) -> None:
    """Fail-first: pre-fix, the job tail contained bare `prismatic doctor`."""
    venv_bin = _fake_venv(tmp_path, monkeypatch, with_entrypoint=True)
    store = _store_with(tmp_path, _doctor_cron())
    lines = [line for line in export_system_crontab_lines(store) if "engine.doctor" in line]
    assert len(lines) == 1
    assert f"{venv_bin / 'prismatic'} doctor" in lines[0]
    assert "&& prismatic doctor" not in lines[0]


def test_doctor_line_falls_back_to_bare_prismatic(tmp_path: Path, monkeypatch) -> None:
    _fake_venv(tmp_path, monkeypatch, with_entrypoint=False)
    store = _store_with(tmp_path, _doctor_cron())
    lines = [line for line in export_system_crontab_lines(store) if "engine.doctor" in line]
    assert len(lines) == 1
    assert "prismatic doctor" in lines[0]


def test_non_prismatic_commands_untouched(tmp_path: Path, monkeypatch) -> None:
    _fake_venv(tmp_path, monkeypatch, with_entrypoint=True)
    cron = NativeCron(
        id="test.echo",
        name="echo",
        schedule="*/5 * * * *",
        command=["python3", "scripts/foo.py"],
        cwd=".",
    )
    store = _store_with(tmp_path, cron)
    lines = [line for line in export_system_crontab_lines(store) if "test.echo" in line]
    assert len(lines) == 1
    assert "python3 scripts/foo.py" in lines[0].replace("'", "")


# ── worktree_janitor: core crons ────────────────────────────────────────


def test_core_crons_bake_absolute_entrypoint(tmp_path: Path, monkeypatch) -> None:
    """Fail-first: pre-fix, core cron lines ran bare `prismatic` (exit 127)."""
    venv_bin = _fake_venv(tmp_path, monkeypatch, with_entrypoint=True)
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setattr(
        "prismatic.worktree_janitor.resolve_repo", lambda *_a, **_k: repo
    )
    lines = crontab_lines(str(repo))
    job_lines = [line for line in lines if line and line[0].isdigit()]
    assert len(job_lines) == 2
    for line in job_lines:
        assert str(venv_bin / "prismatic") in line


def test_core_crons_fall_back_to_bare_prismatic(tmp_path: Path, monkeypatch) -> None:
    _fake_venv(tmp_path, monkeypatch, with_entrypoint=False)
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setattr(
        "prismatic.worktree_janitor.resolve_repo", lambda *_a, **_k: repo
    )
    lines = crontab_lines(str(repo))
    job_lines = [line for line in lines if line and line[0].isdigit()]
    assert len(job_lines) == 2
    for line in job_lines:
        assert "prismatic worktrees" in line
