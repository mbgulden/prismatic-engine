"""Tests for deploy-pipeline alert logging (pe.deploy.deploy_alerts).

Regression coverage for the Sep 22, 2026 finding: the #469 rollback drill
worked, but the rollback path never wrote to the alert log, so a sweep of
the alert log found nothing. After this change the full
break -> detect -> rollback -> recovery sequence appears in the alert log.

Covers:
  - emit_deploy_alert: schema matches AlertRouter._log_alert, never raises,
    unknown severity coerces to info, path precedence.
  - gateway redeployer: ordered rollback events on health failure; no
    rollback events on pre-flip failure or success; GatewayRollbackFailed
    when rollback itself fails.
  - receiver: PostMergeDeploySucceeded / PostMergeDeployFailed terminal
    states; dry-run emits nothing.
"""

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

os.environ["PRISMATIC_ALLOW_DEFAULT_HMAC"] = "1"

from pe.deploy.deploy_alerts import (  # noqa: E402
    ENTRY_KEYS,
    build_alert_entry,
    default_alert_log_path,
    emit_deploy_alert,
)
from pe.deploy.gateway_redeploy import (  # noqa: E402
    DeployFailed,
    GatewayDeployResult,
    GatewayRedeployer,
)


# ---------------------------------------------------------------- helpers


@pytest.fixture
def alert_log(tmp_path, monkeypatch):
    """Point the alert log at a tmp file via PRISMATIC_ALERT_LOG."""
    p = tmp_path / "alerts.log"
    monkeypatch.setenv("PRISMATIC_ALERT_LOG", str(p))
    return p


def read_entries(path):
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def event_names(path):
    p = Path(path)
    if not p.exists():
        return []
    return [e["name"] for e in read_entries(p)]


class FakeRun:
    """Scripted subprocess double. Records every call."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if "is-active" in argv:
            return subprocess.CompletedProcess(argv, 0, "active\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def gw_home(tmp_path):
    """Fake ~/.prismatic with a live release + venv wired via symlinks."""
    home = tmp_path / "home"
    pris = home / ".prismatic"
    for d in ("releases", "venvs", "run", "wheel_cache"):
        (pris / d).mkdir(parents=True)
    live_rel = pris / "releases" / ("prismatic-engine-" + "a" * 40)
    live_rel.mkdir()
    (live_rel / "prismatic").mkdir()
    live_venv = pris / "venvs" / ("prismatic-engine-" + "a" * 40)
    (live_venv / "bin").mkdir(parents=True)
    (pris / "current").symlink_to(live_rel)
    (pris / "venv_current").symlink_to(live_venv)
    return home


class ScriptedRedeployer(GatewayRedeployer):
    """Redeployer with git/wheel/venv/health steps scripted."""

    def __init__(self, *args, health_ok=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.health_ok = health_ok

    def _verify_sha(self, repo, pr_sha):
        return "b" * 40, "c" * 40

    def _live_sha(self):
        return None  # never superseded in these tests

    def _build_wheel(self, worktree, dist_dir):
        w = dist_dir / "prismatic_engine-0.2.0-py3-none-any.whl"
        w.write_text("fake-wheel")
        return w

    def _create_venv(self, venv_dir, wheel):
        venv_dir.mkdir(parents=True, exist_ok=True)
        (venv_dir / "bin").mkdir(exist_ok=True)
        (venv_dir / "bin" / "python").write_text("#!/bin/sh\n")

    def _stage_release(self, worktree, version_dir):
        version_dir.mkdir(parents=True, exist_ok=True)
        (version_dir / "prismatic").mkdir(exist_ok=True)

    def _health_check(self, venv_dir):
        if self.health_ok:
            return {"passed": True, "checks": {"service_active": True}, "details": {}}
        return {
            "passed": False,
            "checks": {"service_active": False},
            "details": {"service_active_error": "simulated failure"},
        }


def make_deployer(home, **kw):
    run = FakeRun()
    kw.setdefault("lock_timeout_s", 30)
    return ScriptedRedeployer(home=home, run=run, **kw), run


# ------------------------------------------------------- helper unit tests


def test_entry_schema_matches_alert_router(alert_log):
    """Same five keys and ISO-8601 timestamp as AlertRouter._log_alert."""
    assert set(ENTRY_KEYS) == {
        "timestamp",
        "name",
        "severity",
        "summary",
        "details",
    }
    assert emit_deploy_alert("X", "info", "s", "d", log_path=alert_log) is True
    (entry,) = read_entries(alert_log)
    assert set(entry) == set(ENTRY_KEYS)
    datetime.fromisoformat(entry["timestamp"])  # parses
    assert entry["name"] == "X" and entry["severity"] == "info"


def test_emit_never_raises_on_unwritable_path(tmp_path):
    d = tmp_path / "adir"
    d.mkdir()
    # a directory is not writable as a file: must return False, not raise
    assert emit_deploy_alert("X", "critical", "s", log_path=d) is False


def test_unknown_severity_coerces_to_info(alert_log):
    assert emit_deploy_alert("X", "bogus", "s", log_path=alert_log) is True
    (entry,) = read_entries(alert_log)
    assert entry["severity"] == "info"


def test_default_path_precedence(tmp_path, monkeypatch):
    monkeypatch.delenv("PRISMATIC_ALERT_LOG", raising=False)
    monkeypatch.delenv("PRISMATIC_STATE_DIR", raising=False)
    # 1. explicit override wins
    monkeypatch.setenv("PRISMATIC_ALERT_LOG", "/x/y.log")
    assert default_alert_log_path() == Path("/x/y.log")
    # 2. state dir next
    monkeypatch.delenv("PRISMATIC_ALERT_LOG")
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path / "state"))
    assert default_alert_log_path() == tmp_path / "state" / "alerts.log"
    # 3. deploy-pipeline home fallback (never CWD-relative)
    monkeypatch.delenv("PRISMATIC_STATE_DIR")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert default_alert_log_path() == tmp_path / "home" / ".prismatic" / "alerts.log"


def test_build_alert_entry_timestamp_injectable():
    e = build_alert_entry(
        "N", "warning", "s", "d", timestamp="2026-01-01T00:00:00+00:00"
    )
    assert e["timestamp"] == "2026-01-01T00:00:00+00:00"
    assert e["severity"] == "warning"


# ------------------------------------------- rollback sequence (the replay)


def test_rollback_sequence_emits_ordered_alerts(gw_home, alert_log):
    """Full break -> detect -> rollback -> recovery sequence in the log.

    This is the regression test for the Sep 22 sweep finding nothing: the
    same sweep run against this log now finds the rollback, in order, with
    the right release ids.
    """
    dep, _ = make_deployer(gw_home, health_ok=False)
    res = dep.redeploy("b" * 40, repo=gw_home)

    assert not res.success and res.rolled_back
    names = event_names(alert_log)
    assert names == [
        "GatewayDeployHealthCheckFailed",  # detect
        "GatewayDeployFailed",  # failure
        "GatewayRollbackStarted",  # rollback begins
        "GatewayRollbackCompleted",  # recovery confirmed
    ]
    entries = {e["name"]: e for e in read_entries(alert_log)}

    detect = entries["GatewayDeployHealthCheckFailed"]
    assert detect["severity"] == "critical"
    assert "b" * 12 in detect["summary"]
    assert f"failed_release=prismatic-engine-{'b' * 40}" in detect["details"]

    failed = entries["GatewayDeployFailed"]
    assert "health check failed" in failed["details"]

    started = entries["GatewayRollbackStarted"]
    assert f"previous_release=prismatic-engine-{'a' * 40}" in started["details"]
    assert f"failed_release=prismatic-engine-{'b' * 40}" in started["details"]

    completed = entries["GatewayRollbackCompleted"]
    assert f"restored_release=prismatic-engine-{'a' * 40}" in completed["details"]
    assert "service_active=true" in completed["details"]
    # every entry carries the pr_sha for joining to deploy_records.json
    for e in entries.values():
        assert ("b" * 40) in e["details"]


def test_pre_flip_failure_emits_no_rollback_events(gw_home, alert_log):
    class BuildFails(ScriptedRedeployer):
        def _build_wheel(self, worktree, dist_dir):
            raise Exception("simulated wheel build failure")

    dep = BuildFails(home=gw_home, run=FakeRun(), lock_timeout_s=30)
    res = dep.redeploy("b" * 40, repo=gw_home)

    assert not res.success
    names = event_names(alert_log)
    assert "GatewayDeployFailed" in names
    assert "GatewayRollbackStarted" not in names
    assert "GatewayRollbackCompleted" not in names
    assert "GatewayRollbackFailed" not in names


def test_rollback_failure_emits_gateway_rollback_failed(gw_home, alert_log):
    class RestartFailsOnRollback(ScriptedRedeployer):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._restarts = 0

        def _systemctl(self, action, timeout=120):
            self._restarts += 1
            if self._restarts > 1:  # the rollback's restart
                raise DeployFailed("simulated rollback restart failure")

    dep = RestartFailsOnRollback(
        home=gw_home, run=FakeRun(), lock_timeout_s=30, health_ok=False
    )
    res = dep.redeploy("b" * 40, repo=gw_home)

    assert not res.success and not res.rolled_back
    names = event_names(alert_log)
    assert "GatewayRollbackStarted" in names
    assert "GatewayRollbackFailed" in names
    assert "GatewayRollbackCompleted" not in names
    (failed,) = [
        e for e in read_entries(alert_log) if e["name"] == "GatewayRollbackFailed"
    ]
    assert "failure_point=service_restart" in failed["details"]
    assert failed["severity"] == "critical"


def test_success_emits_no_gateway_alerts(gw_home, alert_log):
    dep, _ = make_deployer(gw_home, health_ok=True)
    res = dep.redeploy("b" * 40, repo=gw_home)
    assert res.success
    assert event_names(alert_log) == []


# ------------------------------------------------ receiver terminal states


class _StubGatewayRedeployer:
    def __init__(self, success=True, skipped=True, reason="stub", rolled_back=False):
        self._res = GatewayDeployResult(
            success=success,
            skipped=skipped,
            reason=reason,
            pr_sha="t" * 40,
            rolled_back=rolled_back,
        )

    def redeploy(self, pr_sha="", repo=None, dry_run=False):
        return self._res


def _make_pipeline(tmp_path, gateway_redeployer):
    from pe.deploy.health import PostDeployHealthChecker
    from pe.deploy.integrate import AtomicDeployRunner
    from pe.deploy.linear_transition import LinearDeployTransitioner
    from pe.deploy.manifest import DeployManifestStore
    from pe.deploy.receiver import DeployReceiverPipeline

    import subprocess

    versions = tmp_path / "versions"
    releases = tmp_path / "releases"
    versions.mkdir()
    releases.mkdir()
    source = tmp_path / "repo"
    (source / "prismatic").mkdir(parents=True)
    (source / "prismatic" / "__init__.py").write_text("# main")
    # Real git repo: deploys build a pristine worktree of an exact commit
    # (fail-fast source validation, Sep 22 2026).
    def _g(*args):
        subprocess.run(["git", *args], cwd=str(source), check=True,
                       capture_output=True, text=True)
    _g("init", "-q", "-b", "main", ".")
    _g("config", "user.email", "test@example.com")
    _g("config", "user.name", "test")
    _g("add", ".")
    _g("commit", "-qm", "seed")
    head_sha = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    pipeline = DeployReceiverPipeline(
        source_repo=source,
        deploy_runner=AtomicDeployRunner(
            versions_dir=versions,
            release_symlink=releases / "prismatic-engine",
        ),
        health_checker=PostDeployHealthChecker(),
        transitioner=LinearDeployTransitioner(dry_run=True),
        store=DeployManifestStore(db_path=tmp_path / "deploy_records.json"),
        gateway_redeployer=gateway_redeployer,
        mirror_repo=tmp_path / "no-such-dir",  # fail-closed, no fetch
    )
    pipeline.test_head_sha = head_sha
    return pipeline


def test_process_deploy_success_emits_info(tmp_path, alert_log):
    pipeline = _make_pipeline(tmp_path, _StubGatewayRedeployer())
    record = pipeline.process_deploy(
        {"pr_sha": pipeline.test_head_sha, "pr_number": 7, "pr_title": "alert test"}
    )
    assert record.success is True
    (entry,) = [
        e for e in read_entries(alert_log) if e["name"] == "PostMergeDeploySucceeded"
    ]
    assert entry["severity"] == "info"
    assert record.deploy_id in entry["summary"]
    assert f"pr_sha={pipeline.test_head_sha}" in entry["details"]
    assert "duration_ms=" in entry["details"]


def test_process_deploy_rollback_emits_critical(tmp_path, alert_log):
    pipeline = _make_pipeline(
        tmp_path,
        _StubGatewayRedeployer(
            success=False, skipped=False, reason="boom", rolled_back=True
        ),
    )
    record = pipeline.process_deploy(
        {"pr_sha": pipeline.test_head_sha, "pr_number": 8}
    )
    assert record.success is False
    (entry,) = [
        e for e in read_entries(alert_log) if e["name"] == "PostMergeDeployFailed"
    ]
    assert entry["severity"] == "critical"
    assert "rolled_back=True" in entry["details"]
    assert "GATEWAY REDEPLOY FAILED" in entry["details"]


def test_dry_run_emits_nothing(tmp_path, alert_log):
    pipeline = _make_pipeline(tmp_path, _StubGatewayRedeployer())
    record = pipeline.process_deploy({"pr_sha": "f" * 40, "dry_run": True})
    assert record.success is True
    assert event_names(alert_log) == [], "dry-run must keep zero side effects"


def test_default_emit_never_touches_production_log():
    """Regression (Sep 22, 2026): a bare ``emit_deploy_alert`` (no ``log_path=``)
    must land in the tmp-scoped log, never ``~/.prismatic/alerts.log``.

    This is what the B1 verification violated: ``test_process_deploy_pipeline``
    reached the real emit path with no override and polluted the production
    alert log with 14 synthetic entries on the box.
    """
    scoped = os.environ.get("PRISMATIC_ALERT_LOG")
    assert scoped, "autouse fixture must scope PRISMATIC_ALERT_LOG to tmp"
    assert "pytest" in scoped, f"expected a pytest tmp path, got {scoped!r}"

    production = Path.home() / ".prismatic" / "alerts.log"
    before = production.read_bytes() if production.exists() else None

    assert emit_deploy_alert("PytestGuard", "info", "guard", "d") is True

    # The default path resolves to the tmp-scoped log, not production.
    assert default_alert_log_path() == Path(scoped)
    assert default_alert_log_path() != production

    # Production log is byte-identical (or still absent).
    if before is None:
        assert not production.exists()
    else:
        assert production.read_bytes() == before

    # The entry actually landed in the tmp log.
    entries = read_entries(scoped)
    assert any(e["name"] == "PytestGuard" for e in entries)
