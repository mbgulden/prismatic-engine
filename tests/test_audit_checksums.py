"""Tests for audit-checksums.py."""

import subprocess
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _run_script(manifest: str | Path | None = None) -> subprocess.CompletedProcess:
    """Run audit-checksums.py and return the result."""
    script = Path(__file__).parent.parent / "scripts" / "audit-checksums.py"
    cmd = [sys.executable, str(script)]
    if manifest:
        cmd += ["--manifest", str(manifest)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result


def test_missing_manifest(tmp_path: Path):
    """Missing manifest file → exit 0 (skip gracefully)."""
    missing = tmp_path / "nonexistent.json"
    result = _run_script(str(missing))
    assert result.returncode == 0
    assert "missing" in result.stdout.lower()


def test_all_checksums_match():
    """All artifacts match their expected hashes → exit 0."""
    result = _run_script(FIXTURES / "good_checksums.json")
    assert result.returncode == 0, result.stdout
    assert "passed" in result.stdout.lower()


def test_checksum_mismatch():
    """Artifact hash doesn't match expected → exit 1."""
    result = _run_script(FIXTURES / "artifact_checksums.json")
    # dummy_artifact matches, corrupt doesn't
    # corrupt_artifact.txt has hash "e3b0c442..." (empty string hash)
    # but file content is "world\n" → actual hash is "486ea462..."
    assert result.returncode == 1
    assert "failed" in result.stdout.lower()


def test_missing_artifact():
    """Referenced artifact doesn't exist on disk → exit 1."""
    bad_manifest = FIXTURES / "missing_file_manifest.json"
    bad_manifest.write_text('{\n  "tests/fixtures/nonexistent.txt": "' + "a" * 64 + '"\n}')
    result = _run_script(str(bad_manifest))
    assert result.returncode == 1
    assert "missing" in result.stdout.lower()
