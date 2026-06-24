"""
Tests for the `prismatic providers attach` workflow.

These tests pin the contract: token storage, validation, and doctor
re-discovery after attach.
"""

from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from prismatic.providers.registry import USER_CONFIG_PATH_ENV


@contextmanager
def isolated_workspace():
    """Yield (tmp_path, env_builder) for an isolated test workspace.

    We use ``tempfile.TemporaryDirectory`` directly because pytest's
    internal ``tmp_path`` fixture was racing cleanup during assertions
    in this specific test surface. The context manager guarantees the
    directory stays alive until the test body returns.
    """
    with tempfile.TemporaryDirectory() as tdir:
        tdir_path = Path(tdir)

        def env_builder(config_path: Path, home: Path) -> dict[str, str]:
            return {USER_CONFIG_PATH_ENV: str(config_path), "HOME": str(home)}

        yield tdir_path, env_builder


def test_attach_github_writes_token_to_user_config():
    from prismatic.providers.registry import CredentialRegistry

    with isolated_workspace() as (tmp_path, env_overrides):
        config_path = tmp_path / "config.yaml"
        with patch.dict("os.environ", env_overrides(config_path, tmp_path)):
            registry = CredentialRegistry()
            with patch.object(registry, "_validate", return_value=(True, {"login": "octocat"})):
                result = registry.attach("github", token="ghp_test_token", repository="acme/widget")
            data = config_path.read_text()

    assert result.ok is True
    assert result.credential_source == "config.yaml (github.token)"
    assert "github" in data
    assert "ghp_test_token" in data
    assert "acme/widget" in data


def test_attach_rejects_invalid_github_token():
    from prismatic.providers.registry import CredentialRegistry

    with isolated_workspace() as (tmp_path, env_overrides):
        config_path = tmp_path / "config.yaml"
        with patch.dict("os.environ", env_overrides(config_path, tmp_path)):
            registry = CredentialRegistry()
            with patch.object(
                registry,
                "_validate",
                return_value=(False, {"error": "HTTP 401", "detail": {"message": "Bad credentials"}}),
            ):
                result = registry.attach("github", token="bad")

    assert result.ok is False
    assert "HTTP 401" in result.error
    assert result.remediation
    assert not config_path.exists()


def test_attach_unknown_provider_returns_validation_error():
    from prismatic.providers.registry import CredentialRegistry

    with isolated_workspace() as (tmp_path, env_overrides):
        config_path = tmp_path / "config.yaml"
        with patch.dict("os.environ", env_overrides(config_path, tmp_path)):
            registry = CredentialRegistry()
            result = registry.attach("not-a-provider", token="x")

    assert result.ok is False
    assert "Unknown provider" in result.error


def test_providers_attach_routes_to_registry():
    from prismatic import cli

    result = _FakeResult(ok=True, credential_source="config.yaml (github.token)")
    with patch("prismatic.cli._providers_attach", return_value=result) as fake_attach:
        with patch("builtins.print"):
            rc = cli.run([
                "providers", "attach", "github",
                "--token", "ghp_fake",
                "--repository", "acme/widget",
            ])

    assert rc == 0
    fake_attach.assert_called_once()
    kwargs = fake_attach.call_args.kwargs
    assert kwargs["name"] == "github"
    assert kwargs["token"] == "ghp_fake"
    assert kwargs["repository"] == "acme/widget"
    assert kwargs["config_path"] is None


def test_providers_attach_returns_error_on_validation_failure():
    from prismatic import cli

    with patch(
        "prismatic.cli._providers_attach",
        return_value=_FakeResult(ok=False, error="Invalid token", remediation="Set a real token"),
    ):
        with patch("builtins.print"):
            rc = cli.run([
                "providers", "attach", "github", "--token", "bad",
            ])
    assert rc == 1


def test_providers_list_prints_supported_providers():
    from prismatic import cli

    with patch("builtins.print") as fake_print:
        rc = cli.run(["providers", "list"])

    assert rc == 0
    printed = "\n".join(str(call.args[0]) for call in fake_print.call_args_list if call.args)
    assert "github" in printed


class _FakeResult:
    def __init__(
        self,
        *,
        ok: bool,
        error: str = "",
        remediation: str = "",
        credential_source: str = "",
        path: str = "",
    ):
        self.ok = ok
        self.error = error
        self.remediation = remediation
        self.credential_source = credential_source
        self.path = path
