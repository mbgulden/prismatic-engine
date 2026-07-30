"""Tests for step_github_checkout (Phase 4.1).

Coverage:
  - resolution rule 1: explicit github_repo_full_name from prior_outputs
  - resolution rule 2: kpi-collections.json external_sources.github.repo
  - resolution rule 3: convention "<login>/<slug>" when neither above match
  - resolution rule 4: search user repos when convention misses
  - missing credentials → soft_failure with reason=missing_credentials
  - 401 auth_failed → soft_failure with reason=auth_failed
  - 404 repo_not_found → soft_failure with reason=repo_not_found
  - happy path: persists repo/branch/head_sha to kpi-collections.json
  - happy path: returns StepResult with status=complete
  - default_branch returned via get_default_branch()
  - head_message + head_url populated from get_commit()
  - prior_outputs github_repo_full_name is preferred over kpi repo
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from plugins.pwp.capabilities.provision_site.steps.github import (
    _domain_to_slug,
    step_github_checkout,
)
from plugins.pwp.capabilities.provision_site import auth_loader


def _mock_user_response(login: str = "mbgulden"):
    user = MagicMock()
    user.read.return_value = json.dumps({"login": login, "id": 1}).encode()
    user.status = 200
    user.__enter__ = MagicMock(return_value=user)
    user.__exit__ = MagicMock(return_value=False)
    return user


def _mock_repo_response(
    full_name: str = "mbgulden/EZShare",
    default_branch: str = "master",
    private: bool = False,
    push: bool = True,
    admin: bool = True,
):
    repo = MagicMock()
    repo.read.return_value = json.dumps({
        "full_name": full_name,
        "default_branch": default_branch,
        "private": private,
        "description": "test repo",
        "html_url": f"https://github.com/{full_name}",
        "clone_url": f"https://github.com/{full_name}.git",
        "ssh_url": f"git@github.com:{full_name}.git",
        "permissions": {"push": push, "admin": admin, "maintain": push},
        "topics": [],
    }).encode()
    repo.status = 200
    repo.__enter__ = MagicMock(return_value=repo)
    repo.__exit__ = MagicMock(return_value=False)
    return repo


def _mock_branches_response(branches):
    resp = MagicMock()
    resp.read.return_value = json.dumps([
        {"name": b, "commit": {"sha": f"sha_{b}"}, "protected": False}
        for b in branches
    ]).encode()
    resp.status = 200
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _mock_commit_response(sha: str, message: str = "test commit"):
    resp = MagicMock()
    resp.read.return_value = json.dumps({
        "sha": sha,
        "commit": {
            "message": message,
            "author": {
                "name": "Sovereign AI",
                "email": "ai@example.com",
                "date": "2026-04-17T12:01:55Z",
            },
        },
        "parents": [],
    }).encode()
    resp.status = 200
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _stub_run() -> MagicMock:
    return MagicMock()


# -- _domain_to_slug ------------------------------------------------------

def test_domain_to_slug_strips_tld() -> None:
    assert _domain_to_slug("ezshare.systems") == "ezshare"


def test_domain_to_slug_handles_multi_part() -> None:
    assert _domain_to_slug("www.example.com") == "www"


# -- missing credentials --------------------------------------------------

def test_step_soft_fails_without_credentials(monkeypatch, tmp_path: Path) -> None:
    for k in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT"):
        monkeypatch.delenv(k, raising=False)
    with patch(
        "plugins.pwp.capabilities.provision_site.auth_loader.get_secret",
        return_value=auth_loader.AuthResult(
            value=None, source="none", env_var="",
            hint="(test stub)", redaction="<missing>",
        ),
    ):
        result = step_github_checkout(
            domain="ezshare.systems",
            owner="me@example.com",
            run=_stub_run(),
            publish_root=tmp_path,
        )
    assert result.status == "failed"
    assert result.output["_soft_failure"] is True
    assert result.output["reason"] == "missing_credentials"
    assert "GITHUB_TOKEN" in result.output["missing_env"]


# -- happy path with convention rule ------------------------------------

def test_step_happy_path_uses_login_plus_slug(
    monkeypatch, tmp_path: Path
) -> None:
    """When no kpi or prior_outputs repo is set, fall back to login/slug."""
    # No env vars — but the test patches from_env to bypass auth_loader
    from plugins.pwp.capabilities.provision_site.github_client import (
        GitHubClient, GitHubRepo, GitHubBranch, GitHubCommit,
    )
    fake_repo = GitHubRepo(
        full_name="mbgulden/ezshare",
        default_branch="main",
        private=True,
        description="EZShare",
        permissions_push=True,
        permissions_admin=True,
        permissions_maintain=True,
        html_url="https://github.com/mbgulden/ezshare",
        clone_url="https://github.com/mbgulden/ezshare.git",
        ssh_url="git@github.com:mbgulden/ezshare.git",
    )
    fake_branch = GitHubBranch(name="main", sha="abc123", protected=False)
    fake_commit = GitHubCommit(
        sha="abc123",
        message="feat: add EZShare",
        author_name="Sovereign AI",
        author_email="ai@example.com",
        date="2026-04-17T12:01:55Z",
        parents=[],
    )

    with patch.object(GitHubClient, "from_env", return_value=MagicMock()):
        client = GitHubClient.from_env()
        client.validate.return_value = {"login": "mbgulden"}
        client.repo_exists.return_value = True  # convention candidate exists
        client.get_repo.return_value = fake_repo
        client.get_default_branch.return_value = ("main", "abc123")
        client.get_commit.return_value = fake_commit

        result = step_github_checkout(
            domain="ezshare.systems",
            owner="me@example.com",
            run=_stub_run(),
            publish_root=tmp_path,
        )

    assert result.status == "complete"
    assert result.output["repo"] == "mbgulden/ezshare"
    assert result.output["branch"] == "main"
    assert result.output["head_sha"] == "abc123"
    assert result.output["head_message"] == "feat: add EZShare"
    assert result.output["head_url"] == "https://github.com/mbgulden/ezshare/commit/abc123"
    # Permissions copied through
    assert result.output["permissions_push"] is True


def test_step_persists_to_kpi_collections(monkeypatch, tmp_path: Path) -> None:
    """After a successful step, kpi-collections.json must have the github block."""
    from plugins.pwp.capabilities.provision_site.github_client import (
        GitHubClient, GitHubRepo, GitHubBranch, GitHubCommit,
    )
    fake_repo = GitHubRepo(
        full_name="mbgulden/EZShare",
        default_branch="master",
        private=False,
        description="EZShare systems",
        permissions_push=True,
        permissions_admin=True,
        permissions_maintain=True,
        html_url="https://github.com/mbgulden/EZShare",
        clone_url="https://github.com/mbgulden/EZShare.git",
        ssh_url="git@github.com:mbgulden/EZShare.git",
    )
    fake_branch = GitHubBranch(name="master", sha="fd8efd8d547e11c21b615cef2d33cd537630e8e0", protected=False)
    fake_commit = GitHubCommit(
        sha="fd8efd8d547e11c21b615cef2d33cd537630e8e0",
        message="feat: ezshare.systems deployment configuration",
        author_name="Sovereign AI",
        author_email="ai@example.com",
        date="2026-04-17T12:01:55Z",
        parents=[],
    )
    sites_root = tmp_path / "sites"
    sites_root.mkdir()

    with patch.object(GitHubClient, "from_env", return_value=MagicMock()):
        client = GitHubClient.from_env()
        client.validate.return_value = {"login": "mbgulden"}
        client.repo_exists.return_value = False  # convention candidate doesn't exist
        client.search_user_repos.return_value = [fake_repo]  # case-insensitive fallback finds it
        client.get_repo.return_value = fake_repo
        client.get_default_branch.return_value = ("master", "fd8efd8d547e11c21b615cef2d33cd537630e8e0")
        client.get_commit.return_value = fake_commit

        result = step_github_checkout(
            domain="ezshare.systems",
            owner="michael@growthwebdev.com",
            run=_stub_run(),
            publish_root=tmp_path,
            sites_root=sites_root,
        )
    assert result.status == "complete"

    # Verify the kpi-collections.json was written
    kpi_path = sites_root / "ezshare.kpi.json"
    assert kpi_path.exists()
    kpi = json.loads(kpi_path.read_text())
    gh = kpi["external_sources"]["github"]
    assert gh["repo"] == "mbgulden/EZShare"
    assert gh["branch"] == "master"
    assert gh["head_sha"] == "fd8efd8d547e11c21b615cef2d33cd537630e8e0"
    assert gh["head_message"] == "feat: ezshare.systems deployment configuration"
    assert gh["head_author"] == "Sovereign AI"
    assert gh["permissions"]["admin"] is True
    assert "fetched_at" in gh


# -- resolution rules -----------------------------------------------------

def test_resolution_rule1_explicit_prior_outputs(monkeypatch, tmp_path: Path) -> None:
    """prior_outputs['github_repo_full_name'] overrides everything."""
    from plugins.pwp.capabilities.provision_site.github_client import (
        GitHubClient, GitHubRepo, GitHubBranch,
    )
    fake_repo = GitHubRepo(
        full_name="acme-corp/their-site",
        default_branch="main", private=True, description="",
        permissions_push=False, permissions_admin=False, permissions_maintain=False,
        html_url="", clone_url="", ssh_url="",
    )
    with patch.object(GitHubClient, "from_env", return_value=MagicMock()):
        client = GitHubClient.from_env()
        client.validate.return_value = {"login": "mbgulden"}
        client.get_repo.return_value = fake_repo
        client.get_default_branch.return_value = ("main", "sha1")
        client.get_commit.return_value = MagicMock(
            sha="sha1", message="m", author_name="", author_email="", date="", parents=[]
        )

        result = step_github_checkout(
            domain="ezshare.systems",
            owner="me@example.com",
            run=_stub_run(),
            publish_root=tmp_path,
            prior_outputs={"github_repo_full_name": "acme-corp/their-site"},
        )

    assert result.output["repo"] == "acme-corp/their-site"
    # And the rule3 convention "mbgulden/ezshare" was NOT used


def test_resolution_rule2_kpi_collections_repo(monkeypatch, tmp_path: Path) -> None:
    """Rule 2: kpi-collections.json external_sources.github.repo wins over convention."""
    from plugins.pwp.capabilities.provision_site.github_client import (
        GitHubClient, GitHubRepo, GitHubBranch,
    )
    sites_root = tmp_path / "sites"
    sites_root.mkdir()
    # Pre-existing kpi-collections.json with the explicit repo
    (sites_root / "ezshare.kpi.json").write_text(json.dumps({
        "schema_version": "1.0",
        "domain": "ezshare.systems",
        "name": "ezshare",
        "owner": "me@example.com",
        "metrics": {},
        "external_sources": {
            "github": {"repo": "my-org/custom-name"}
        },
    }))
    fake_repo = GitHubRepo(
        full_name="my-org/custom-name",
        default_branch="main", private=False, description="",
        permissions_push=True, permissions_admin=False, permissions_maintain=False,
        html_url="", clone_url="", ssh_url="",
    )
    with patch.object(GitHubClient, "from_env", return_value=MagicMock()):
        client = GitHubClient.from_env()
        client.validate.return_value = {"login": "mbgulden"}
        client.repo_exists.return_value = True
        client.get_repo.return_value = fake_repo
        client.get_default_branch.return_value = ("main", "sha2")
        client.get_commit.return_value = MagicMock(
            sha="sha2", message="", author_name="", author_email="", date="", parents=[]
        )

        result = step_github_checkout(
            domain="ezshare.systems",
            owner="me@example.com",
            run=_stub_run(),
            publish_root=tmp_path,
            sites_root=sites_root,
        )
    assert result.output["repo"] == "my-org/custom-name"


# -- error paths ----------------------------------------------------------

def test_step_handles_repo_404(monkeypatch, tmp_path: Path) -> None:
    """When GitHubClient.get_repo() raises 404, soft-fail with reason=repo_not_found."""
    from plugins.pwp.capabilities.provision_site.github_client import (
        GitHubClient, GitHubError,
    )
    with patch.object(GitHubClient, "from_env", return_value=MagicMock()):
        client = GitHubClient.from_env()
        client.validate.return_value = {"login": "mbgulden"}
        client.repo_exists.return_value = True
        client.get_repo.side_effect = GitHubError(
            "not found", status_code=404, path="/repos/x/y"
        )

        result = step_github_checkout(
            domain="ezshare.systems",
            owner="me@example.com",
            run=_stub_run(),
            publish_root=tmp_path,
        )

    assert result.status == "failed"
    assert result.output["_soft_failure"] is True
    assert result.output["reason"] == "repo_not_found"


def test_step_handles_auth_failure(monkeypatch, tmp_path: Path) -> None:
    """When GitHubClient.validate() raises 401, soft-fail with reason=auth_failed."""
    from plugins.pwp.capabilities.provision_site.github_client import (
        GitHubClient, GitHubError,
    )
    with patch.object(GitHubClient, "from_env", return_value=MagicMock()):
        client = GitHubClient.from_env()
        client.validate.side_effect = GitHubError(
            "bad token", status_code=401, path="/user"
        )

        result = step_github_checkout(
            domain="ezshare.systems",
            owner="me@example.com",
            run=_stub_run(),
            publish_root=tmp_path,
        )

    assert result.status == "failed"
    assert result.output["_soft_failure"] is True
    assert result.output["reason"] == "auth_failed"


def test_step_handles_no_repo_resolution(monkeypatch, tmp_path: Path) -> None:
    """When no rule produces a full_name AND no GitHub login, soft-fail."""
    from plugins.pwp.capabilities.provision_site.github_client import (
        GitHubClient, GitHubError,
    )
    with patch.object(GitHubClient, "from_env", return_value=MagicMock()):
        client = GitHubClient.from_env()
        # Empty login → rule 3 cannot fire
        client.validate.return_value = {"login": ""}

        result = step_github_checkout(
            domain="ezshare.systems",
            owner="me@example.com",
            run=_stub_run(),
            publish_root=tmp_path,
        )

    assert result.status == "failed"
    assert result.output["_soft_failure"] is True
    assert result.output["reason"] == "no_repo_resolution"
