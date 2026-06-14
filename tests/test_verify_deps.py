"""Tests for verify-phase-dependencies.py."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _run_script(phase_id: str, state_dir: str | Path) -> subprocess.CompletedProcess:
    """Run verify-phase-dependencies.py and return the result."""
    script = Path(__file__).parent.parent / "scripts" / "verify-phase-dependencies.py"
    result = subprocess.run(
        [sys.executable, str(script), phase_id, "--state-dir", str(state_dir)],
        capture_output=True,
        text=True,
    )
    return result


def test_missing_deps_file(tmp_path: Path):
    """Missing phase_dependencies.json → exit 0 (skip check gracefully)."""
    result = _run_script("14", tmp_path)
    assert result.returncode == 0
    assert "missing" in result.stdout.lower()


def test_all_deps_satisfied():
    """All prerequisites completed → exit 0."""
    result = _run_script("14", FIXTURES)
    assert result.returncode == 0, result.stdout
    assert "passed" in result.stdout.lower()


def test_missing_dependency():
    """Prerequisite not completed → exit 1 with violation message."""
    result = _run_script("17", FIXTURES)
    assert result.returncode == 1
    assert "violation" in result.stdout.lower()


def test_phase_with_unknown_prereq():
    """Prerequisite has no matching completed run → exit 1."""
    result = _run_script("35", FIXTURES)
    assert result.returncode == 1
    assert "violation" in result.stdout.lower()


def test_phase_with_no_dependencies():
    """Phase ID not in deps map → exit 0 (no dependencies defined)."""
    result = _run_script("99", FIXTURES)
    assert result.returncode == 0
    assert "no defined dependencies" in result.stdout.lower()


def test_empty_deps_list():
    """Phase with empty prerequisites list → exit 0."""
    empty_deps = tmp_path = Path(__file__).parent / "fixtures_empty_deps"
    empty_deps.mkdir(exist_ok=True)
    (empty_deps / "phase_dependencies.json").write_text(
        json.dumps({"14": []})
    )
    (empty_deps / "run_records.json").write_text("[]")
    result = _run_script("14", empty_deps)
    assert result.returncode == 0
