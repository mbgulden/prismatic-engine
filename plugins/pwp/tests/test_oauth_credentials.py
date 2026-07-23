from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.pwp.oauth_credentials import (  # noqa: E402
    CredentialRefreshError,
    PROVIDERS,
    TokenPaths,
    default_token_paths,
    refresh_oauth_token,
    validate_token_shape,
)
from plugins.pwp.plugin import PWPDesignTokenPlugin  # noqa: E402


# Deliberately synthetic shape-valid fixtures. These are not provider-issued values.
SYNTHETIC_ACCESS = "ubs_oauth2_synthetic_access_" + "A" * 32
SYNTHETIC_REFRESH = "ubs_oauth2_synthetic_refresh_" + "R" * 30
SYNTHETIC_NEW_ACCESS = "ubs_oauth2_synthetic_new_access_" + "B" * 28
SYNTHETIC_NEW_REFRESH = "ubs_oauth2_synthetic_new_refresh_" + "S" * 26


@pytest.fixture(autouse=True)
def _synthetic_provider_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep every credential-provider test inside pytest-owned synthetic paths.

    No test may inherit a developer's configured provider paths or HOME directory.
    Refresh calls receive a fake post function and subprocesses receive an explicit,
    minimal environment; the test suite never invokes live verification.
    """
    for name in (
        "UBERSUGGEST_ACCESS_TOKEN_FILE",
        "UBERSUGGEST_REFRESH_TOKEN_FILE",
        "UBERSUGGEST_REFRESH_RESPONSE_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "synthetic-home"))
    monkeypatch.setenv("UBERSUGGEST_ACCESS_TOKEN_FILE", str(tmp_path / "synthetic-access"))
    monkeypatch.setenv("UBERSUGGEST_REFRESH_TOKEN_FILE", str(tmp_path / "synthetic-refresh"))
    monkeypatch.setenv("UBERSUGGEST_REFRESH_RESPONSE_FILE", str(tmp_path / "synthetic-response.json"))


def _paths(tmp_path: Path) -> TokenPaths:
    return TokenPaths(
        access_token=tmp_path / "ubs_token",
        refresh_token=tmp_path / "ubs_refresh",
        response_json=tmp_path / "ubs_response.json",
    )


def test_refresh_oauth_token_rotates_tokens_without_returning_secret_material(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.access_token.write_text(SYNTHETIC_ACCESS, encoding="utf-8")
    paths.refresh_token.write_text(SYNTHETIC_REFRESH, encoding="utf-8")
    calls: list[tuple[str, Mapping[str, str], float]] = []

    def fake_post(url: str, data: Mapping[str, str], timeout: float) -> Mapping[str, Any]:
        calls.append((url, data, timeout))
        return {
            "token_type": "Bearer",
            "access_token": SYNTHETIC_NEW_ACCESS,
            "refresh_token": SYNTHETIC_NEW_REFRESH,
            "expires_in": 172800,
            "scope": "profile domain keywords serp backlinks site_audit content",
        }

    result = refresh_oauth_token(
        PROVIDERS["ubersuggest"],
        paths,
        timeout=7,
        http_post=fake_post,
        verifier=lambda token: {"verified_token_len": len(token)},
    )

    assert calls == [
        (
            "https://ubersuggest-mcp.neilpatelapi.com/token",
            {
                "grant_type": "refresh_token",
                "client_id": "ubersuggest-mcp",
                "refresh_token": SYNTHETIC_REFRESH,
            },
            7,
        )
    ]
    assert paths.access_token.read_text(encoding="utf-8") == SYNTHETIC_NEW_ACCESS
    assert paths.refresh_token.read_text(encoding="utf-8") == SYNTHETIC_NEW_REFRESH
    assert paths.response_json is not None
    assert json.loads(paths.response_json.read_text(encoding="utf-8"))["expires_in"] == 172800
    public = result.public_dict()
    assert public == {
        "status": "ok",
        "provider": "ubersuggest",
        "saved_access_len": len(SYNTHETIC_NEW_ACCESS),
        "saved_refresh_len": len(SYNTHETIC_NEW_REFRESH),
        "expires_in": 172800,
        "scope": "profile domain keywords serp backlinks site_audit content",
        "verified": {"verified_token_len": len(SYNTHETIC_NEW_ACCESS)},
    }
    assert SYNTHETIC_NEW_ACCESS not in json.dumps(public)
    assert SYNTHETIC_NEW_REFRESH not in json.dumps(public)


def test_refresh_rejects_mangled_refresh_token_before_network_call(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.refresh_token.write_text("ubs_oa...9ytj", encoding="utf-8")

    def should_not_call(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("network should not be called for mangled tokens")

    with pytest.raises(CredentialRefreshError, match="ellipsis"):
        refresh_oauth_token(
            PROVIDERS["ubersuggest"],
            paths,
            http_post=should_not_call,
        )


def test_refresh_rejects_endpoint_response_without_new_refresh_token(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.refresh_token.write_text(SYNTHETIC_REFRESH, encoding="utf-8")

    with pytest.raises(CredentialRefreshError, match=r"access\+refresh"):
        refresh_oauth_token(
            PROVIDERS["ubersuggest"],
            paths,
            http_post=lambda *_args: {"access_token": SYNTHETIC_NEW_ACCESS},
        )


def test_validate_token_shape_rejects_wrong_provider_prefix() -> None:
    with pytest.raises(CredentialRefreshError, match="unexpected prefix"):
        validate_token_shape(
            "not_ubersuggest_" + "x" * 50,
            label="access",
            provider=PROVIDERS["ubersuggest"],
        )


def test_pwp_plugin_registers_credential_tools() -> None:
    plugin = PWPDesignTokenPlugin()
    tools = plugin.register_tools()
    names = {tool["name"] for tool in tools}

    assert "pwp_credentials_refresh" in names
    assert "pwp_credentials_status" in names
    refresh_tool = next(tool for tool in tools if tool["name"] == "pwp_credentials_refresh")
    assert refresh_tool["parameters"]["properties"]["provider"]["enum"] == ["ubersuggest"]


def test_pwp_plugin_credentials_status_uses_registered_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access = tmp_path / "access"
    refresh = tmp_path / "refresh"
    access.write_text(SYNTHETIC_ACCESS, encoding="utf-8")
    refresh.write_text(SYNTHETIC_REFRESH, encoding="utf-8")
    monkeypatch.setenv("UBERSUGGEST_ACCESS_TOKEN_FILE", str(access))
    monkeypatch.setenv("UBERSUGGEST_REFRESH_TOKEN_FILE", str(refresh))

    payload = PWPDesignTokenPlugin().credentials_status("ubersuggest")

    assert payload == {
        "status": "ok",
        "provider": "ubersuggest",
        "access_token_len": len(SYNTHETIC_ACCESS),
        "refresh_token_len": len(SYNTHETIC_REFRESH),
    }


def test_repo_local_pwp_credentials_status_command_validates_temp_tokens(tmp_path: Path) -> None:
    access = tmp_path / "access"
    refresh = tmp_path / "refresh"
    access.write_text(SYNTHETIC_ACCESS, encoding="utf-8")
    refresh.write_text(SYNTHETIC_REFRESH, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "scripts/pwp", "credentials", "status", "ubersuggest"],
        cwd=_REPO_ROOT,
        env={
            "UBERSUGGEST_ACCESS_TOKEN_FILE": str(access),
            "UBERSUGGEST_REFRESH_TOKEN_FILE": str(refresh),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "ok"
    assert payload["provider"] == "ubersuggest"
    assert payload["access_token_len"] == len(SYNTHETIC_ACCESS)
    assert payload["refresh_token_len"] == len(SYNTHETIC_REFRESH)
    assert SYNTHETIC_ACCESS not in completed.stdout
    assert SYNTHETIC_REFRESH not in completed.stdout


def test_repo_local_pwp_credentials_status_command_fails_for_mangled_token(tmp_path: Path) -> None:
    access = tmp_path / "access"
    refresh = tmp_path / "refresh"
    access.write_text("ubs_oa...9ytj", encoding="utf-8")
    refresh.write_text(SYNTHETIC_REFRESH, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "scripts/pwp", "credentials", "status", "ubersuggest"],
        cwd=_REPO_ROOT,
        env={
            "UBERSUGGEST_ACCESS_TOKEN_FILE": str(access),
            "UBERSUGGEST_REFRESH_TOKEN_FILE": str(refresh),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "ellipsis" in completed.stderr


def test_default_paths_are_confined_to_synthetic_pytest_directory(tmp_path: Path) -> None:
    paths = default_token_paths("ubersuggest")

    assert paths.access_token.parent == tmp_path
    assert paths.refresh_token.parent == tmp_path
    assert paths.response_json is not None
    assert paths.response_json.parent == tmp_path
