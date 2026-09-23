"""WS3 doctor deploy-section tests: prismatic.doctor._probe_deploy.

Hermetic: HOME, the unit search dirs (PRISMATIC_DEPLOY_UNIT_DIRS), and the
registry env are all isolated per test. Never touches the live box.
"""

import json

import pytest

from pe.deploy.install import LAYOUT_SUBDIRS
from prismatic import doctor as doctor_module
from prismatic.doctor import (
    _compute_verdict,
    _probe_deploy,
)


@pytest.fixture()
def deploy_env(tmp_path, monkeypatch):
    """A fake HOME with a WS3-installer layout (marker + dirs)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("PRISMATIC_STATE_DIR", raising=False)
    monkeypatch.delenv("PRISMATIC_DEPLOY_REPOS_FILE", raising=False)
    monkeypatch.delenv("PRISMATIC_DEPLOY_REPOS", raising=False)
    # Unit search dirs: only the fake user dir (never the live system).
    units = home / ".config" / "systemd" / "user"
    units.mkdir(parents=True)
    monkeypatch.setenv("PRISMATIC_DEPLOY_UNIT_DIRS", str(units))
    state = home / ".prismatic"
    state.mkdir()
    for sub in LAYOUT_SUBDIRS:
        (state / sub).mkdir()
    (state / ".deploy-install.json").write_text(
        json.dumps({"installer": "pe.deploy.install", "install_version": 1}),
        encoding="utf-8",
    )
    return {"home": home, "state": state, "units": units}


def _make_healthy(deploy_env, monkeypatch, secret="shared-value"):
    """Fill the fake layout: units, env file with secret, mirror."""
    state, units = deploy_env["state"], deploy_env["units"]
    (units / "prismatic-deploy-receiver.service").write_text(
        f"[Service]\nEnvironmentFile={state}/env.d/deploy-receiver.env\n",
        encoding="utf-8",
    )
    (units / "prismatic-gateway.service").write_text(
        "[Service]\nExecStart=/bin/true\n", encoding="utf-8"
    )
    (units / "prismatic-repo-mirror-fetch.service").write_text(
        "[Service]\nExecStart=/bin/true\n", encoding="utf-8"
    )
    (units / "prismatic-repo-mirror-fetch.timer").write_text(
        "[Timer]\nOnCalendar=*:0/15\n", encoding="utf-8"
    )
    env_file = state / "env.d" / "deploy-receiver.env"
    if secret:
        env_file.write_text(f"DEPLOY_HMAC_SECRET={secret}\n", encoding="utf-8")
    else:
        env_file.write_text("# generated\n", encoding="utf-8")
    mirror = state / "repos" / "mbgulden" / "prismatic-engine"
    mirror.mkdir(parents=True)
    (mirror / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return state


class TestProbeDeploy:
    def test_not_installed_is_neutral(self, tmp_path, monkeypatch):
        home = tmp_path / "empty-home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.delenv("PRISMATIC_STATE_DIR", raising=False)
        monkeypatch.delenv("PRISMATIC_DEPLOY_REPOS_FILE", raising=False)
        monkeypatch.delenv("PRISMATIC_DEPLOY_REPOS", raising=False)
        monkeypatch.setenv("PRISMATIC_DEPLOY_UNIT_DIRS", str(tmp_path / "units"))
        report = _probe_deploy()
        assert report.installed is False
        assert report.error == ""
        # Neutral: a missing deploy plane must not flip the verdict.
        assert _compute_verdict([], [], None, None, deploy=report) == "OK"

    def test_legacy_layout_without_marker_is_neutral(self, deploy_env):
        # A state dir WITHOUT the WS3 marker (grandfathered pre-WS3 layout)
        # is not judged by the WS3 layout.
        (deploy_env["state"] / ".deploy-install.json").unlink()
        report = _probe_deploy()
        assert report.installed is False
        assert _compute_verdict([], [], None, None, deploy=report) == "OK"

    def test_healthy_layout_is_green(self, deploy_env, monkeypatch):
        _make_healthy(deploy_env, monkeypatch)
        report = _probe_deploy()
        assert report.installed is True
        assert report.error == ""
        assert report.layout_dirs_missing == []
        assert len(report.layout_dirs_ok) == len(LAYOUT_SUBDIRS)
        assert report.receiver_unit_installed is True
        assert report.gateway_unit_installed is True
        assert report.hmac_secret_set is True
        assert len(report.repos) == 1
        repo = report.repos[0]
        assert repo.full_name == "mbgulden/prismatic-engine"
        assert repo.mirror_present is True
        assert repo.fetch_unit_installed is True
        assert repo.shared_secret_fallback is True
        assert repo.remediation == ""
        assert _compute_verdict([], [], None, None, deploy=report) == "OK"

    def test_explicit_per_repo_secret(self, deploy_env, monkeypatch):
        _make_healthy(deploy_env, monkeypatch, secret="")
        monkeypatch.setenv(
            "DEPLOY_HMAC_SECRET_MBGULDEN_PRISMATIC_ENGINE", "per-repo-value"
        )
        report = _probe_deploy()
        repo = report.repos[0]
        assert repo.secret_configured is True
        assert repo.shared_secret_fallback is False
        assert report.hmac_secret_set is True
        assert _compute_verdict([], [], None, None, deploy=report) == "OK"

    def test_missing_secret_is_warn_with_remediation(self, deploy_env, monkeypatch):
        _make_healthy(deploy_env, monkeypatch, secret="")
        report = _probe_deploy()
        assert report.hmac_secret_set is False
        repo = report.repos[0]
        assert repo.secret_configured is False
        assert repo.shared_secret_fallback is False
        assert "DEPLOY_HMAC_SECRET_MBGULDEN_PRISMATIC_ENGINE" in repo.remediation
        assert "DEPLOY_HMAC_SECRET" in repo.remediation
        assert _compute_verdict([], [], None, None, deploy=report) == "WARN"

    def test_missing_units_are_warn(self, deploy_env, monkeypatch):
        _make_healthy(deploy_env, monkeypatch)
        (deploy_env["units"] / "prismatic-gateway.service").unlink()
        (deploy_env["units"] / "prismatic-repo-mirror-fetch.timer").unlink()
        report = _probe_deploy()
        assert report.gateway_unit_installed is False
        assert report.repos[0].fetch_unit_installed is True  # .service still there
        assert _compute_verdict([], [], None, None, deploy=report) == "WARN"

    def test_missing_layout_dir_is_warn(self, deploy_env, monkeypatch):
        _make_healthy(deploy_env, monkeypatch)
        (deploy_env["state"] / "logs").rmdir()
        report = _probe_deploy()
        assert report.layout_dirs_missing == ["logs"]
        assert _compute_verdict([], [], None, None, deploy=report) == "WARN"

    def test_receiver_env_file_from_unit_is_honored(self, deploy_env, monkeypatch):
        # The secret lives only in the EnvironmentFile named by the
        # installed receiver unit (like the production box), not in the
        # default env.d location.
        _make_healthy(deploy_env, monkeypatch, secret="")
        (deploy_env["state"] / "env.d" / "deploy-receiver.env").unlink()
        custom = deploy_env["state"] / "custom.env"
        custom.write_text("DEPLOY_HMAC_SECRET=from-unit-file\n", encoding="utf-8")
        (deploy_env["units"] / "prismatic-deploy-receiver.service").write_text(
            "[Service]\nEnvironmentFile=" + str(custom) + "\n",
            encoding="utf-8",
        )
        report = _probe_deploy()
        assert report.hmac_secret_set is True
        assert _compute_verdict([], [], None, None, deploy=report) == "OK"

    def test_no_secret_values_leak_into_report(self, deploy_env, monkeypatch):
        _make_healthy(deploy_env, monkeypatch, secret="s3cr3t-value-xyz")
        report = _probe_deploy()
        as_json = json.dumps(report.to_dict())
        assert "s3cr3t-value-xyz" not in as_json

    def test_report_is_json_safe(self, deploy_env, monkeypatch):
        _make_healthy(deploy_env, monkeypatch)
        report = _probe_deploy()
        json.dumps(report.to_dict())  # must not raise

    def test_deploy_verdict_never_overrides_error(self, deploy_env, monkeypatch):
        # Deploy WARN must not mask an ERROR from native components.
        _make_healthy(deploy_env, monkeypatch, secret="")
        report = _probe_deploy()
        native = [
            doctor_module.CapabilityReport(
                name="gateway", status="error", message="down", required=True
            )
        ]
        assert _compute_verdict([], [], None, native, deploy=report) == "ERROR"

    def test_not_listening_is_not_an_error(self, deploy_env, monkeypatch):
        # A configured-but-not-running receiver is informational, not WARN:
        # the installer deliberately leaves units stopped.
        _make_healthy(deploy_env, monkeypatch)
        monkeypatch.setattr(doctor_module, "DEPLOY_RECEIVER_PORT", 19460)
        report = _probe_deploy()
        assert report.receiver_listening is False
        assert _compute_verdict([], [], None, None, deploy=report) == "OK"

    def test_unit_dirs_seam_defaults(self, monkeypatch):
        monkeypatch.delenv("PRISMATIC_DEPLOY_UNIT_DIRS", raising=False)
        dirs = doctor_module._deploy_unit_dirs()
        assert any(str(d).endswith("systemd/user") for d in dirs)
        assert any(str(d) == "/etc/systemd/system" for d in dirs)
