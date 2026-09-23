"""Tests for WS4 receiver startup logging.

At startup the receiver logs the bind address/port and a summary of the
routed repo registry (names only, never secrets), and warns LOUDLY for each
routed repo with no HMAC secret configured. These tests verify that.
"""

import json
import logging

import pytest

from pe.deploy import receiver
from pe.deploy.receiver import log_receiver_startup_config

WIDGETS = "acme/widgets"
GADGETS = "acme/gadgets"
WIDGETS_SECRET = "widget-secret-xyz"
SHARED_SECRET = "shared-secret-abc"


@pytest.fixture
def hermetic_two_repo(monkeypatch, tmp_path):
    """Hermetic registry + secret lookup: no HOME/CWD/.env leakage."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    for var in (
        "PRISMATIC_DEPLOY_REPOS",
        "PRISMATIC_DEPLOY_REPOS_FILE",
        "PRISMATIC_ALLOW_DEFAULT_HMAC",
        "PRISMATIC_STRICT_SECRETS",
        "DEPLOY_HMAC_SECRET",
        "DEPLOY_HMAC_SECRET_ACME_WIDGETS",
        "DEPLOY_HMAC_SECRET_ACME_GADGETS",
    ):
        monkeypatch.delenv(var, raising=False)
    # Stub file-based secret lookup so the real box .env files can't leak in.
    monkeypatch.setattr(receiver, "_load_env_file", lambda path: {})

    repos = {
        WIDGETS: {"mirror_dir": str(tmp_path / "m1")},
        GADGETS: {"mirror_dir": str(tmp_path / "m2")},
    }
    repos_file = tmp_path / "repos.json"
    repos_file.write_text(json.dumps(repos), encoding="utf-8")
    monkeypatch.setenv("PRISMATIC_DEPLOY_REPOS_FILE", str(repos_file))
    # Pipeline construction (app creation) fail-fasts without a source repo.
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setenv("PRISMATIC_DEPLOY_SOURCE_REPO", str(src))
    return tmp_path


def test_startup_logs_bind_and_registry_summary(hermetic_two_repo, caplog):
    with caplog.at_level(logging.INFO, logger="pe.deploy.receiver"):
        log_receiver_startup_config()
    lines = caplog.text
    assert (
        f"deploy receiver startup: bind 0.0.0.0:9460; "
        f"routing 2 repo(s): {WIDGETS}, {GADGETS}"
    ) in lines


def test_startup_log_leaks_no_secret_values(hermetic_two_repo, caplog, monkeypatch):
    monkeypatch.setenv("DEPLOY_HMAC_SECRET_ACME_WIDGETS", WIDGETS_SECRET)
    with caplog.at_level(logging.INFO, logger="pe.deploy.receiver"):
        log_receiver_startup_config()
    assert WIDGETS_SECRET not in caplog.text
    # The var NAME appears only in the loud missing-secret warning; the
    # configured-repo INFO line names the repo, not the var. Either way the
    # secret VALUE never appears in logs.
    assert "HMAC secret configured for acme/widgets" in caplog.text


def test_missing_secret_warns_loudly_naming_repo(hermetic_two_repo, caplog):
    with caplog.at_level(logging.WARNING, logger="pe.deploy.receiver"):
        log_receiver_startup_config()
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 2
    for repo, var in (
        (WIDGETS, "DEPLOY_HMAC_SECRET_ACME_WIDGETS"),
        (GADGETS, "DEPLOY_HMAC_SECRET_ACME_GADGETS"),
    ):
        match = [r for r in warnings if repo in r.getMessage()]
        assert match, f"no warning for {repo}"
        assert "NO HMAC secret configured" in match[0].getMessage()
        assert var in match[0].getMessage()


def test_per_repo_secret_silences_warning(hermetic_two_repo, caplog, monkeypatch):
    monkeypatch.setenv("DEPLOY_HMAC_SECRET_ACME_WIDGETS", WIDGETS_SECRET)
    with caplog.at_level(logging.WARNING, logger="pe.deploy.receiver"):
        log_receiver_startup_config()
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert GADGETS in warnings[0].getMessage()
    assert WIDGETS not in warnings[0].getMessage()


def test_shared_secret_covers_all_repos(hermetic_two_repo, caplog, monkeypatch):
    monkeypatch.setenv("DEPLOY_HMAC_SECRET", SHARED_SECRET)
    with caplog.at_level(logging.WARNING, logger="pe.deploy.receiver"):
        log_receiver_startup_config()
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings == []


def test_app_creation_logs_startup_config(hermetic_two_repo, caplog, monkeypatch):
    """The startup summary fires at receiver startup (app creation), not import."""
    monkeypatch.setenv("DEPLOY_HMAC_SECRET", SHARED_SECRET)
    with caplog.at_level(logging.INFO, logger="pe.deploy.receiver"):
        app = receiver.create_deploy_receiver_app()
    assert app is not None
    assert "deploy receiver startup: bind 0.0.0.0:9460" in caplog.text
    assert "HMAC secret configured for" in caplog.text
