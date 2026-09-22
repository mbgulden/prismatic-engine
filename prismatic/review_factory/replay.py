"""Historical PR replay — shadow evidence backfill.

Replays closed PRs through the *exact* live shadow path::

    shadow_poller.build_input_dict  ->  shadow_observer.evaluate

and joins each decision against the actual outcome with
``shadow_agreement.calls_agree``.

Every live-path producer is **reused verbatim** (imported, never copied):

- ``review_verdict`` : ``prismatic.review_factory.shadow_poller.review_verdict``
  (the ``review factory gate (tier A)`` check-run conclusion -> CLEAN,
  anything else -> REJECT, fail-safe)
- ``ruff_clean``     : ``prismatic.review_factory.shadow_poller.ruff_clean``
  (the ``smoke (ruff lint)`` check-run concluded success; missing -> False)
- ``ci_green_self_hosted`` : ``shadow_poller.ci_green_self_hosted``
- ``branch_protection_satisfied`` : ``shadow_poller.branch_protection_satisfied``
- ``merge_conflicts`` : ``shadow_poller.merge_conflicts``

The only replay-specific code is the *fetch layer*
(:class:`GitHubApiPRSource`), which reads historical PR data over the
GitHub API (GET-only). The pure core (:func:`replay_pr`,
:func:`replay_batch`) takes already-fetched data, so unit tests never
touch the network.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from prismatic.review_factory.shadow_agreement import calls_agree
from prismatic.review_factory.shadow_observer import (
    ShadowDecision,
    evaluate,
    load_input_from_dict,
)
from prismatic.review_factory.shadow_poller import (
    SELF_HOSTED_CHECKS,
    build_input_dict,
    default_components,
)

logger = logging.getLogger(__name__)

REPO = "mbgulden/prismatic-engine"
USER_AGENT = "prismatic-replay/1.0"

# review_verdict producer, cited for the audit trail (see module docstring).
REVIEW_VERDICT_PRODUCER = "prismatic.review_factory.shadow_poller.review_verdict"

ACTUAL_MERGED = "merged"
ACTUAL_CLOSED_UNMERGED = "closed_unmerged"

# Fields of the PR dict that build_input_dict consumes.
_PR_KEYS = ("number", "title", "headRefOid", "baseRefOid", "mergeable")


# ─────────────────────────────────────────────────────────────────────
# Fetch layer (thin; the only network code in this module)
# ─────────────────────────────────────────────────────────────────────


class GitHubApiError(RuntimeError):
    """A GitHub API call failed after retries."""


def _api_get(url: str, accept: str = "application/vnd.github+json") -> Any:
    """GET a GitHub API URL with the ambient credential.

    Auth follows the same surrogate pattern as the github skill
    (~/workspace/skills/github/bin/ghread): the request carries the
    credential and the egress layer substitutes it. Imported lazily so
    the pure core stays importable without the skill present.
    """
    sys_path_added = False
    try:
        import sys

        if "/opt/hatch/skills/skill-creator/bin" not in sys.path:
            sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
            sys_path_added = True
        from dynamic_credentials import add_surrogate_to_request

        req = urllib.request.Request(
            url, headers={"Accept": accept, "User-Agent": USER_AGENT}
        )
        add_surrogate_to_request(
            req, "custom.github", allowed_hosts=("api.github.com",)
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    finally:
        if sys_path_added:
            import sys

            sys.path.remove("/opt/hatch/skills/skill-creator/bin")


def _api_get_retry(url: str, retries: int = 2) -> Any:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return _api_get(url)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise GitHubApiError(f"404 for {url}") from exc
            last = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
        time.sleep(2**attempt)
    raise GitHubApiError(f"GET {url} failed after {retries + 1} attempts: {last}")


class GitHubApiPRSource:
    """Read-only GitHub API source for replay. GET-only, no mutations."""

    def __init__(self, repo: str = REPO, pause_s: float = 0.2) -> None:
        self.repo = repo
        self.pause_s = pause_s

    def _get(self, path: str, **params: Any) -> Any:
        url = f"https://api.github.com/repos/{self.repo}/{path.lstrip('/')}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        time.sleep(self.pause_s)
        return _api_get_retry(url)

    @staticmethod
    def _shape_pr(raw: dict[str, Any]) -> dict[str, Any]:
        """Shape a PR API object to what build_input_dict consumes,
        plus replay metadata the record needs."""
        head = raw.get("head") or {}
        base = raw.get("base") or {}
        user = raw.get("user") or {}
        return {
            "number": raw["number"],
            "title": raw.get("title", ""),
            "headRefOid": head.get("sha", ""),
            "baseRefOid": base.get("sha", ""),
            # Closed PRs report mergeable UNKNOWN/None; build_input_dict
            # only treats CONFLICTING as conflicts (same as the live
            # closed-event path, which skips the mergeability gate).
            "mergeable": raw.get("mergeable"),
            # The pulls LIST endpoint omits the `merged` boolean (only the
            # single-PR endpoint has it); derive from merged_at instead.
            "merged": bool(raw.get("merged") or raw.get("merged_at")),
            "merged_at": raw.get("merged_at"),
            "closed_at": raw.get("closed_at"),
            "author_login": user.get("login", ""),
            "author_type": user.get("type", ""),
        }

    def list_closed_prs(self, since_days: int) -> list[dict[str, Any]]:
        """All closed PRs (merged + unmerged), oldest first.

        Client-side date filter: the API has no closed-since filter for
        the pulls endpoint that is reliable, so we page and filter.
        """
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = self._get(
                "pulls",
                state="closed",
                sort="created",
                direction="asc",
                per_page=100,
                page=page,
            )
            if not batch:
                break
            for raw in batch:
                out.append(self._shape_pr(raw))
            page += 1
            if len(batch) < 100:
                break
        cutoff = time.time() - since_days * 86400
        kept = []
        for pr in out:
            stamp = pr["merged_at"] or pr["closed_at"]
            if not stamp:
                continue
            try:
                dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                continue
            if dt.timestamp() >= cutoff:
                kept.append(pr)
        kept.sort(key=lambda p: p["number"])
        return kept

    def get_pr_files(self, pr_number: int) -> list[str]:
        """Changed file paths for one PR (paginated)."""
        paths: list[str] = []
        page = 1
        while True:
            batch = self._get(f"pulls/{pr_number}/files", per_page=100, page=page)
            if not batch:
                break
            paths.extend(f.get("filename", "") for f in batch)
            page += 1
            if len(batch) < 100:
                break
        return [p for p in paths if p]

    def get_check_runs(self, head_sha: str) -> list[dict[str, Any]]:
        """Check runs for a commit SHA. Missing history -> []."""
        if not head_sha:
            return []
        try:
            data = self._get(f"commits/{head_sha}/check-runs", per_page=100)
        except GitHubApiError as exc:
            logger.warning("check-runs for %s: %s", head_sha[:8], exc)
            return []
        runs = (data or {}).get("check_runs", [])
        return [
            {
                "name": r.get("name", ""),
                "status": r.get("status", ""),
                "conclusion": r.get("conclusion"),
            }
            for r in runs
        ]


# ─────────────────────────────────────────────────────────────────────
# Pure replay core (no network)
# ─────────────────────────────────────────────────────────────────────


def _unknown_fields(pr: dict[str, Any], check_runs: list[dict[str, Any]]) -> list[str]:
    unknown: list[str] = []
    if not check_runs:
        # Live functions fail closed on empty check runs (ruff_clean False,
        # review_verdict REJECT); flag it so the report can show the count.
        unknown.append("check_runs")
    if not pr["merged"] and pr.get("mergeable") not in ("MERGEABLE", "CONFLICTING"):
        # Closed-unmerged PRs lose mergeability history; build_input_dict
        # reports False (same substitution the live closed-event path makes).
        unknown.append("merge_conflicts")
    return unknown


def replay_pr(
    pr: dict[str, Any],
    check_runs: list[dict[str, Any]],
    files: list[str],
    components: tuple | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Replay one PR through the live shadow path. Pure (no network).

    Returns a shadow record in the live record format plus backfill
    metadata (see module docstring / plan §7).
    """
    for key in _PR_KEYS:
        if key not in pr:
            raise ValueError(f"PR dict missing required key: {key}")
    policy, bands, tier_engine = components or default_components()

    input_dict = build_input_dict({k: pr[k] for k in _PR_KEYS}, check_runs, files)
    decision: ShadowDecision = evaluate(
        load_input_from_dict(input_dict), policy, bands, tier_engine
    )

    actual_outcome = ACTUAL_MERGED if pr["merged"] else ACTUAL_CLOSED_UNMERGED
    agree = calls_agree(decision.call, actual_outcome)
    decided_at = pr["merged_at"] or pr["closed_at"] or ""
    replayed_at = (now or datetime.now(timezone.utc)).isoformat()

    return {
        "pr_number": pr["number"],
        "pr_title": pr["title"],
        "head_sha": pr["headRefOid"],
        "system_call": decision.call,
        "actual_outcome": actual_outcome,
        "agree": agree,
        "decided_at": decided_at,
        "backfilled": True,
        "tier": decision.tier,
        "tier_name": decision.tier_name,
        "gate_results": [
            {"gate": g.gate, "passed": g.passed, "detail": g.detail}
            for g in decision.gate_results
        ],
        "reasons": list(decision.reasons),
        "replay": {
            "pipeline": "shadow_observer.evaluate",
            "policy_version": decision.policy_version,
            "ruff_version": "check-run (not computed locally)",
            "review_verdict_producer": REVIEW_VERDICT_PRODUCER,
            "unknown_fields": _unknown_fields(pr, check_runs),
            "replayed_at": replayed_at,
        },
    }


def select_prs(
    prs: Iterable[dict[str, Any]],
    limit: int,
    exclude_bots: bool = True,
    order: str = "asc",
) -> list[dict[str, Any]]:
    """Deterministic selection: bots out, capped at ``limit``.

    ``order="asc"`` takes the oldest PR numbers first; ``order="desc"``
    takes the newest first. Either way the selection is a pure function
    of the input, so reruns are comparable.
    """
    if order not in ("asc", "desc"):
        raise ValueError(f"order must be 'asc' or 'desc', got {order!r}")
    selected = []
    for pr in sorted(prs, key=lambda p: p["number"], reverse=(order == "desc")):
        if exclude_bots and (pr.get("author_type") == "Bot"):
            continue
        selected.append(pr)
        if len(selected) >= limit:
            break
    return selected


def filter_prs_with_check_runs(
    prs: Iterable[dict[str, Any]],
    fetch_check_runs: Callable[[str], list[dict[str, Any]]],
    max_keep: int | None = None,
) -> list[dict[str, Any]]:
    """Keep only PRs with real CI history on the head SHA.

    A PR counts only if at least one check run is one of the
    SELF_HOSTED_CHECKS the shadow gates actually read (ruff smoke,
    tier-A gate, plugin load). Other checks (e.g. the shadow-call
    observer itself) do not satisfy the gates, so PRs having only
    those replay trivially: every gate fails closed -> skip, which
    inflates agreement without testing the pipeline's judgment.
    One PR's fetch failure never aborts the filter. When ``max_keep``
    is set, probing stops after that many PRs qualify (callers should
    pass PRs newest-first so the freshest evidence is preferred).
    """
    kept: list[dict[str, Any]] = []
    for pr in prs:
        try:
            runs = fetch_check_runs(pr["headRefOid"])
            if any(r.get("name") in SELF_HOSTED_CHECKS for r in runs):
                kept.append(pr)
                if max_keep is not None and len(kept) >= max_keep:
                    break
        except Exception as exc:  # per-PR isolation
            logger.warning(
                "check-run probe for PR #%s failed: %s", pr.get("number"), exc
            )
    return kept


def replay_batch(
    prs: Iterable[dict[str, Any]],
    fetch_files: Callable[[int], list[str]],
    fetch_check_runs: Callable[[str], list[dict[str, Any]]],
    components: tuple | None = None,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Replay many PRs. One PR's failure never aborts the batch.

    Returns (records, errors); each error is
    {"pr_number": n, "error": "..."}.
    """
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for pr in prs:
        try:
            files = fetch_files(pr["number"])
            if not files:
                # Plan §5: PRs with no file changes are excluded.
                continue
            check_runs = fetch_check_runs(pr["headRefOid"])
            records.append(replay_pr(pr, check_runs, files, components, now=now))
        except Exception as exc:  # per-PR isolation (plan §6 step 8)
            logger.warning("replay of PR #%s failed: %s", pr.get("number"), exc)
            errors.append({"pr_number": pr.get("number"), "error": str(exc)[:300]})
    return records, errors
