"""step_vercel_project — create or look up the Vercel project for the domain.

Runs after `platform_detect` (and skips itself if platform != 'vercel').
Uses the Vercel REST API. Reads its zone_id from prior_outputs (the
Cloudflare zone lookup is still useful because we need to confirm the
domain actually points to Vercel via DNS — same verification pattern).

Output (on success):
  {
    "project_id": "prj_xxx",
    "project_name": "ezshare",
    "framework": "other",
    "action": "lookup" | "create",
    "vercel_account_id": "<account-id>",
  }
"""

from __future__ import annotations

from typing import Any

from ..types import StepResult
from ..vercel_client import VercelClient, VercelError


def step_vercel_project(
    *,
    domain: str,
    owner: str,
    run,
    publish_root,
    prior_outputs=None,
    **_: Any,
) -> StepResult:
    """Look up or create the Vercel project for `domain`."""
    prior = prior_outputs or {}

    # Skip if platform != vercel (set by platform_detect)
    platform_info = prior.get("platform_detect") or {}
    platform = platform_info.get("platform")
    if platform not in ("vercel", "vercel_pages"):
        return StepResult(
            name="vercel_project",
            status="skipped",
            output={
                "reason": (
                    f"platform_detect found platform={platform!r}; "
                    "vercel_project only runs when platform=vercel"
                ),
                "platform": platform,
            },
        )

    # Determine a project name (use domain's left-most label, e.g.
    # 'ezshare.systems' -> 'ezshare' — same kebab-case convention the
    # provision_site registry uses).
    project_name = platform_info.get("vercel_project_name") or domain.split(".")[0]

    # Try to construct the client. If VERCEL_TOKEN is missing, return a
    # clean error (so the run can be marked soft-failed and the rest
    # of the pipeline proceeds).
    try:
        vc = VercelClient.from_env()
    except ValueError as e:
        return StepResult(
            name="vercel_project",
            status="failed",
            error=(
                f"Vercel token not configured: {e}. Set VERCEL_TOKEN env "
                "var to enable Vercel project provisioning."
            ),
        )

    try:
        existing = vc.project_lookup(project_name)
    except VercelError as e:
        return StepResult(
            name="vercel_project",
            status="failed",
            error=f"Vercel API error during project_lookup: {e}",
        )

    if existing is not None:
        return StepResult(
            name="vercel_project",
            status="complete",
            output={
                "project_id": existing.id,
                "project_name": existing.name,
                "framework": existing.framework,
                "action": "lookup",
                "vercel_account_id": existing.account_id,
            },
        )

    # Project doesn't exist yet — try to create it.
    try:
        project = vc.project_create(project_name, framework="other")
    except VercelError as e:
        return StepResult(
            name="vercel_project",
            status="failed",
            error=f"Vercel API error during project_create: {e}",
        )

    return StepResult(
        name="vercel_project",
        status="complete",
        output={
            "project_id": project.id,
            "project_name": project.name,
            "framework": project.framework,
            "action": "create",
            "vercel_account_id": project.account_id,
        },
    )
