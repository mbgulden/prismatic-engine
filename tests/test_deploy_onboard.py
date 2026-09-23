"""Tests for pe.deploy.onboard — the guided multi-repo onboarding flow."""

from __future__ import annotations

import json
import os

import pytest

from pe.deploy import onboard
from pe.deploy.onboard import AddRepoOptions, OnboardError


class _Cp:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _ok_run(cmd, timeout=60):  # noqa: ANN001, ANN202
    return _Cp(0, stdout="deadbeef\tHEAD\n")


def _fail_run(cmd, timeout=60):  # noqa: ANN001, ANN202
    return _Cp(128, stderr="fatal: repository 'x' not found")


@pytest.fixture
def iso_env(tmp_path, monkeypatch):
    """Isolate HOME + cwd so no real registry/mirror/secret is touched."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PRISMATIC_DEPLOY_REPOS_FILE", raising=False)
    monkeypatch.delenv("PRISMATIC_DEPLOY_REPOS", raising=False)
    return tmp_path


def test_invalid_name_rejected(iso_env):  # noqa: ANN001, ANN201
    with pytest.raises(OnboardError):
        onboard.add_repo(AddRepoOptions("not-a-repo"), run=_ok_run)


def test_unreachable_repo_rejected_before_any_mutation(iso_env):  # noqa: ANN001, ANN201
    with pytest.raises(OnboardError, match="not reachable"):
        onboard.add_repo(AddRepoOptions("octo/ghost"), run=_fail_run)
    assert not (iso_env / ".prismatic" / "deploy-repos.json").exists()


def test_dry_run_changes_nothing(iso_env):  # noqa: ANN001, ANN201
    result = onboard.add_repo(AddRepoOptions("octo/repo", dry_run=True), run=_ok_run)
    assert result.ok
    assert all(s.status in ("ok", "would-do", "skipped") for s in result.steps)
    assert not (iso_env / ".prismatic" / "deploy-repos.json").exists()
    env_file = iso_env / ".prismatic" / "env.d" / "deploy-receiver.env"
    assert not env_file.exists()


def test_add_repo_registers_atomically_and_preserves_existing(iso_env):  # noqa: ANN001, ANN201
    reg = iso_env / ".prismatic" / "deploy-repos.json"
    reg.parent.mkdir(parents=True)
    reg.write_text(json.dumps({"mbgulden/prismatic-engine": {}}), encoding="utf-8")

    calls = []

    def run(cmd, timeout=60):  # noqa: ANN001, ANN202
        calls.append(cmd)
        return _Cp(0)

    result = onboard.add_repo(
        AddRepoOptions("octo/repo", no_mirror=True, registry_file=str(reg)),
        run=run,
    )
    assert result.ok
    data = json.loads(reg.read_text(encoding="utf-8"))
    assert set(data) == {"mbgulden/prismatic-engine", "octo/repo"}
    # release_prefix is always explicit: a second repo's live symlinks must
    # never fall back to the production gateway's bare links.
    assert data["octo/repo"] == {"release_prefix": "octo-repo"}
    # no clone when --no-mirror
    assert not any("clone" in c for c in calls)


def test_duplicate_refused(iso_env):  # noqa: ANN001, ANN201
    reg = iso_env / ".prismatic" / "deploy-repos.json"
    result = onboard.add_repo(
        AddRepoOptions("octo/repo", no_mirror=True, registry_file=str(reg)),
        run=_ok_run,
    )
    assert result.ok
    with pytest.raises(OnboardError, match="already in"):
        onboard.add_repo(
            AddRepoOptions("octo/repo", no_mirror=True, registry_file=str(reg)),
            run=_ok_run,
        )


def test_default_registry_gets_seeded_explicitly(iso_env):  # noqa: ANN001, ANN201
    reg = iso_env / ".prismatic" / "deploy-repos.json"
    result = onboard.add_repo(
        AddRepoOptions("octo/repo", no_mirror=True, registry_file=str(reg)),
        run=_ok_run,
    )
    assert result.ok
    data = json.loads(reg.read_text(encoding="utf-8"))
    # default single-repo registry seeded so behavior is preserved
    assert "mbgulden/prismatic-engine" in data
    assert "octo/repo" in data


def test_secret_file_permissions_and_idempotency(iso_env):  # noqa: ANN001, ANN201
    reg = iso_env / ".prismatic" / "deploy-repos.json"
    result = onboard.add_repo(
        AddRepoOptions("octo/repo", no_mirror=True, registry_file=str(reg)),
        run=_ok_run,
    )
    env_file = iso_env / ".prismatic" / "env.d" / "deploy-receiver.env"
    assert env_file.exists()
    assert oct(env_file.stat().st_mode & 0o777) == "0o600"
    first = result.secret_value
    assert len(first) == 64  # token_hex(32)
    # re-running for another repo reuses the file, generates a fresh secret
    result2 = onboard.add_repo(
        AddRepoOptions("octo/repo2", no_mirror=True, registry_file=str(reg)),
        run=_ok_run,
    )
    assert result2.secret_value != first
    assert oct(env_file.stat().st_mode & 0o777) == "0o600"
    vars_present = {
        line.split("=", 1)[0]
        for line in env_file.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    }
    assert "DEPLOY_HMAC_SECRET_OCTO_REPO" in vars_present
    assert "DEPLOY_HMAC_SECRET_OCTO_REPO2" in vars_present


def test_export_hint_when_file_var_unset(iso_env):  # noqa: ANN001, ANN201
    result = onboard.add_repo(AddRepoOptions("octo/repo", dry_run=True), run=_ok_run)
    assert "PRISMATIC_DEPLOY_REPOS_FILE" in result.registry_export_hint


def test_validate_repo_fails_loud_when_unregistered(iso_env):  # noqa: ANN001, ANN201
    result = onboard.validate_repo("octo/ghost", run=_ok_run)
    assert not result.ok
    assert any(c.name == "registry" and not c.ok for c in result.checks)


def test_validate_repo_ok_path(iso_env):  # noqa: ANN001, ANN201
    reg = iso_env / ".prismatic" / "deploy-repos.json"

    def run(cmd, timeout=60):  # noqa: ANN001, ANN202
        return _Cp(0, stdout="deadbeef\n")

    # register with mirror skipped
    result = onboard.add_repo(
        AddRepoOptions("octo/repo", no_mirror=True, registry_file=str(reg)),
        run=run,
    )
    assert result.ok
    # file var not exported yet: registry still default-only
    assert [r.full_name for r in onboard.list_repos()] == ["mbgulden/prismatic-engine"]

    # now point the registry at the file and fake the mirror
    os.environ["PRISMATIC_DEPLOY_REPOS_FILE"] = str(reg)
    mirror = iso_env / ".prismatic" / "repos" / "octo" / "repo"
    mirror.mkdir(parents=True)
    (mirror / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    result = onboard.validate_repo("octo/repo", run=run)
    assert result.ok, [c for c in result.checks if not c.ok]
    assert all(c.ok for c in result.checks)


def test_validate_repo_missing_mirror(iso_env):  # noqa: ANN001, ANN201
    reg = iso_env / ".prismatic" / "deploy-repos.json"
    onboard.add_repo(
        AddRepoOptions("octo/repo", no_mirror=True, registry_file=str(reg)),
        run=_ok_run,
    )
    os.environ["PRISMATIC_DEPLOY_REPOS_FILE"] = str(reg)
    result = onboard.validate_repo("octo/repo", run=_ok_run)
    assert not result.ok
    assert any(c.name == "mirror" and not c.ok for c in result.checks)


def test_list_repos_readonly(iso_env):  # noqa: ANN001, ANN201
    rows = onboard.list_repos()
    assert len(rows) == 1
    assert rows[0].full_name == "mbgulden/prismatic-engine"
    assert rows[0].secret in ("per-repo", "shared-fallback", "missing")
