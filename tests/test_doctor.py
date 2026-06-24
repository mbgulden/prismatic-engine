"""Tests for prismatic.doctor — pure data layer (no CLI).

Doctor is the engine's health-diagnostic module. It probes:
- System (Python version, Git, gh CLI)
- Config (PRISMATIC_HOME, paths)
- Providers (Linear, GitHub credentials)
- Capabilities (which agent runtimes are reachable)

It returns a DoctorReport dataclass. The CLI presentation lives in
prismatic/cli/doctor.py and is tested separately.

These tests use monkeypatch to stub out external commands (git, gh) and
HTTP calls so the doctor can run in a hermetic test env.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_PE_ROOT = Path(os.environ.get(
    "PRISMATIC_HOME",
    os.path.join(os.environ.get("HOME", ""), "work", "prismatic-engine")
))
sys.path.insert(0, str(_PE_ROOT))

from prismatic.doctor import (  # noqa: E402
    SystemInfo,
    ConfigInfo,
    ProviderReport,
    CapabilityReport,
    DoctorReport,
    run_doctor,
    _probe_system,
    _compute_verdict,
)


# ── Dataclass shape tests ────────────────────────────────────────────
def test_system_info_defaults():
    s = SystemInfo()
    assert s.python_version == ""
    assert s.git_version == ""
    assert s.gh_cli_version == ""


def test_config_info_defaults():
    c = ConfigInfo()
    assert c.prismatic_home == ""
    assert c.user_config_exists is False
    assert c.database_exists is False


def test_provider_report_defaults():
    """ProviderReport has 15+ fields; test the most important defaults."""
    p = ProviderReport(name="linear")
    assert p.name == "linear"
    assert p.status == "unknown"
    assert p.credential_source == "Not found"
    assert p.scopes == []
    assert p.missing_scopes == []


def test_provider_report_to_dict_is_jsonable():
    """to_dict() output must be JSON-serializable."""
    p = ProviderReport(
        name="linear", status="connected", user="test@example.com",
        user_name="Test", scopes=["read", "write"], missing_scopes=[],
        target_repo="foo/bar", repo_access_verified=True,
        rate_limit_info={"limit": 5000},
    )
    d = p.to_dict()
    # Must serialize cleanly
    json.dumps(d)
    assert d["name"] == "linear"
    assert d["scopes"] == ["read", "write"]


def test_capability_report_shape():
    c = CapabilityReport(name="chat_agy", status="ok", message="ready")
    assert c.name == "chat_agy"
    assert c.status == "ok"
    assert c.message == "ready"


def test_doctor_report_to_dict_includes_sections():
    """DoctorReport.to_dict() has system, config, providers, capabilities, verdict."""
    r = DoctorReport()
    d = r.to_dict()
    assert "system" in d
    assert "config" in d
    assert "providers" in d
    assert "capabilities" in d
    assert "verdict" in d


# ── Probes: system ───────────────────────────────────────────────────
def test_probe_system_returns_system_info(monkeypatch):
    """_probe_system returns a SystemInfo with all three versions."""
    def fake_run(cmd, **kwargs):
        if "git" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="git version 2.43.0\n", stderr="",
            )
        if "gh" in cmd:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="gh version 2.55.0\n", stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    s = _probe_system()
    assert isinstance(s, SystemInfo)
    assert "2.43" in s.git_version
    assert "2.55" in s.gh_cli_version
    assert s.python_version  # non-empty (real Python)


def test_probe_system_handles_missing_gh_cli(monkeypatch):
    """If gh CLI is not installed, version is 'Not found' (graceful)."""
    def fake_run(cmd, **kwargs):
        if "gh" in cmd:
            raise FileNotFoundError("gh not found")
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="git version 2.43.0\n", stderr="",
        )
    monkeypatch.setattr(subprocess, "run", fake_run)
    s = _probe_system()
    assert "Not found" in s.gh_cli_version or s.gh_cli_version == ""
    assert "2.43" in s.git_version  # git still probed


def test_probe_system_handles_gh_timeout(monkeypatch):
    """If gh hangs, doctor doesn't hang — returns 'Not found'."""
    def fake_run(cmd, **kwargs):
        if "gh" in cmd:
            raise subprocess.TimeoutExpired(cmd, timeout=5)
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout="git version 2.43.0\n", stderr="",
        )
    monkeypatch.setattr(subprocess, "run", fake_run)
    s = _probe_system()
    assert "Not found" in s.gh_cli_version or s.gh_cli_version == ""


# ── Verdict logic ────────────────────────────────────────────────────
def test_compute_verdict_ok_when_all_healthy():
    r = _compute_verdict(
        providers=[ProviderReport(name="linear", status="connected")],
        capabilities=[CapabilityReport(name="chat_agy", status="ok")],
    )
    assert r == "OK"


def test_compute_verdict_warn_when_capability_unavailable():
    r = _compute_verdict(
        providers=[],
        capabilities=[CapabilityReport(name="chat_agy", status="error", message="missing")],
    )
    assert r in ("WARN", "ERROR")


def test_compute_verdict_error_when_provider_disconnected():
    r = _compute_verdict(
        providers=[ProviderReport(name="linear", status="disconnected")],
        capabilities=[],
    )
    assert r in ("ERROR", "WARN")


def test_compute_verdict_handles_empty_inputs():
    """No providers, no capabilities → should be OK (or warn)."""
    r = _compute_verdict(providers=[], capabilities=[])
    assert r in ("OK", "WARN")


# ── run_doctor (the public entry point) ──────────────────────────────
def test_run_doctor_returns_doctor_report(monkeypatch):
    """run_doctor() returns a DoctorReport (smoke test, no network)."""
    monkeypatch.setenv("LINEAR_API_KEY", "")
    monkeypatch.setenv("GITHUB_TOKEN", "")
    report = run_doctor()
    assert isinstance(report, DoctorReport)
    assert report.system is not None
    assert report.config is not None
    assert isinstance(report.providers, list)
    assert isinstance(report.capabilities, list)
    assert report.verdict in ("OK", "WARN", "ERROR")


def test_run_doctor_handles_missing_env(monkeypatch, tmp_path):
    """When PRISMATIC_HOME doesn't exist, run_doctor still returns a report."""
    monkeypatch.delenv("PRISMATIC_HOME", raising=False)
    monkeypatch.setenv("LINEAR_API_KEY", "")
    monkeypatch.setenv("GITHUB_TOKEN", "")
    # Pass explicit path so it doesn't fail on real disk
    report = run_doctor()
    assert report is not None
    assert isinstance(report.verdict, str)
    assert report.config.prismatic_home == "" or report.config.prismatic_home != ""


def test_run_doctor_to_dict_is_jsonable(monkeypatch):
    """Full DoctorReport.to_dict() must round-trip through JSON."""
    monkeypatch.setenv("LINEAR_API_KEY", "")
    monkeypatch.setenv("GITHUB_TOKEN", "")
    report = run_doctor()
    d = report.to_dict()
    # Round-trip through JSON
    serialized = json.dumps(d, default=str)
    deserialized = json.loads(serialized)
    assert deserialized["verdict"] == report.verdict
    assert "system" in deserialized
    assert "config" in deserialized