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
    check_github_oauth,
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


def test_check_github_oauth():
    res = check_github_oauth()
    assert res["service"] == "github_oauth"
    assert res["auth_type"] == "oauth2"
    assert "fulfilled" in res
    assert isinstance(res["fulfilled"], bool)


def test_custom_services_add_and_delete():
    from prismatic.credential.manager import add_custom_service, delete_custom_service, load_custom_services

    payload = {
        "service_id": "test_gitlab_spec",
        "name": "GitLab Self-Hosted",
        "category": "oauth",
        "auth_type": "pat",
        "key": "TEST_GITLAB_TOKEN",
        "value": "glpat-123456789"
    }

    res_add = add_custom_service(payload)
    assert res_add["ok"] is True
    assert res_add["service"]["service_id"] == "test_gitlab_spec"

    custom_list = load_custom_services()
    assert any(s["service_id"] == "test_gitlab_spec" for s in custom_list)

    res_del = delete_custom_service("test_gitlab_spec")
    assert res_del["ok"] is True
    assert res_del["removed_service_id"] == "test_gitlab_spec"


def test_get_credentials_status_payload():
    status = get_credentials_status()
    assert status["ok"] is True
    assert "credentials" in status
    assert "oauth_services" in status
    assert "custom_services" in status

    oauth = status["oauth_services"]
    assert "google_antigravity" in oauth
    assert "jules_cli" in oauth
    assert "github_oauth" in oauth
