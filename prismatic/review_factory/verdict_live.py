"""Live verdict actions for the stranded-work pipeline.

DISABLED BY DEFAULT. Everything in this module performs GitHub writes
(verdict comments, labels). Nothing here runs unless BOTH conditions hold:

1. ``PRISMATIC_VERDICT_LIVE=1`` is set in the environment (explicit opt-in).
2. A FRESH dry-run log exists (``stranded-verdict-dryrun.jsonl`` written
   within the last 26 hours and non-empty) — the live picture must be current.

The dry-run pipeline in :mod:`prismatic.review_factory.verdict` stays the
default and is stdlib-only. This module shells out to ``gh`` (subprocess) for
the live actions only.

Safety properties:
- Idempotent: a verdict comment carries a machine marker; an existing marker
  comment for the same verdict class suppresses re-posting (no daily spam).
- Labels are never created here. If the configured label does not exist on
  the repo, label application fails closed with a clear error.
- Every attempted action — success, skip, or failure — is appended to the
  live audit log. Live runs are never silent.
- Per-PR failures do not abort the run; they are collected, audited, and
  reported, and the run exits non-zero.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

LIVE_FLAG_ENV = "PRISMATIC_VERDICT_LIVE"

DEFAULT_DRY_RUN_LOG = "~/.prismatic/audit/stranded-verdict-dryrun.jsonl"
DEFAULT_LIVE_LOG = "~/.prismatic/audit/stranded-verdict-live.jsonl"
DEFAULT_DIGEST_FRAGMENT = "~/.prismatic/audit/stranded-verdicts-issued.json"

# Cron ticks daily; 26h of slack keeps one missed tick from blocking live mode
# while still refusing a genuinely stale picture.
MAX_DRY_RUN_AGE = timedelta(hours=26)

# Verdict class -> repo label. Labels must already exist; they are never created.
VERDICT_LABELS = {
    "overdue": "stranded-overdue",
    "approaching": "stranded-approaching",
}

COMMENT_MARKER_PREFIX = "<!-- prismatic-verdict:v1:"


class VerdictLiveError(RuntimeError):
    """A live verdict action failed. Audited, never silent."""


def live_enabled() -> bool:
    """True only when the operator explicitly opted into live verdicts."""
    return os.environ.get(LIVE_FLAG_ENV, "").strip() == "1"


def dry_run_fresh(
    log_path: str | Path = DEFAULT_DRY_RUN_LOG,
    max_age: timedelta = MAX_DRY_RUN_AGE,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Check the dry-run log is present, non-empty, and recent."""
    now = now or datetime.now(timezone.utc)
    path = Path(log_path).expanduser()
    if not path.is_file():
        return False, f"dry-run log missing: {path}"
    try:
        if path.stat().st_size == 0:
            return False, f"dry-run log empty: {path}"
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError as exc:
        return False, f"dry-run log unreadable: {exc}"
    age = now - mtime
    if age > max_age:
        return False, (
            f"dry-run log stale: {age.total_seconds() / 3600:.1f}h old "
            f"(limit {max_age.total_seconds() / 3600:.0f}h)"
        )
    return True, f"dry-run log fresh ({age.total_seconds() / 3600:.1f}h old)"


def guard_allows_live(
    log_path: str | Path = DEFAULT_DRY_RUN_LOG,
) -> tuple[bool, str]:
    """Combined live-mode guard. Returns (allowed, reason)."""
    if not live_enabled():
        return False, (
            f"live verdicts disabled (set {LIVE_FLAG_ENV}=1 to enable); "
            "dry-run remains the default"
        )
    fresh, reason = dry_run_fresh(log_path)
    if not fresh:
        return False, f"live verdicts refused: {reason}"
    return True, reason


def render_verdict_comment(record: dict[str, Any]) -> str:
    """Render the concise verdict comment for one decision record."""
    verdict = record.get("verdict", "unknown")
    lines = [
        f"## Prismatic stranded-work verdict: {str(verdict).upper()}",
        "",
        f"- **PR age:** {record.get('age_days', '?')} days (7-day verdict line)",
        f"- **Recommendation:** {record.get('recommended', 'none')} — "
        f"{record.get('reason', '')}",
        "- **Mode:** live verdict (automated notice)",
        f"- **Evaluated:** {record.get('ts', '')}",
        "",
        "The verdict pipeline recommends "
        f"`{record.get('recommended', 'none')}`. A maintainer reviews all "
        "overdue verdicts; this automation posted this notice and applied a "
        "label only — nothing was closed or merged.",
        "",
        f"{COMMENT_MARKER_PREFIX}{verdict} -->",
    ]
    return "\n".join(lines)


def _default_runner(
    args: list[str], input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, input=input_text, capture_output=True, text=True, timeout=120
    )


Runner = Callable[[list[str], str | None], subprocess.CompletedProcess[str]]


def _gh_json(args: list[str], runner: Runner = _default_runner) -> Any:
    proc = runner(args, None)
    if proc.returncode != 0:
        raise VerdictLiveError(
            f"gh failed ({' '.join(args[:4])}…): {(proc.stderr or '').strip()[:300]}"
        )
    try:
        return json.loads(proc.stdout or "null")
    except json.JSONDecodeError as exc:
        raise VerdictLiveError(f"gh returned non-JSON output: {exc}") from exc


def existing_verdict_marker(
    repo: str, pr_number: int, runner: Runner = _default_runner
) -> str | None:
    """Return the verdict class already announced on the PR, if any."""
    comments = _gh_json(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--repo",
            repo,
            "--json",
            "comments",
            "--jq",
            ".comments[].body",
        ],
        runner,
    )
    if not isinstance(comments, list):
        return None
    for body in comments:
        if isinstance(body, str) and COMMENT_MARKER_PREFIX in body:
            start = body.index(COMMENT_MARKER_PREFIX) + len(COMMENT_MARKER_PREFIX)
            end = body.find(" -->", start)
            if end != -1:
                return body[start:end].strip()
    return None


def label_exists(repo: str, label: str, runner: Runner = _default_runner) -> bool:
    """Check the label exists on the repo. Never creates labels."""
    names = _gh_json(
        ["gh", "label", "list", "--repo", repo, "--json", "name", "--jq", ".[].name"],
        runner,
    )
    return isinstance(names, list) and label in names


def post_verdict_comment(
    repo: str,
    pr_number: int,
    body: str,
    runner: Runner = _default_runner,
) -> None:
    """Post the verdict comment. Raises VerdictLiveError on failure."""
    proc = runner(
        ["gh", "pr", "comment", str(pr_number), "--repo", repo, "--body", body],
        None,
    )
    if proc.returncode != 0:
        raise VerdictLiveError(
            f"gh pr comment failed for PR #{pr_number}: "
            f"{(proc.stderr or '').strip()[:300]}"
        )


def apply_verdict_label(
    repo: str,
    pr_number: int,
    label: str,
    runner: Runner = _default_runner,
) -> None:
    """Apply a label. Fails closed when the label does not exist.

    Labels are configuration, not code: creating them here would let a typo
    silently mint a new label instead of failing loudly.
    """
    if not label_exists(repo, label, runner):
        raise VerdictLiveError(
            f"label '{label}' does not exist on {repo}; refusing to create "
            "labels automatically (create it manually, then re-run)"
        )
    proc = runner(
        ["gh", "pr", "edit", str(pr_number), "--repo", repo, "--add-label", label],
        None,
    )
    if proc.returncode != 0:
        raise VerdictLiveError(
            f"gh pr edit --add-label failed for PR #{pr_number}: "
            f"{(proc.stderr or '').strip()[:300]}"
        )


def _audit(action: dict[str, Any], log_path: str | Path) -> None:
    path = Path(log_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = dict(action)
    entry.setdefault("ts", datetime.now(timezone.utc).isoformat())
    entry.setdefault("mode", "live")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def issue_live_verdict(
    record: dict[str, Any],
    repo: str,
    *,
    runner: Runner = _default_runner,
    live_log: str | Path = DEFAULT_LIVE_LOG,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Issue the live actions for one verdict record.

    Only ``overdue`` and ``approaching`` records get actions. Returns an
    action record (always audited); raises VerdictLiveError on action failure
    after auditing the failure.
    """
    now = now or datetime.now(timezone.utc)
    pr_number = record.get("pr")
    verdict = record.get("verdict")

    def audited(action: dict[str, Any]) -> dict[str, Any]:
        _audit(action, live_log)
        return action

    if not isinstance(pr_number, int):
        return audited(
            {
                "pr": pr_number,
                "action": "skipped",
                "reason": "no integer PR number in record",
            }
        )
    label = VERDICT_LABELS.get(verdict)
    if label is None:
        return audited(
            {
                "pr": pr_number,
                "action": "skipped",
                "reason": f"verdict '{verdict}' takes no live action",
            }
        )

    try:
        announced = existing_verdict_marker(repo, pr_number, runner=runner)
        if announced == verdict:
            return audited(
                {
                    "pr": pr_number,
                    "action": "skipped",
                    "reason": f"verdict '{verdict}' already announced; not re-posting",
                }
            )
        body = render_verdict_comment(record)
        post_verdict_comment(repo, pr_number, body, runner=runner)
        apply_verdict_label(repo, pr_number, label, runner=runner)
    except VerdictLiveError as exc:
        return audited(
            {
                "pr": pr_number,
                "action": "failed",
                "reason": str(exc)[:500],
            }
        )
    return audited(
        {
            "pr": pr_number,
            "action": "verdict_issued",
            "verdict": verdict,
            "label": label,
            "recommendation": record.get("recommended"),
        }
    )


def run_live_verdicts(
    records: list[dict[str, Any]],
    repo: str,
    *,
    runner: Runner = _default_runner,
    live_log: str | Path = DEFAULT_LIVE_LOG,
    dry_run_log: str | Path = DEFAULT_DRY_RUN_LOG,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Issue live verdicts for evaluated records.

    Refuses unless the live guard passes. Returns (actions, failures);
    every record produces exactly one audited action entry.
    """
    allowed, reason = guard_allows_live(dry_run_log)
    if not allowed:
        raise VerdictLiveError(f"live verdicts refused: {reason}")
    actions = [
        issue_live_verdict(r, repo, runner=runner, live_log=live_log)
        for r in (records or [])
    ]
    failures = [a for a in actions if a.get("action") == "failed"]
    return actions, failures


def verdicts_for_digest(
    actions: list[dict[str, Any]],
    records: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Build the digest-ready stranded payload from a tick's records+actions.

    Shape matches what ``digest._normalize_stranded`` expects:
      {"approaching": [{pr, title, age_days, author}],
       "verdicts_issued": [{pr, verdict, reason, ts}]}
    """
    by_pr = {r.get("pr"): r for r in (records or []) if isinstance(r, dict)}
    approaching = [
        {
            "pr": r.get("pr"),
            "title": r.get("title"),
            "age_days": r.get("age_days"),
            "author": r.get("author"),
        }
        for r in (records or [])
        if isinstance(r, dict) and r.get("verdict") == "approaching"
    ]
    issued = []
    for action in actions or []:
        if not isinstance(action, dict) or action.get("action") != "verdict_issued":
            continue
        record = by_pr.get(action.get("pr"), {})
        issued.append(
            {
                "pr": action.get("pr"),
                "verdict": action.get("verdict"),
                "reason": record.get("reason", ""),
                "ts": action.get("ts"),
            }
        )
    return {"approaching": approaching, "verdicts_issued": issued}


def write_digest_fragment(
    payload: dict[str, Any],
    fragment_path: str | Path = DEFAULT_DIGEST_FRAGMENT,
    now: datetime | None = None,
) -> Path:
    """Write the digest fragment the autonomy digest consumes.

    Atomic-ish: write temp, then rename. Never raises on its own account
    in a way that should break a tick — callers decide.
    """
    now = now or datetime.now(timezone.utc)
    path = Path(fragment_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = dict(payload)
    body["ts"] = now.isoformat()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path
