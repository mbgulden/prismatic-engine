"""Contract tests for the process-manager abstraction (deploy-portability WS2).

- ``SystemdProcessManager`` issues byte-identical commands to today's deploy
  code paths (assert command *construction* only -- never execute real
  systemctl in tests).
- Privilege needs surface as the typed ``ProcessManagerPrivilegeError``,
  never as a hang.
- Swapping managers changes no deploy-logic outcome (fake manager records
  calls; the systemd-backed run and the fake-backed run produce identical
  deploy results).
"""

import subprocess
import time
from pathlib import Path

import pytest

from pe.deploy.gateway_redeploy import (
    DeployFailed,
    GatewayRedeployer,
)
from pe.deploy.process_manager import (
    ProcessManager,
    ProcessManagerError,
    ProcessManagerPrivilegeError,
)
from pe.deploy.process_manager_systemd import SystemdProcessManager


import pe.deploy.process_manager as pm_mod


class ScriptedRun:
    """Records (argv, timeout); scripted (returncode, stdout, stderr) per key."""

    def __init__(self, script=None):
        self.calls: list[tuple[list[str], int | None]] = []
        self.script = script or {}

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append((argv, kwargs.get("timeout")))
        for key, resp in self.script.items():
            if key in argv:
                rc, out, err = resp
                return subprocess.CompletedProcess(argv, rc, out, err)
        return subprocess.CompletedProcess(argv, 0, "", "")

    def argvs(self):
        return [c[0] for c in self.calls]


class FakeManager(ProcessManager):
    """Test double: records calls, scripted results."""

    def __init__(self, active=True):
        self.restart_calls: list[tuple[str, int | None]] = []
        self.is_active_calls: list[tuple[str, int]] = []
        self._active = active

    def restart(self, service, *, timeout=None):
        self.restart_calls.append((service, timeout))

    def is_active(self, service, *, timeout_s=60):
        self.is_active_calls.append((service, timeout_s))
        return self._active

    def install_unit(self, name, content, *, scope="system"):
        return f"/fake/units/{name}"

    def enable(self, service):
        pass

    def status(self, service):
        return {"service": service, "ActiveState": "active"}


# ---------------------------------------------------------------- interface


def test_process_manager_is_abstract():
    with pytest.raises(TypeError):
        ProcessManager()  # type: ignore[abstract]


def test_deferred_implementations_named_in_docstring():
    doc = pm_mod.__doc__
    assert "launchd" in doc
    assert "Windows" in doc
    assert "subprocess" in doc.lower()


def test_privilege_error_is_typed_subclass():
    assert issubclass(ProcessManagerPrivilegeError, ProcessManagerError)


# ------------------------------------------------- systemd command construction


def test_restart_issues_exact_sudo_systemctl_argv():
    run = ScriptedRun()
    SystemdProcessManager(run=run).restart("prismatic-gateway.service", timeout=180)
    assert run.calls == [
        (
            [
                "sudo",
                "-n",
                "/usr/bin/systemctl",
                "restart",
                "prismatic-gateway.service",
            ],
            180,
        )
    ]


def test_restart_honors_custom_systemctl_bin():
    run = ScriptedRun()
    SystemdProcessManager(systemctl_bin="/bin/systemctl", run=run).restart("svc")
    assert run.argvs() == [["sudo", "-n", "/bin/systemctl", "restart", "svc"]]


def test_is_active_polls_until_active():
    attempts = {"n": 0}
    run = ScriptedRun()

    def scripted(argv, **kwargs):
        argv = list(argv)
        attempts["n"] += 1
        run.calls.append((argv, kwargs.get("timeout")))
        out = "inactive\n" if attempts["n"] < 3 else "active\n"
        return subprocess.CompletedProcess(argv, 0, out, "")

    mgr = SystemdProcessManager(run=scripted)
    assert mgr.is_active("svc", timeout_s=60) is True
    assert attempts["n"] == 3
    for argv, timeout in run.calls:
        assert argv == ["sudo", "-n", "/usr/bin/systemctl", "is-active", "svc"]
        assert timeout == 15


def test_is_active_swallows_failures_and_returns_false(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    run = ScriptedRun({"is-active": (1, "", "boom")})
    mgr = SystemdProcessManager(run=run)
    assert mgr.is_active("svc", timeout_s=1) is False
    assert len(run.calls) >= 1  # retried, never raised


def test_restart_failure_message_matches_legacy_format():
    run = ScriptedRun({"restart": (1, "", "boom")})
    with pytest.raises(
        ProcessManagerError,
        match=r"^command exited 1: sudo -n /usr/bin/systemctl restart :: boom$",
    ):
        SystemdProcessManager(run=run).restart("svc")


def test_sudo_password_required_raises_typed_privilege_error():
    run = ScriptedRun({"restart": (1, "", "sudo: a password is required\n")})
    with pytest.raises(ProcessManagerPrivilegeError) as ei:
        SystemdProcessManager(run=run).restart("svc")
    assert "passwordless sudo" in str(ei.value)
    assert isinstance(ei.value, ProcessManagerError)


def test_enable_issues_exact_argv():
    run = ScriptedRun()
    SystemdProcessManager(run=run).enable("svc")
    assert run.argvs() == [["sudo", "-n", "/usr/bin/systemctl", "enable", "svc"]]


def test_enable_user_scope_issues_user_argv():
    run = ScriptedRun()
    SystemdProcessManager(run=run).enable("svc", scope="user")
    assert run.argvs() == [["/usr/bin/systemctl", "--user", "enable", "svc"]]


def test_enable_unknown_scope_raises():
    run = ScriptedRun()
    with pytest.raises(ProcessManagerError, match="unknown unit scope"):
        SystemdProcessManager(run=run).enable("svc", scope="bogus")


def test_install_unit_user_scope_writes_file_and_reloads(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    run = ScriptedRun()
    path = SystemdProcessManager(run=run).install_unit(
        "x.service", "[Unit]\n", scope="user"
    )
    unit = tmp_path / ".config" / "systemd" / "user" / "x.service"
    assert path == str(unit)
    assert unit.read_text() == "[Unit]\n"
    assert run.argvs() == [["/usr/bin/systemctl", "--user", "daemon-reload"]]


def test_install_unit_unknown_scope_raises():
    with pytest.raises(ProcessManagerError, match="unknown unit scope"):
        SystemdProcessManager(run=ScriptedRun()).install_unit(
            "x.service", "", scope="bogus"
        )


def test_status_parses_show_output():
    run = ScriptedRun(
        {"show": (0, "ActiveState=active\nSubState=running\nLoadState=loaded\n", "")}
    )
    info = SystemdProcessManager(run=run).status("svc")
    assert info == {
        "service": "svc",
        "ActiveState": "active",
        "SubState": "running",
        "LoadState": "loaded",
    }
    assert run.argvs() == [
        [
            "sudo",
            "-n",
            "/usr/bin/systemctl",
            "show",
            "svc",
            "-p",
            "ActiveState",
            "-p",
            "SubState",
            "-p",
            "LoadState",
        ]
    ]


# ------------------------------------------------- gateway_redeploy delegation


def _make_home(base: Path) -> Path:
    home = base / "home"
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
    """Redeployer with git/wheel/venv/health steps scripted for manager tests."""

    def _verify_sha(self, repo, pr_sha):
        return "b" * 40, "c" * 40

    def _live_sha(self):
        return None

    def _build_wheel(self, worktree, dist_dir):
        w = dist_dir / "prismatic_engine-0.2.0-py3-none-any.whl"
        w.write_text("fake-wheel")
        return w

    def _create_venv(self, venv_dir, wheel):
        (venv_dir / "bin").mkdir(parents=True, exist_ok=True)
        (venv_dir / "bin" / "python").write_text("#!/bin/sh\n")

    def _stage_release(self, worktree, version_dir):
        (version_dir / "prismatic").mkdir(parents=True, exist_ok=True)

    def _health_check(self, venv_dir):
        return {"passed": True, "checks": {"service_active": True}, "details": {}}


def test_redeployer_defaults_to_systemd_manager(tmp_path):
    dep = ScriptedRedeployer(home=_make_home(tmp_path))
    mgr = dep._process_manager
    assert isinstance(mgr, SystemdProcessManager)
    assert mgr.systemctl_bin == "/usr/bin/systemctl"


def test_redeployer_accepts_custom_manager(tmp_path):
    fake = FakeManager()
    dep = ScriptedRedeployer(home=_make_home(tmp_path), manager=fake)
    assert dep._process_manager is fake


def test_unsupported_systemctl_action_is_refused(tmp_path):
    dep = ScriptedRedeployer(home=_make_home(tmp_path), run=ScriptedRun())
    with pytest.raises(DeployFailed, match="unsupported systemctl action"):
        dep._systemctl("stop")


def test_privilege_error_surfaces_as_deploy_failed_without_hanging(tmp_path):
    run = ScriptedRun({"restart": (1, "", "sudo: a password is required\n")})
    dep = ScriptedRedeployer(home=_make_home(tmp_path), run=run)
    with pytest.raises(DeployFailed, match="passwordless sudo"):
        dep._systemctl("restart", timeout=180)


def _deploy_outcome(dep, repo) -> dict:
    res = dep.redeploy("b" * 40, repo=repo)
    return {
        "success": res.success,
        "skipped": res.skipped,
        "reason": res.reason,
        "version_dir": Path(res.version_dir).name,
        "venv_dir": Path(res.venv_dir).name,
        "rolled_back": res.rolled_back,
    }


def test_manager_swap_changes_no_deploy_outcome(tmp_path):
    # systemd-backed (default) vs fake-manager-backed: identical outcomes.
    run_sys = ScriptedRun()
    dep_sys = ScriptedRedeployer(
        home=_make_home(tmp_path / "sys"), run=run_sys, lock_timeout_s=30
    )
    out_sys = _deploy_outcome(dep_sys, repo=tmp_path / "sys")

    run_fake = ScriptedRun()
    fake = FakeManager()
    dep_fake = ScriptedRedeployer(
        home=_make_home(tmp_path / "fake"),
        run=run_fake,
        manager=fake,
        lock_timeout_s=30,
    )
    out_fake = _deploy_outcome(dep_fake, repo=tmp_path / "fake")

    assert out_sys == out_fake
    assert out_sys["success"] and not out_sys["skipped"]
    assert out_sys["version_dir"] == "prismatic-engine-" + "b" * 40

    # systemd path issued today's exact restart command...
    assert (
        ["sudo", "-n", "/usr/bin/systemctl", "restart", "prismatic-gateway.service"],
        180,
    ) in run_sys.calls
    # ...and the fake manager recorded the equivalent restart call.
    assert fake.restart_calls == [("prismatic-gateway.service", 180)]
    # no systemctl argv leaked through the generic run hook in the fake case
    assert not any("systemctl" in argv for argv in run_fake.argvs())
