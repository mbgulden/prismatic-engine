"""WS3 installer tests: pe.deploy.install.

Hermetic: a FakeManager records unit installs/enables (never touches
systemd), a FakeRun fakes subprocess, port probes are stubbed free (the
live box really holds 9460/9000), and HOME is isolated per test.
"""

import json
import os
import stat
import types
from pathlib import Path

import pytest

from pe.deploy import install as installer


class FakeManager:
    """Stand-in for the WS2 process manager: records, never touches systemd."""

    def __init__(self):
        self.installed: list[tuple[str, str]] = []  # (unit name, scope)
        self.enabled: list[tuple[str, str]] = []  # (service, scope)

    def install_unit(self, name, content, *, scope="system"):
        self.installed.append((name, scope))
        return f"/fake/{scope}/{name}"

    def enable(self, service, *, scope="system"):
        self.enabled.append((service, scope))


class FakeRun:
    """Subprocess stand-in: records argv, reports success."""

    def __init__(self):
        self.argv_list: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.argv_list.append(list(argv))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("PRISMATIC_STATE_DIR", raising=False)
    monkeypatch.delenv("PRISMATIC_DEPLOY_REPOS_FILE", raising=False)
    monkeypatch.delenv("PRISMATIC_DEPLOY_REPOS", raising=False)
    return home


@pytest.fixture()
def hermetic_ports(monkeypatch):
    """Stub port probes free: the live box really holds 9460/9000."""
    monkeypatch.setattr(installer, "_port_free", lambda port: True)


def _options(**kw):
    base: dict = {"unit_scope": "user", "skip_steps": ("mirror", "venvs")}
    base.update(kw)
    return installer.InstallOptions(**base)


def _run_install(manager=None, run=None, **kw):
    manager = manager if manager is not None else FakeManager()
    run = run if run is not None else FakeRun()
    result = installer.run_install(_options(**kw), manager=manager, run=run)
    return result, manager, run


def _state_dir(home: Path) -> Path:
    return home / ".prismatic"


class TestLayoutAndUnits:
    def test_install_creates_layout_and_marker(self, fake_home, hermetic_ports):
        result, _, _ = _run_install()
        assert result.ok is True
        state = _state_dir(fake_home)
        for sub in installer.LAYOUT_SUBDIRS:
            assert (state / sub).is_dir(), sub
        assert stat.S_IMODE(os.stat(state).st_mode) == 0o700
        marker = json.loads((state / installer.INSTALL_MARKER_NAME).read_text())
        assert marker["installer"] == "pe.deploy.install"
        assert marker["install_version"] == installer.INSTALL_VERSION

    def test_units_installed_and_enabled_with_user_scope(
        self, fake_home, hermetic_ports
    ):
        result, manager, _ = _run_install()
        assert result.ok is True
        installed = dict(manager.installed)
        assert installed[installer.RECEIVER_UNIT_NAME] == "user"
        assert installed[installer.GATEWAY_UNIT_NAME] == "user"
        enabled = dict(manager.enabled)
        assert enabled[installer.RECEIVER_UNIT_NAME] == "user"
        assert enabled[installer.GATEWAY_UNIT_NAME] == "user"
        # Per-repo fetch service + timer for the default repo.
        assert ("prismatic-repo-mirror-fetch.service", "user") in manager.installed
        assert ("prismatic-repo-mirror-fetch.timer", "user") in manager.installed
        assert ("prismatic-repo-mirror-fetch.timer", "user") in manager.enabled

    def test_receiver_unit_content_user_scope(self, fake_home, hermetic_ports):
        contents: dict[str, str] = {}

        class Recording(FakeManager):
            def install_unit(self, name, content, *, scope="system"):
                contents[name] = content
                return super().install_unit(name, content, scope=scope)

        _run_install(manager=Recording())
        receiver = contents[installer.RECEIVER_UNIT_NAME]
        assert "{{" not in receiver
        assert "WantedBy=default.target" in receiver
        assert "User=" not in receiver  # user units must not set User=

    def test_system_scope_renders_user_directive(self, fake_home, hermetic_ports):
        contents: dict[str, str] = {}

        class Recording(FakeManager):
            def install_unit(self, name, content, *, scope="system"):
                contents[name] = content
                return super().install_unit(name, content, scope=scope)

        _run_install(manager=Recording(), unit_scope="system")
        receiver = contents[installer.RECEIVER_UNIT_NAME]
        assert "WantedBy=multi-user.target" in receiver
        assert "User=" in receiver

    def test_mirror_clone_uses_git_mirror(self, fake_home, hermetic_ports):
        _, _, run = _run_install(skip_steps=("venvs",))
        clones = [a for a in run.argv_list if a[:2] == ["git", "clone"]]
        assert clones, "expected a git clone call"
        assert clones[0][2] == "--mirror"
        assert "mbgulden/prismatic-engine" in clones[0][3]

    def test_secret_generated_with_0600(self, fake_home, hermetic_ports):
        _run_install()
        env_file = _state_dir(fake_home) / installer.RECEIVER_ENV_RELATIVE
        text = env_file.read_text(encoding="utf-8")
        assert "DEPLOY_HMAC_SECRET=" in text
        assert "prismatic-dev-secret" not in text
        assert stat.S_IMODE(os.stat(env_file).st_mode) == 0o600

    def test_secrets_differ_between_installs(
        self, fake_home, hermetic_ports, tmp_path, monkeypatch
    ):
        _run_install()
        first = (_state_dir(fake_home) / installer.RECEIVER_ENV_RELATIVE).read_text()
        home2 = tmp_path / "home2"
        home2.mkdir()
        monkeypatch.setenv("HOME", str(home2))
        _run_install()
        second = (_state_dir(home2) / installer.RECEIVER_ENV_RELATIVE).read_text()
        assert first != second

    def test_skip_steps_honored(self, fake_home, hermetic_ports):
        result, manager, _ = _run_install(
            skip_steps=("mirror", "venvs", "units", "fetch_timer", "secrets")
        )
        assert result.ok is True
        by_name = {s.name: s.status for s in result.steps}
        assert by_name["units"] == "skipped"
        assert by_name["secrets"] == "skipped"
        assert by_name["prerequisites"] == "ok"
        assert manager.installed == []
        assert manager.enabled == []


class TestPrerequisites:
    def test_free_ports_pass(self, fake_home, monkeypatch):
        monkeypatch.setattr(installer, "_port_free", lambda port: True)
        step = installer._check_prerequisites(
            _options(), FakeManager(), _state_dir(fake_home)
        )
        assert step.name == installer.STEP_PREREQUISITES
        assert step.status == "ok"

    def test_occupied_port_fails(self, fake_home, monkeypatch):
        monkeypatch.setattr(installer, "_port_free", lambda port: port != 9460)
        with pytest.raises(installer.InstallError, match="9460"):
            installer._check_prerequisites(
                _options(), FakeManager(), _state_dir(fake_home)
            )

    def test_dry_run_skips_port_checks(self, fake_home, monkeypatch):
        # Even with every port "occupied", dry-run prerequisites pass.
        monkeypatch.setattr(installer, "_port_free", lambda port: False)
        step = installer._check_prerequisites(
            _options(dry_run=True), FakeManager(), _state_dir(fake_home)
        )
        assert step.status == "ok"

    def test_port_free_detects_bound_port(self):
        import socket

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        try:
            assert installer._port_free(s.getsockname()[1]) is False
        finally:
            s.close()


class TestRefusal:
    def test_second_install_is_refused(self, fake_home, hermetic_ports):
        first, _, _ = _run_install()
        assert first.ok is True
        with pytest.raises(installer.InstallRefused):
            _run_install()

    def test_main_returns_2_on_refusal(
        self, fake_home, hermetic_ports, monkeypatch, capsys
    ):
        monkeypatch.setattr(installer, "_default_manager", lambda: FakeManager())
        _run_install()
        rc = installer.main(["--home", str(fake_home)])
        assert rc == 2
        assert "refused" in capsys.readouterr().err.lower()


class TestDryRun:
    def test_dry_run_writes_nothing(self, fake_home, monkeypatch, capsys):
        monkeypatch.setattr(installer, "_default_manager", lambda: FakeManager())
        rc = installer.main(["--home", str(fake_home), "--dry-run"])
        assert rc == 0
        assert not _state_dir(fake_home).exists()
        assert "would-do" in capsys.readouterr().out


class TestTemplates:
    def _mapping(self, name: str) -> dict[str, str]:
        common = {
            "STATE_DIR": "/home/u/.prismatic",
            "HOME_DIR": "/home/u",
            "ENV_FILE": "/home/u/.prismatic/env.d/deploy-receiver.env",
            "WANTED_BY": "default.target",
            "USER_DIRECTIVE": "",
        }
        if name == "receiver.service.template":
            return {
                **common,
                "RECEIVER_PORT": "9460",
                "RECEIVER_VENV": "/home/u/.prismatic/venv_receiver",
                "DEPLOY_SOURCE_REPO": (
                    "/home/u/.prismatic/repos/mbgulden/prismatic-engine"
                ),
            }
        if name == "gateway.service.template":
            return {
                **common,
                "GATEWAY_PORT": "9000",
                "GATEWAY_VENV": "/home/u/.prismatic/venv_gateway",
            }
        if name == "mirror-fetch.service.template":
            return {
                "REPO_FULL_NAME": "mbgulden/prismatic-engine",
                "MIRROR_DIR": "/home/u/.prismatic/repos/mbgulden/prismatic-engine",
                "GIT_BIN": "/usr/bin/git",
            }
        return {
            "REPO_FULL_NAME": "mbgulden/prismatic-engine",
            "FETCH_SERVICE_NAME": "prismatic-repo-mirror-fetch.service",
        }

    def test_all_templates_render_without_leftovers(self):
        for name in (
            "receiver.service.template",
            "gateway.service.template",
            "mirror-fetch.service.template",
            "mirror-fetch.timer.template",
        ):
            rendered = installer.render_template(name, self._mapping(name))
            assert "{{" not in rendered, name
            assert "}}" not in rendered, name

    def test_empty_user_directive_line_dropped(self):
        rendered = installer.render_template(
            "receiver.service.template", self._mapping("receiver.service.template")
        )
        assert "User=" not in rendered
        assert all(line.strip() for line in rendered.splitlines())

    def test_missing_placeholder_raises(self):
        with pytest.raises(installer.InstallError, match="no value provided"):
            installer.render_template("receiver.service.template", {})


class TestFetchUnitNames:
    def test_default_repo_keeps_legacy_name(self):
        assert (
            installer.fetch_unit_base(
                "mbgulden/prismatic-engine", "mbgulden/prismatic-engine"
            )
            == "prismatic-repo-mirror-fetch"
        )

    def test_extra_repo_gets_slug(self):
        assert (
            installer.fetch_unit_base("someone/cool-repo", "mbgulden/prismatic-engine")
            == "prismatic-repo-mirror-fetch-someone-cool-repo"
        )


class TestChecklist:
    def test_checklist_names_repo_and_secret_vars(self, fake_home):
        from pe.deploy import config as deploy_config

        registry = deploy_config.load_repo_registry()
        text = installer.render_checklist(registry, 9460)
        assert "mbgulden/prismatic-engine" in text
        assert "DEPLOY_HMAC_SECRET" in text
        assert "9460" in text


class TestAuthStep:
    def test_auth_step_writes_instance_json(self, fake_home, hermetic_ports):
        result, _, _ = _run_install(
            auth_provider="tailnet-only",
            admin_identities=("michael@example.com",),
        )
        assert result.ok is True
        names = [step.name for step in result.steps]
        assert "auth" in names
        instance_path = _state_dir(fake_home) / "instance.json"
        document = json.loads(instance_path.read_text())
        assert document["version"] == 1
        assert document["auth_provider"] == "tailnet-only"
        assert document["identity_roles"] == {"michael@example.com": "admin"}
        assert len(document["instance_id"]) == 32
        assert stat.S_IMODE(os.stat(instance_path).st_mode) == 0o600

    def test_auth_step_defaults_to_cloudflare_access(self, fake_home, hermetic_ports):
        result, _, _ = _run_install()
        assert result.ok is True
        document = json.loads((_state_dir(fake_home) / "instance.json").read_text())
        assert document["auth_provider"] == "cloudflare-access"
        assert document["identity_roles"] == {}

    def test_unknown_auth_provider_refused(self, fake_home, hermetic_ports):
        with pytest.raises(installer.InstallRefused):
            _run_install(auth_provider="okta")

    def test_basic_auth_without_users_refused(self, fake_home, hermetic_ports):
        with pytest.raises(installer.InstallRefused):
            _run_install(auth_provider="basic-auth")

    def test_basic_auth_writes_users_file(self, fake_home, hermetic_ports, monkeypatch):
        monkeypatch.setenv("PRISMATIC_BASIC_AUTH_PASSWORD", "s3cret")
        result, _, _ = _run_install(
            auth_provider="basic-auth",
            basic_auth_users=("ops",),
            admin_identities=("ops",),
        )
        assert result.ok is True
        users_path = _state_dir(fake_home) / "auth" / "basic-auth-users.json"
        document = json.loads(users_path.read_text())
        assert document["version"] == 1
        assert [u["username"] for u in document["users"]] == ["ops"]
        assert "s3cret" not in users_path.read_text()
        assert stat.S_IMODE(os.stat(users_path).st_mode) == 0o600
        instance_doc = json.loads((_state_dir(fake_home) / "instance.json").read_text())
        assert instance_doc["identity_roles"] == {"ops": "admin"}

    def test_auth_step_dry_run_writes_nothing(self, fake_home, hermetic_ports):
        result, _, _ = _run_install(
            dry_run=True,
            auth_provider="basic-auth",
            basic_auth_users=("ops",),
            admin_identities=("michael@example.com",),
        )
        assert result.ok is True
        auth_steps = [s for s in result.steps if s.name == "auth"]
        assert len(auth_steps) == 1
        assert auth_steps[0].status == "would-do"
        assert not (_state_dir(fake_home) / "instance.json").exists()

    def test_auth_step_skippable(self, fake_home, hermetic_ports):
        result, _, _ = _run_install(skip_steps=("mirror", "venvs", "auth"))
        assert result.ok is True
        auth_steps = [s for s in result.steps if s.name == "auth"]
        assert auth_steps[0].status == "skipped"
        assert not (_state_dir(fake_home) / "instance.json").exists()

    def test_second_install_still_refused(self, fake_home, hermetic_ports):
        result, _, _ = _run_install()
        assert result.ok is True
        with pytest.raises(installer.InstallRefused):
            _run_install()
