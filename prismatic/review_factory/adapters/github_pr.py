"""L0 adapter: GitHub PR (+ the shadow feed's extraction) -> ReviewArtifact.

Pure translator: no network, no side effects, no judgment. Imports only
``prismatic.review_factory.artifact`` (workstream A) plus the adapter-shared
``._artifact`` helpers and stdlib.

This formalizes what the shadow feed's event workflow already extracts
(``shadow_event.py`` / ``shadow_poller.py`` / ``shadow_observer.py``): the PR
object from ``gh pr view --json number,title,headRefOid,mergeable,state``,
the changed-file list, the check-runs from the commits check-runs API, and
the merge state. The adapter consumes those already-fetched structures — it
never calls ``gh`` itself.

Documented input fixture — one PR's fetched structures as JSON::

    {
      "pr_number": 545,                       # required
      "pr_title": "Close the repair loop",    # -> intent.brief (+ pr_body)
      "pr_body": "Routes drainer ...",        # appended to brief when present
      "head_sha": "73692e27...",              # required; -> diff.head_tree
      "base_sha": "daa72c6a...",              # -> diff.base_tree
      "state": "open",                        # harness-only (open/closed/merged)
      "mergeable": "MERGEABLE",               # -> checks[] "merge-state" entry
      "files": ["a.py"],                      # -> novelty_context.first_seen_paths;
                                              #    bare paths carry no change_type
                                              #    or line counts, so they are NOT
                                              #    diff.files entries (never guessed)
      "diff_unified": "diff --git ...",       # -> diff.unified
      "check_runs": [                         # -> checks (completed runs only)
        {"name": "review factory gate (tier A)", "status": "completed",
         "conclusion": "success", "completed_at": "2026-09-23T23:10:00Z"}
      ],
      "plan_ref": "plans/x.md",               # rarely present on PRs
      "goals": ["..."],                       # rarely present on PRs
      "prior_receipts": ["rcpt-..."],
      "submitted_at": "2026-09-23T23:15:00Z"  # else now (UTC)
    }

``files`` also accepts ``{"path", "change_type", "lines_added",
"lines_removed"}`` mappings (and ``additions``/``deletions`` from
``gh pr view --json files``, from which the change kind is derived
deterministically). Entries that still cannot be honestly built are omitted
from ``diff.files``; their paths remain in ``novelty_context``.

Check-run mapping mirrors the shadow poller: conclusion ``success`` becomes
exit_code 0, any other completed conclusion becomes 1. Runs that have not
completed are NOT mapped to checks — an exit code would be fabricated, and
the §2 schema has no field for "unsettled". ``mergeable`` maps to a
deterministic ``merge-state`` check entry (0 when MERGEABLE, 1 when
CONFLICTING); an unknown merge state is omitted — the §2 schema has no
merge-state field, and the adapter invents none.

Missing ``pr_number``/``head_sha`` or a non-mapping ``raw`` fails closed
with ``AdapterError``.
"""

from __future__ import annotations

import json
from typing import Any

from ..artifact import (
    CheckResult,
    Diff,
    DiffFile,
    Intent,
    NoveltyContext,
    ReviewArtifact,
)
from ._artifact import (
    AdapterError,
    build_artifact,
    compute_gaps,
    make_check,
    make_diff_file,
    normalize_change_type,
    now_iso,
    opt_str,
    require_mapping,
    str_list,
    truncate_unified,
)

# gh's mergeable values, per the shadow feed's usage.
_MERGEABLE_CLEAN = "MERGEABLE"
_MERGEABLE_CONFLICT = "CONFLICTING"


class GitHubPRAdapter:
    """Harness adapter for GitHub PR data (shadow-feed extraction shape)."""

    harness_id = "github-pr"

    def to_artifact(self, raw: Any) -> ReviewArtifact:
        return build_artifact(harness_id=self.harness_id, **self._extract(raw))

    def explicit_gaps(self, raw: Any) -> list[str]:
        kw = self._extract(raw)
        return compute_gaps(kw["intent"], kw["diff"], kw["checks"])

    # ── translation ──────────────────────────────────────────────────

    def _extract(self, raw: Any) -> dict[str, Any]:
        data = require_mapping(raw, "github-pr")

        pr_number = data.get("pr_number")
        if pr_number is None or not str(pr_number).strip():
            raise AdapterError("github-pr: pr_number is required")
        head_sha = opt_str(data, "head_sha") or opt_str(data, "headRefOid")
        if head_sha is None:
            raise AdapterError("github-pr: head_sha is required")
        harness_run_id = f"github-pr-{pr_number}-{head_sha[:12]}"

        title = opt_str(data, "pr_title") or opt_str(data, "title")
        body = opt_str(data, "pr_body") or opt_str(data, "body")
        brief = None
        if title is not None:
            brief = f"{title}\n\n{body}" if body else title
        elif body is not None:
            brief = body
        goals = data.get("goals") or []
        if not isinstance(goals, list) or any(not isinstance(g, str) for g in goals):
            raise AdapterError("github-pr: goals must be a list of strings")
        intent = Intent(
            plan_ref=opt_str(data, "plan_ref"), brief=brief, goals=list(goals)
        )

        diff_text = opt_str(data, "diff_unified") or opt_str(data, "diff")
        unified = truncate_unified(diff_text) if diff_text is not None else None
        files: list[DiffFile] = []
        seen_paths: list[str] = []
        if "files" in data:
            raw_files = data["files"]
            if not isinstance(raw_files, list):
                raise AdapterError("github-pr: files must be a list")
            for entry in raw_files:
                if isinstance(entry, str):
                    # Bare path (the shadow feed's shape): no change_type, no
                    # line counts — recorded for novelty, never guessed into
                    # a diff.files entry.
                    seen_paths.append(entry)
                elif isinstance(entry, dict):
                    path = entry.get("path")
                    if not isinstance(path, str) or not path:
                        raise AdapterError("github-pr: file entry missing path")
                    seen_paths.append(path)
                    built = make_diff_file(
                        path,
                        self._change_type(entry),
                        entry.get("lines_added", entry.get("additions")),
                        entry.get("lines_removed", entry.get("deletions")),
                    )
                    if built is not None:
                        files.append(built)
                else:
                    raise AdapterError(
                        "github-pr: files entries must be paths or mappings"
                    )
        diff = Diff(
            # gh 2.45.0 on the shadow box cannot fetch baseRefOid; the poller
            # works without it. Missing here is a null + gap, not a fabrication.
            base_tree=opt_str(data, "base_sha") or opt_str(data, "baseRefOid"),
            head_tree=head_sha,
            unified=unified,
            files=files,
        )

        checks: list[CheckResult] = []
        if "check_runs" in data:
            raw_runs = data["check_runs"]
            if not isinstance(raw_runs, list):
                raise AdapterError("github-pr: check_runs must be a list")
            for run in raw_runs:
                if not isinstance(run, dict):
                    raise AdapterError("github-pr: check_runs entries must be mappings")
                name = run.get("name")
                if not isinstance(name, str) or not name:
                    raise AdapterError("github-pr: check run missing name")
                if run.get("status") != "completed":
                    # No exit code to report; omitted, not fabricated.
                    continue
                conclusion = run.get("conclusion")
                exit_code = 0 if conclusion == "success" else 1
                checks.append(
                    make_check(
                        name,
                        exit_code,
                        # Provenance for the captured record (deterministic);
                        # the hash covers what the harness reported, nothing more.
                        json.dumps(
                            {
                                "name": name,
                                "status": run.get("status"),
                                "conclusion": conclusion,
                            },
                            sort_keys=True,
                        ),
                        opt_str(run, "completed_at"),
                    )
                )

        mergeable = opt_str(data, "mergeable")
        if mergeable in (_MERGEABLE_CLEAN, _MERGEABLE_CONFLICT):
            checks.append(
                make_check(
                    "merge-state",
                    0 if mergeable == _MERGEABLE_CLEAN else 1,
                    mergeable,
                    opt_str(data, "submitted_at"),
                )
            )
        # Any other mergeable value (UNKNOWN, missing): omitted. The §2 schema
        # has no merge-state field, so there is nothing to null out and no gap
        # to list — inventing one would be fabrication.

        prior_receipts = str_list(
            data.get("prior_receipts"), "github-pr: prior_receipts"
        )
        submitted_at = opt_str(data, "submitted_at") or now_iso()

        return {
            "harness_run_id": harness_run_id,
            "submitted_at": submitted_at,
            "intent": intent,
            "diff": diff,
            "checks": checks,
            "prior_receipts": prior_receipts,
            "novelty_context": NoveltyContext(first_seen_paths=seen_paths),
        }

    @staticmethod
    def _change_type(entry: dict[str, Any]) -> str:
        """Determine the closed-vocabulary change kind for a file entry.

        Uses an explicit ``change_type`` when present; otherwise derives it
        deterministically from ``additions``/``deletions`` (the shape
        ``gh pr view --json files`` returns). Returns ``"unknown"`` when
        neither is available — the entry is then omitted from diff.files.
        """
        raw = entry.get("change_type")
        if isinstance(raw, str) and raw.strip():
            return normalize_change_type(raw)
        additions = entry.get("additions")
        deletions = entry.get("deletions")
        if (
            isinstance(additions, int)
            and isinstance(deletions, int)
            and not isinstance(additions, bool)
            and not isinstance(deletions, bool)
        ):
            if additions > 0 and deletions == 0:
                return "added"
            if deletions > 0 and additions == 0:
                return "deleted"
            if additions > 0 or deletions > 0:
                return "modified"
        return "unknown"
