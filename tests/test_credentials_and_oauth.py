"""Unit tests for Prismatic Engine Service Credentials & Interactive OAuth.

Verifies status inspection for Google Antigravity OAuth, Jules CLI OAuth,
managed API keys, and credential status endpoints.
"""

from __future__ import annotations

import pytest
from prismatic.credential.manager import (
    get_credentials_status,
    check_google_antigravity_oauth,
    check_jules_cli_oauth,
)


def test_check_google_antigravity_oauth():
    res = check_google_antigravity_oauth()
    assert res["service"] == "google_antigravity"
    assert res["auth_type"] == "oauth2"
    assert "fulfilled" in res
    assert isinstance(res["fulfilled"], bool)


def test_check_jules_cli_oauth():
    res = check_jules_cli_oauth()
    assert res["service"] == "jules_cli"
    assert res["auth_type"] == "oauth2"
    assert "fulfilled" in res
    assert isinstance(res["fulfilled"], bool)


def test_get_credentials_status_payload():
    status = get_credentials_status()
    assert status["ok"] is True
    assert "credentials" in status
    assert "oauth_services" in status

    oauth = status["oauth_services"]
    assert "google_antigravity" in oauth
    assert "jules_cli" in oauth
