from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from prismatic.api.server import app


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "PE Test")
    (repo / "README.md").write_text("root\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    return repo


def test_worktrees_api_lists_worktrees(monkeypatch, tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    monkeypatch.setenv("PRISMATIC_API_KEY", "test-token")
    client = TestClient(app)

    response = client.get(
        "/api/v1/worktrees",
        params={"repo": str(repo), "base_ref": "main"},
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["worktrees"]) == 1
    assert body["worktrees"][0]["path"] == str(repo)


def test_worktree_janitor_api_defaults_to_dry_run(monkeypatch, tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    wt = tmp_path / "wt-clean"
    _git(repo, "worktree", "add", str(wt), "HEAD")
    monkeypatch.setenv("PRISMATIC_API_KEY", "test-token")
    client = TestClient(app)

    response = client.post(
        "/api/v1/worktrees/janitor",
        json={"repo": str(repo), "base_ref": "main", "stale_hours": 0},
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True
    assert any(Path(item["path"]) == wt for item in body["removable"])
    assert wt.exists()
