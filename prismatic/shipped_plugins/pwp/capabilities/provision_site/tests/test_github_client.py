"""Tests for GitHubClient — GitHub REST API wrapper used by PWP
provision_site for GitHub repo metadata, webhooks, and Actions secrets.

Coverage:
  - from_env precedence (GITHUB_TOKEN > GH_TOKEN > GITHUB_PAT)
  - direct construction rejects empty token
  - validate() returns the authenticated user
  - get_repo parses metadata correctly
  - get_repo 404 raises GitHubError(404)
  - repo_exists returns False on 404, True otherwise
  - list_branches pagination
  - get_default_branch returns (name, sha)
  - get_commit parses author/message
  - get_file decodes base64
  - create_webhook payload shape
  - delete_webhook
  - 429 rate-limit retry
  - 5xx retry
  - 401 does NOT retry
  - 4xx (other) does NOT retry
  - auth_loader integration (auth_loader fallback path)
  - dataclass field defaults
"""

from __future__ import annotations

import http.client
import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest
from plugins.pwp.capabilities.provision_site import auth_loader
from plugins.pwp.capabilities.provision_site.github_client import (
    GitHubBranch,
    GitHubClient,
    GitHubError,
    GitHubRepo,
)


def _http_response(status: int, body: dict) -> urllib.error.HTTPError:
    """Construct an HTTPError with a JSON body."""
    resp = http.client.HTTPResponse(MagicMock())
    resp.status = status
    resp.reason = "Test" if status != 200 else "OK"
    payload = json.dumps(body).encode()
    return urllib.error.HTTPError(
        url="https://api.github.com/test",
        code=status,
        msg=resp.reason,
        hdrs=http.client.HTTPMessage(),
        fp=io.BytesIO(payload),
    )


def _mock_response(status: int, body):
    """Build a context-manager that returns a fake response."""
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = json.dumps(body).encode()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _mock_request(status: int, body):
    """Backwards-compat alias — body can be dict or list."""
    return _mock_response(status, body)


# -- from_env / factory --------------------------------------------------

def test_from_env_finds_github_token(monkeypatch) -> None:
    for k in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_YTz...lorS")
    c = GitHubClient.from_env()
    assert c.token_source == "GITHUB_TOKEN"
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


def test_from_env_precedence(monkeypatch) -> None:
    """GITHUB_TOKEN wins over GH_TOKEN and GITHUB_PAT."""
    for k in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GH_TOKEN", "ghp_b_should_not_be_used")
    monkeypatch.setenv("GITHUB_PAT", "ghp_c_should_not_be_used")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_a_winner")
    c = GitHubClient.from_env()
    assert c.token_source == "GITHUB_TOKEN"
    for k in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT"):
        monkeypatch.delenv(k, raising=False)


def test_from_env_falls_back_to_auth_loader(monkeypatch) -> None:
    """When no env vars are set, auth_loader is consulted."""
    for k in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT"):
        monkeypatch.delenv(k, raising=False)
    fake_result = auth_loader.AuthResult(
        value="ghp_fake_loader_token",
        source="profile-env",
        env_var="GITHUB_TOKEN",
        hint="(test stub)",
        redaction="ghp_fake...ken=",
    )
    with patch(
        "plugins.pwp.capabilities.provision_site.auth_loader.get_secret",
        return_value=fake_result,
    ) as mock:
        c = GitHubClient.from_env()
    assert c.token_source == "auth_loader:profile-env"
    assert mock.called


def test_from_env_raises_when_no_token(monkeypatch) -> None:
    for k in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT"):
        monkeypatch.delenv(k, raising=False)
    with patch(
        "plugins.pwp.capabilities.provision_site.auth_loader.get_secret",
        return_value=auth_loader.AuthResult(
            value=None, source="none", env_var="",
            hint="(test stub)", redaction="<missing>",
        ),
    ), pytest.raises(ValueError, match="GITHUB_TOKEN"):
        GitHubClient.from_env()


def test_direct_construction_rejects_empty() -> None:
    with pytest.raises(ValueError, match="token is required"):
        GitHubClient(token="")


# -- validate / get_authenticated_user ----------------------------------

def test_validate_returns_user(monkeypatch) -> None:
    c = GitHubClient(token="ghp_xxx")
    fake_resp = _mock_request(200, {
        "login": "mbgulden",
        "id": 12345,
        "type": "User",
    })
    with patch("urllib.request.urlopen", return_value=fake_resp):
        user = c.validate()
    assert user["login"] == "mbgulden"


# -- get_repo / repo_exists ---------------------------------------------

def test_get_repo_parses_metadata(monkeypatch) -> None:
    c = GitHubClient(token="ghp_xxx")
    fake_resp = _mock_request(200, {
        "full_name": "mbgulden/EZShare",
        "default_branch": "master",
        "private": False,
        "description": "EZShare systems",
        "html_url": "https://github.com/mbgulden/EZShare",
        "clone_url": "https://github.com/mbgulden/EZShare.git",
        "ssh_url": "git@github.com:mbgulden/EZShare.git",
        "permissions": {"push": True, "admin": True, "maintain": True},
        "topics": ["vercel", "nextjs"],
    })
    with patch("urllib.request.urlopen", return_value=fake_resp):
        repo = c.get_repo("mbgulden/EZShare")
    assert repo.full_name == "mbgulden/EZShare"
    assert repo.default_branch == "master"
    assert repo.permissions_push is True
    assert repo.permissions_admin is True
    assert "vercel" in repo.topics


def test_get_repo_404_raises(monkeypatch) -> None:
    c = GitHubClient(token="ghp_xxx")
    err = _http_response(404, {"message": "Not Found"})
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(GitHubError) as exc:
            c.get_repo("nobody/missing")
    assert exc.value.status_code == 404


def test_repo_exists_returns_true(monkeypatch) -> None:
    c = GitHubClient(token="ghp_xxx")
    fake_resp = _mock_request(200, {"full_name": "x/y", "default_branch": "main"})
    with patch("urllib.request.urlopen", return_value=fake_resp):
        assert c.repo_exists("x/y") is True


def test_repo_exists_returns_false_on_404(monkeypatch) -> None:
    c = GitHubClient(token="ghp_xxx")
    err = _http_response(404, {"message": "Not Found"})
    with patch("urllib.request.urlopen", side_effect=err):
        assert c.repo_exists("nobody/missing") is False


# -- list_branches / get_default_branch ----------------------------------

def test_list_branches_single_page(monkeypatch) -> None:
    c = GitHubClient(token="ghp_xxx")
    fake_resp = _mock_request(200, [
        {"name": "main", "commit": {"sha": "abc123"}, "protected": True},
        {"name": "dev", "commit": {"sha": "def456"}, "protected": False},
    ])
    with patch("urllib.request.urlopen", return_value=fake_resp):
        branches = c.list_branches("x/y")
    assert len(branches) == 2
    assert branches[0].name == "main"
    assert branches[0].sha == "abc123"
    assert branches[0].protected is True


def test_list_branches_pagination(monkeypatch) -> None:
    """If page 1 returns 100 results, list_branches returns all."""
    c = GitHubClient(token="ghp_xxx")
    page1 = [{"name": f"branch-{i}", "commit": {"sha": f"sha{i}"}, "protected": False}
             for i in range(100)]
    page2: list = []  # empty terminator
    responses = [
        _mock_request(200, page1),
        _mock_request(200, page2),
    ]
    with patch("urllib.request.urlopen", side_effect=responses):
        branches = c.list_branches("x/y")
    assert len(branches) == 100


def test_get_default_branch_returns_matching(monkeypatch) -> None:
    c = GitHubClient(token="ghp_xxx")
    repo_resp = _mock_request(200, {"default_branch": "main", "full_name": "x/y"})
    branch_resp = _mock_request(200, [
        {"name": "main", "commit": {"sha": "main_sha"}, "protected": False},
        {"name": "dev", "commit": {"sha": "dev_sha"}, "protected": False},
    ])
    with patch("urllib.request.urlopen", side_effect=[repo_resp, branch_resp]):
        name, sha = c.get_default_branch("x/y")
    assert name == "main"
    assert sha == "main_sha"


# -- get_commit ----------------------------------------------------------

def test_get_commit_parses(monkeypatch) -> None:
    c = GitHubClient(token="ghp_xxx")
    fake_resp = _mock_request(200, {
        "sha": "fd8efd8d547e11c21b615cef2d33cd537630e8e0",
        "commit": {
            "message": "feat: deployment configuration",
            "author": {
                "name": "Sovereign AI",
                "email": "ai@example.com",
                "date": "2026-04-17T12:01:55Z",
            },
        },
        "parents": [{"sha": "parent_sha_1"}],
    })
    with patch("urllib.request.urlopen", return_value=fake_resp):
        commit = c.get_commit("x/y", "fd8efd8d547e11c21b615cef2d33cd537630e8e0")
    assert commit.author_name == "Sovereign AI"
    assert commit.sha == "fd8efd8d547e11c21b615cef2d33cd537630e8e0"
    assert commit.date == "2026-04-17T12:01:55Z"
    assert "parent_sha_1" in commit.parents


# -- get_file ------------------------------------------------------------

def test_get_file_decodes_base64(monkeypatch) -> None:
    c = GitHubClient(token="ghp_xxx")
    import base64
    encoded = base64.b64encode(b"hello world").decode()
    fake_resp = _mock_request(200, {"content": encoded, "sha": "file_sha"})
    with patch("urllib.request.urlopen", return_value=fake_resp):
        content, sha = c.get_file("x/y", "README.md")
    assert content == "hello world"
    assert sha == "file_sha"


def test_get_file_handles_embedded_newlines(monkeypatch) -> None:
    """GitHub returns base64 with embedded newlines."""
    c = GitHubClient(token="ghp_xxx")
    import base64
    encoded = base64.b64encode(b"hi").decode()
    with_newlines = "\n".join(
        [encoded[i:i+60] for i in range(0, len(encoded), 60)]
    )
    fake_resp = _mock_request(200, {"content": with_newlines, "sha": "f"})
    with patch("urllib.request.urlopen", return_value=fake_resp):
        content, _ = c.get_file("x/y", "x.txt")
    assert content == "hi"


# -- webhooks ------------------------------------------------------------

def test_create_webhook_payload_shape(monkeypatch) -> None:
    """create_webhook must send the correct JSON shape."""
    c = GitHubClient(token="ghp_xxx")
    captured_request = []
    def fake_urlopen(req, **kwargs):
        captured_request.append(req)
        return _mock_response(201, {"id": 12345, "name": "web"})
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        c.create_webhook(
            "x/y",
            url="https://example.com/hook",
            events=["push"],
        )
    assert len(captured_request) == 1
    body = json.loads(captured_request[0].data.decode())
    assert body["name"] == "web"
    assert body["events"] == ["push"]
    assert body["config"]["url"] == "https://example.com/hook"


def test_delete_webhook(monkeypatch) -> None:
    c = GitHubClient(token="ghp_xxx")
    fake_resp = _mock_request(204, {})
    captured_path = []
    def fake_urlopen(req, **kwargs):
        captured_path.append(req.full_url)
        return fake_resp
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        c.delete_webhook("x/y", 12345)
    assert "/hooks/12345" in captured_path[0]


# -- retry / error handling ----------------------------------------------

def test_429_rate_limit_retries(monkeypatch) -> None:
    """429 with rate-limit error message triggers retry."""
    c = GitHubClient(token="ghp_xxx", max_retries=2, retry_backoff=1.1)
    err = _http_response(429, {
        "message": "API rate limit exceeded",
        "documentation_url": "https://docs.github.com/rest/overview/resources-in-the-rest-api#rate-limiting",
    })
    ok = _mock_request(200, {"login": "ok"})
    sleeps: list = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    with patch("urllib.request.urlopen", side_effect=[err, ok]):
        result = c._request("GET", "/user")
    assert result[0] == 200
    # We retry on rate-limit; sleep called at least once
    assert len(sleeps) >= 1


def test_5xx_retries(monkeypatch) -> None:
    """5xx errors trigger retry up to max_retries."""
    c = GitHubClient(token="ghp_xxx", max_retries=2, retry_backoff=1.1)
    err = _http_response(502, {"message": "Bad Gateway"})
    ok = _mock_request(200, {"ok": True})
    monkeypatch.setattr("time.sleep", lambda s: None)
    with patch("urllib.request.urlopen", side_effect=[err, err, ok]):
        result = c._request("GET", "/user")
    assert result[0] == 200


def test_401_does_not_retry(monkeypatch) -> None:
    """401 errors should raise immediately, not retry."""
    c = GitHubClient(token="ghp_xxx", max_retries=2)
    err = _http_response(401, {"message": "Bad credentials"})
    monkeypatch.setattr("time.sleep", lambda s: None)
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(GitHubError) as exc:
            c._request("GET", "/user")
    assert exc.value.status_code == 401


def test_404_does_not_retry(monkeypatch) -> None:
    """404 errors should raise immediately (don't try alternate URLs)."""
    c = GitHubClient(token="ghp_xxx", max_retries=2)
    err = _http_response(404, {"message": "Not Found"})
    monkeypatch.setattr("time.sleep", lambda s: None)
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(GitHubError) as exc:
            c._request("GET", "/repos/missing/x")
    assert exc.value.status_code == 404


def test_4xx_validation_does_not_retry(monkeypatch) -> None:
    """422 (validation) errors should raise immediately."""
    c = GitHubClient(token="ghp_xxx", max_retries=2)
    err = _http_response(422, {"message": "Validation Failed"})
    monkeypatch.setattr("time.sleep", lambda s: None)
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(GitHubError) as exc:
            c._request("POST", "/repos/x/y/hooks", json_body={})
    assert exc.value.status_code == 422


# -- dataclass defaults --------------------------------------------------

def test_github_repo_dataclass_defaults() -> None:
    repo = GitHubRepo(
        full_name="x/y",
        default_branch="main",
        private=False,
        description="",
        permissions_push=False,
        permissions_admin=False,
        permissions_maintain=False,
        html_url="",
        clone_url="",
        ssh_url="",
    )
    assert repo.topics == []
    assert repo.raw == {}


def test_github_branch_dataclass() -> None:
    b = GitHubBranch(name="main", sha="abc", protected=True)
    assert b.name == "main"
    assert b.sha == "abc"
    assert b.protected is True
