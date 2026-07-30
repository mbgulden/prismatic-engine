"""step_github_checkout — provision_site Phase 4 GitHub access step.

Resolves a site's GitHub repository from a few signals, then fetches
metadata (default branch, HEAD SHA, recent commit, file lookup) and
persists everything to the kpi-collections.json external_sources.github
block.

This is the "GitHub access for most sites" primitive the dashboard
relies on for:
  - showing recent commit info on each site card
  - embedding GitHub commit URL + branch in Linear task bodies
  - allowing the agent skill to read package.json / vercel.json /
    wrangler.toml via get_file()

Resolution rules (first match wins):

  1. Explicit `github_repo` from prior_outputs (set by user via
     funnel_config form, or hardcoded in kpi-collections.json)
  2. kpi-collections.json external_sources.github.repo
  3. Convention: slug → "<owner>/<slug>" using the GitHub login of
     the authenticated user. (e.g. "ezshare" → "mbgulden/ezshare" if
     mbgulden is logged in.)
  4. Search user's repos for one whose name matches the slug exactly
     (case-insensitive).

The step is `soft` — when no GitHub token is available, or no repo
matches, it returns _soft_failure=True but does NOT block provisioning.
The dashboard's "Pending Changes" panel surfaces it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..types import StepResult

if TYPE_CHECKING:
    from ..github_client import GitHubClient


# Conventions used for repo slug → full_name resolution.
SLUG_FROM_DOMAIN_RE = re.compile(r"[^a-z0-9]+")


def _domain_to_slug(domain: str) -> str:
    """Convert 'ezshare.systems' → 'ezshare' (strip TLD)."""
    parts = domain.lower().split(".")
    return parts[0] if parts else domain


def _load_kpi_collections(
    sites_root: Path, slug: str
) -> Optional[Dict[str, Any]]:
    path = sites_root / f"{slug}.kpi.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_kpi_collections(
    sites_root: Path, slug: str, data: Dict[str, Any]
) -> None:
    path = sites_root / f"{slug}.kpi.json"
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _resolve_repo_full_name(
    *,
    kpi: Optional[Dict[str, Any]],
    prior_outputs: Dict[str, Any],
    github_login: str,
    domain: str,
    github_client: Optional["GitHubClient"] = None,
) -> Optional[str]:
    """Apply resolution rules in priority order.

    Rules:
      1. Explicit `github_repo_full_name` from prior_outputs (e.g. user
         filled in the "Configure website KPIs" form).
      2. kpi-collections.json `external_sources.github.repo`.
      3. Convention: `<github_login>/<slug>` (slug is the first dot-
         separated token of the domain, lowercased). If the repo
         doesn't exist, search the user's repos for a case-insensitive
         match on the slug.

    `github_client` is required for rule 3 fallback search; if not
    provided, only the direct convention string is returned.
    """
    # Rule 1: explicit from prior_outputs
    explicit = prior_outputs.get("github_repo_full_name")
    if explicit and "/" in explicit:
        return explicit
    # Rule 2: kpi-collections.json
    if kpi:
        gh = kpi.get("external_sources", {}).get("github", {})
        if gh.get("repo") and "/" in gh["repo"]:
            return gh["repo"]
    # Rule 3: convention — GitHub login + slug
    slug = _domain_to_slug(domain)
    if not (github_login and slug):
        return None
    candidate = f"{github_login}/{slug}"
    if github_client is None:
        return candidate
    # Verify the convention candidate exists; if not, search user's repos
    # for a case-insensitive slug match.
    if github_client.repo_exists(candidate):
        return candidate
    # Fallback: case-insensitive search through the user's repos
    try:
        repos = github_client.search_user_repos(github_login, limit=100)
    except Exception:
        return candidate  # give up; let downstream 404 handle it
    slug_lower = slug.lower()
    for r in repos:
        # The "name" field is the part after the slash
        repo_name = r.full_name.split("/", 1)[-1] if "/" in r.full_name else ""
        if repo_name.lower() == slug_lower:
            return r.full_name
    # No match — return the original convention so the caller can 404
    return candidate


def step_github_checkout(
    *,
    domain: str,
    owner: str,
    run: Any,
    publish_root: Path,
    prior_outputs: Optional[Dict[str, Any]] = None,
    sites_root: Optional[Path] = None,
) -> StepResult:
    """Resolve a site's GitHub repo and persist metadata to kpi-collections.

    Returns a StepResult with `output["repo"]`, `output["branch"]`,
    `output["head_sha"]`, `output["head_message"]`, and
    `output["head_url"]`. On missing creds / missing repo, returns
    a `soft_failure=True` result.
    """
    prior_outputs = prior_outputs or {}
    sites_root = sites_root or publish_root / "sites"
    # Ensure sites_root exists so we can write the kpi-collections.json
    sites_root.mkdir(parents=True, exist_ok=True)
    slug = _domain_to_slug(domain)
    kpi = _load_kpi_collections(sites_root, slug)

    # 1. Construct the GitHubClient (auth_loader falls back)
    try:
        from ..github_client import GitHubClient, GitHubError
        client = GitHubClient.from_env()
    except ValueError as exc:
        return StepResult(
            name="github_checkout",
            status="failed",
            output={
                "_soft_failure": True,
                "reason": "missing_credentials",
                "missing_env": ["GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT"],
                "hint": (
                    "GitHub access not configured. To enable this step:\n"
                    "  1. Run `gh auth login` (recommended), OR\n"
                    "  2. Set GITHUB_TOKEN env var to a PAT with `repo` scope, OR\n"
                    "  3. Run `pwp-kpi-tracker auth register "
                    "--type github_token --value <ghp_...>`"
                ),
            },
            error=str(exc),
        )

    # 2. Get the authenticated user (so we know the GitHub login)
    try:
        user = client.validate()
    except GitHubError as exc:
        return StepResult(
            name="github_checkout",
            status="failed",
            output={
                "_soft_failure": True,
                "reason": "auth_failed",
                "status_code": exc.status_code,
                "hint": "GitHub token rejected. Re-run `gh auth login`.",
            },
            error=str(exc),
        )
    github_login = user.get("login", "")

    # 3. Resolve repo full_name
    repo_full_name = _resolve_repo_full_name(
        kpi=kpi,
        prior_outputs=prior_outputs,
        github_login=github_login,
        domain=domain,
        github_client=client,
    )
    if not repo_full_name:
        return StepResult(
            name="github_checkout",
            status="failed",
            output={
                "_soft_failure": True,
                "reason": "no_repo_resolution",
                "domain": domain,
                "github_login": github_login,
                "hint": (
                    f"Could not resolve a GitHub repo for {domain}. "
                    f"Provide one via `github_repo_full_name` in the "
                    f"`Configure website KPIs` form, or set "
                    f"kpi-collections.json `external_sources.github.repo`."
                ),
            },
        )

    # 4. Check repo exists + permissions
    try:
        repo = client.get_repo(repo_full_name)
    except GitHubError as exc:
        if exc.status_code == 404:
            return StepResult(
                name="github_checkout",
                status="failed",
                output={
                    "_soft_failure": True,
                    "reason": "repo_not_found",
                    "repo": repo_full_name,
                    "hint": (
                        f"Repo {repo_full_name} does not exist or is "
                        f"not accessible with the current token. Either "
                        f"create it, grant access, or pick a different "
                        f"repo via the form."
                    ),
                },
                error=str(exc),
            )
        raise  # other errors propagate

    # 5. Fetch the default branch + HEAD commit metadata
    try:
        branch_name, head_sha = client.get_default_branch(repo_full_name)
    except GitHubError as exc:
        return StepResult(
            name="github_checkout",
            status="failed",
            output={
                "_soft_failure": True,
                "reason": "branch_lookup_failed",
                "repo": repo_full_name,
            },
            error=str(exc),
        )
    head_message = ""
    head_url = ""
    head_author = ""
    head_date = ""
    if head_sha:
        try:
            commit = client.get_commit(repo_full_name, head_sha)
            head_message = commit.message
            head_author = commit.author_name
            head_date = commit.date
            head_url = f"{repo.html_url}/commit/{head_sha}"
        except GitHubError:
            # Non-fatal — leave URL empty
            head_url = f"{repo.html_url}/commits/{branch_name}"

    # 6. Persist to kpi-collections.json
    if kpi is None:
        kpi = {
            "schema_version": "1.0",
            "domain": domain,
            "name": slug,
            "owner": owner,
            "metrics": {},
            "external_sources": {},
        }
    kpi.setdefault("external_sources", {})
    kpi["external_sources"]["github"] = {
        "repo": repo_full_name,
        "branch": branch_name,
        "head_sha": head_sha,
        "head_message": head_message,
        "head_author": head_author,
        "head_date": head_date,
        "head_url": head_url,
        "html_url": repo.html_url,
        "clone_url": repo.clone_url,
        "ssh_url": repo.ssh_url,
        "default_branch": repo.default_branch,
        "private": repo.private,
        "permissions": {
            "push": repo.permissions_push,
            "admin": repo.permissions_admin,
            "maintain": repo.permissions_maintain,
        },
        "fetched_at": _now_iso(),
    }
    _save_kpi_collections(sites_root, slug, kpi)

    return StepResult(
        name="github_checkout",
        status="complete",
        output={
            "repo": repo_full_name,
            "branch": branch_name,
            "head_sha": head_sha,
            "head_message": head_message,
            "head_author": head_author,
            "head_url": head_url,
            "default_branch": repo.default_branch,
            "private": repo.private,
            "permissions_push": repo.permissions_push,
            "html_url": repo.html_url,
        },
    )


def _now_iso() -> str:
    """ISO-8601 timestamp in UTC."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
