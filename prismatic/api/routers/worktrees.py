"""Worktree hygiene endpoints for the Prismatic Engine Core API."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from prismatic.api.auth import verify_api_key
from prismatic.worktree_janitor import list_worktrees, run_janitor, worktree_proof_template

router = APIRouter()


class JanitorRequest(BaseModel):
    repo: str | None = Field(default=None, description="Git repo path; defaults to PRISMATIC_REPO_DIR or cwd")
    base_ref: str = Field(default="origin/main", description="Reference used to determine merged worktrees")
    archive_dir: str | None = Field(default=None, description="Optional archive destination")
    apply: bool = Field(default=False, description="Actually remove planned worktrees; false is dry-run")
    include_dirty: bool = Field(default=False, description="Include dirty worktrees in the manifest; deletion still requires confirm_dirty_token")
    confirm_dirty_token: str | None = Field(default=None, description="Exact token from the manifest; required to delete dirty worktrees")
    stale_hours: float = Field(default=24.0, ge=0.0, description="Age threshold for stale clean worktrees")


@router.get("/worktrees")
async def get_worktrees(
    repo: str | None = None,
    base_ref: str = "origin/main",
    current_user: dict = Depends(verify_api_key),  # noqa: B008
):
    """List registered Git worktrees for this PE installation."""
    return {
        "worktrees": [record.to_dict() for record in list_worktrees(repo, base_ref=base_ref)]
    }


@router.get("/worktrees/proof-template")
async def get_worktree_proof_template(
    issue: str | None = None,
    summary: str = "",
    current_user: dict = Depends(verify_api_key),  # noqa: B008
):
    """Return the portable proof bundle agents should leave in worktrees."""
    return worktree_proof_template(issue=issue, summary=summary)


@router.post("/worktrees/janitor")
async def post_worktree_janitor(
    request: JanitorRequest,
    current_user: dict = Depends(verify_api_key),  # noqa: B008
):
    """Plan or run the core worktree janitor.

    The endpoint is dry-run by default. Set ``apply=true`` to mutate. Dirty
    worktrees are archived but not deleted unless the caller also supplies the
    exact ``confirm_dirty_token`` emitted by the manifest.
    """
    result = run_janitor(
        request.repo,
        base_ref=request.base_ref,
        archive_dir=request.archive_dir,
        dry_run=not request.apply,
        include_dirty=request.include_dirty,
        confirm_dirty_token=request.confirm_dirty_token,
        stale_seconds=int(request.stale_hours * 3600),
    )
    return result.to_dict()
