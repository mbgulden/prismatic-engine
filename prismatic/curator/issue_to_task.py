"""
prismatic/curator/issue_to_task.py - Extract Linear issue to task dictionary.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

# Constants for assigning lanes
BLOCK_LABELS = {"agent:needs-human-review", "dispatch:blocked"}
BACKLOG_READY_LABELS = {"dispatch:ready", "dispatch:backlog"}
PRIORITY_LABELS = {"dispatch:priority", "revenue", "blocker"}
PROJECT_PWP_LABELS = {"project:pwp", "pwp", "prismatic-web-plugin"}
REVIEW_ONLY_LABELS = {
    "agent:peer-review",
    "agent:ned-review",
    "agent:needs-human-review",
    "agent:post-publish-review",
    "agent:post-publish-review-agy-approved",
    "agent:post-publish-review-jules-approved",
}
REVIEW_TITLE_PATTERNS = (
    "peer review",
    "self-review",
    "self review",
    "review-only",
    "review lane",
    "pr review",
    "post-publish review",
    "needs human review",
)
DEFAULT_LINEAR_PRIORITY = int(os.environ.get("PRISMATIC_DEFAULT_LINEAR_PRIORITY", "2"))
AGY_CLOSEOUT_V02_MIN_ISSUE = 4500
#: Supervisor-owned marker inserted into ``AGY_TASK.md`` so the appendix cannot
#: be suppressed by tampering with the Linear description.
AGY_CLOSEOUT_APPENDIX_MARKER = "<!-- prismatic:agy-closeout-v02-appendix -->"


def _parse_linear_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def issue_labels(issue_node: dict) -> set[str]:
    return {
        n.get("name", "")
        for n in issue_node.get("labels", {}).get("nodes", [])
        if n.get("name")
    }


def is_review_only_issue(issue_node: dict | None) -> bool:
    """Return True when Linear work belongs to review, not execution."""
    if not issue_node:
        return False
    labels = issue_labels(issue_node)
    if labels & REVIEW_ONLY_LABELS:
        return True
    state = (issue_node.get("state", {}) or {}).get("name", "").lower()
    if state == "in review":
        return True
    text = f"{issue_node.get('title') or ''}\n{issue_node.get('description') or ''}".lower()
    return any(pattern in text for pattern in REVIEW_TITLE_PATTERNS)


def task_priority_score(issue_node: dict | None = None) -> int:
    """Higher score wins when capacity falls back outside the lane's normal slice."""
    if not issue_node:
        return 0
    labels = issue_labels(issue_node)
    priority = int(issue_node.get("priority") or DEFAULT_LINEAR_PRIORITY)
    score = priority * 100
    if labels & PRIORITY_LABELS:
        score += 1000
    if "revenue" in labels:
        score += 800
    if "blocker" in labels:
        score += 700
    if labels & BACKLOG_READY_LABELS:
        score += 100
    created = _parse_linear_datetime(issue_node.get("createdAt"))
    if created:
        now = datetime.now(created.tzinfo) if created.tzinfo else datetime.utcnow()
        age_days = max(0, (now - created).days)
        score -= min(age_days, 90)
    return score


def assign_lane(
    issue_node: dict | None = None,
    *,
    explicit: bool = False,
    active_project: str = "pwp",
    backlog_age_days: int = 30,
    lane_mode: str = "off",
) -> tuple[str, str | None]:
    """Return (lane, skip_reason). skip_reason is None when dispatchable."""
    if lane_mode == "off":
        return "default", None
    if explicit:
        return "on-demand", None
    if not issue_node:
        return "backlog", None

    labels = issue_labels(issue_node)
    if labels & BLOCK_LABELS:
        return "blocked", "blocked-label"

    state = issue_node.get("state", {}).get("name", "")
    if state in ("Done", "Cancelled", "Canceled"):
        return "blocked", "terminal-state"

    if is_review_only_issue(issue_node):
        return "review", "review-only-lane"

    priority = int(issue_node.get("priority") or DEFAULT_LINEAR_PRIORITY)
    title = (issue_node.get("title") or "").lower()
    desc = (issue_node.get("description") or "").lower()

    if labels & PRIORITY_LABELS or priority >= 3:
        return "priority", None

    active_project = (active_project or "").lower()
    if active_project in ("pwp", "prismatic-web-plugin"):
        if (
            labels & PROJECT_PWP_LABELS
            or "pwp" in title
            or "prismatic web plugin" in title
            or "prismatic web plugin" in desc
        ):
            return "project", None

    # Backlog is mostly opt-in, but Michael (Jun 30 2026) approved auto-eligibility
    # for issues that meet ALL of: (a) has a lane label (agent:*), (b) priority 1
    # OR (priority 2 AND age < 14 days).
    if not (labels & BACKLOG_READY_LABELS):
        has_lane = any(label.startswith("agent:") for label in labels)
        if not has_lane:
            return "blocked", "backlog-not-explicitly-ready-no-lane"
        if priority < 1:
            return "blocked", "backlog-not-explicitly-ready-no-priority"
        if priority >= 3:
            return "blocked", f"backlog-not-explicitly-ready-low-priority({priority})"
        if priority == 2:
            created = _parse_linear_datetime(issue_node.get("createdAt"))
            if created:
                now = (
                    datetime.now(created.tzinfo)
                    if created.tzinfo
                    else datetime.utcnow()
                )
                age_days = (now - created).days
                if age_days >= 14:
                    return (
                        "blocked",
                        f"backlog-not-explicitly-ready-too-old({age_days}d)",
                    )

    created = _parse_linear_datetime(issue_node.get("createdAt"))
    if created and backlog_age_days > 0:
        now = datetime.now(created.tzinfo) if created.tzinfo else datetime.utcnow()
        age_days = (now - created).days
        if age_days > backlog_age_days:
            return "blocked", f"stale-backlog-age-{age_days}d"

    return "backlog", None


def build_task_content_from_issue(iid: str, issue_node: dict) -> str:
    """Build a rich AGY_TASK.md from a Linear issue node."""
    title = issue_node.get("title", "(no title)")
    desc = issue_node.get("description") or "(no description in Linear)"
    priority = issue_node.get("priority")
    state = (issue_node.get("state") or {}).get("name", "")
    labels = [
        label["name"] for label in (issue_node.get("labels") or {}).get("nodes", [])
    ]

    rewrite_map = [
        (
            r"\brun\s+(?:a\s+)?(?:the\s+)?(?:full\s+)?(?:test|tests|pytest|lighthouse|axe|audit|benchmark)(?:\s+suite)?\b",
            "READ existing test/lighthouse/axe output files (lighthouse-report.json, axe-results.json, web-vitals.json) — do NOT launch new runs",
        ),
        (
            r"\brun\s+npm\s+(?:install|test|run)\b",
            "READ package.json + node_modules — do NOT launch npm install/test/run",
        ),
        (
            r"\brun\s+pnpm\s+(?:install|test|run)\b",
            "READ package.json + node_modules — do NOT launch pnpm install/test/run",
        ),
        (
            r"\brun\s+pip\s+install\b",
            "READ requirements / pyproject — do NOT run pip install",
        ),
        (r"\bgit\s+fetch\b", "READ existing remote refs — do NOT run git fetch"),
        (r"\bgit\s+clone\b", "READ existing repo state — do NOT run git clone"),
        (
            r"\bawait\s+(?:its|the|background)\s+results?\b",
            "READ existing result files synchronously — do NOT wait on background tasks",
        ),
        (
            r"\bwait\s+for\s+(?:\w+)\s+(?:to\s+)?finish\b",
            "READ existing result file for that task — do NOT wait",
        ),
    ]
    safe_desc = desc
    for pat, replacement in rewrite_map:
        safe_desc = re.sub(pat, replacement, safe_desc, flags=re.IGNORECASE)

    bg_guard = (
        "\n\n# AGY sandbox guard (added by supervisor 2026-06-26)\n"
        "This task has been rewritten to be output-driven. Do NOT:\n"
        "  * launch pytest / lighthouse / axe / npm / pnpm / pip / cargo / go test\n"
        "  * run git fetch / git clone\n"
        "  * block on 'Waiting for background task' / 'await its results'\n"
        "  * start any long-running subprocess as a background task\n"
        "If the task requires a missing artifact, write a `## MISSING ARTIFACTS`\n"
        "section in RESULT.md naming the artifact and the command that should\n"
        "produce it. The runner will generate it next sprint.\n"
    )

    iid_match = re.fullmatch(r"GRO-([0-9]+)", iid, re.IGNORECASE)
    iid_number = int(iid_match.group(1)) if iid_match else None
    modern_task = iid_number is not None and iid_number >= AGY_CLOSEOUT_V02_MIN_ISSUE
    appendix = ""
    if modern_task:
        appendix_path = (
            Path(__file__).resolve().parents[1]
            / "skills"
            / "prismatic-agent-closeout-contract"
            / "templates"
            / "AGY_TASK_APPENDIX.md"
        )
        if not appendix_path.exists():
            raise FileNotFoundError(
                f"AGY_TASK.md appendix missing for {iid} at {appendix_path}"
            )
        appendix = (
            "\n\n"
            + AGY_CLOSEOUT_APPENDIX_MARKER
            + "\n"
            + appendix_path.read_text(encoding="utf-8").strip()
            + "\n"
            + AGY_CLOSEOUT_APPENDIX_MARKER
            + "\n"
        )

    return (
        f"WORKDIR: prismatic\n"
        f"ISSUE: {iid}\n"
        f"TITLE: {title}\n"
        f"PRIORITY: {priority}\n"
        f"STATE: {state}\n"
        f"LABELS: {', '.join(labels) if labels else '(none)'}\n"
        f"\n"
        f"DESCRIPTION:\n"
        f"{safe_desc}\n"
        f"{bg_guard}"
        f"{appendix}"
        f"\n"
        f"MANDATORY FINISH PROTOCOL:\n"
        f"1. Write a complete summary to AGY_TASK.md sibling RESULT.md "
        f"(use Write tool). RESULT.md must include: what you did, files "
        f"changed, commit hashes, test results (if any), follow-ups.\n"
        f"2. Run self-review: python3 ~/.hermes/profiles/orchestrator/scripts/"
        f"agy_self_review.py {iid}\n"
        f"3. After self-review posts, output DONE: {iid} <one-line summary> "
        f"as the LAST line.\n"
        f"4. If you cannot save RESULT.md, output ERROR: {iid} <reason> "
        f"instead.\n"
    )


def issue_to_task(
    issue_node: dict,
    *,
    lane_mode: str = "off",
    active_project: str = "pwp",
    backlog_age_days: int = 30,
) -> dict | None:
    """Convert Linear issue node to a task dict, loading task content from cache."""
    iid = issue_node["identifier"]
    state = issue_node.get("state", {}).get("name", "")
    if state in ("Done", "Cancelled", "Canceled"):
        return None
    if "[done]" in iid.lower():
        return None

    lane, skip_reason = assign_lane(
        issue_node,
        active_project=active_project,
        backlog_age_days=backlog_age_days,
        lane_mode=lane_mode,
    )
    if skip_reason:
        print(f"  [lane-skip] {iid}: {skip_reason}", flush=True)
        return None

    cached = Path(f"/tmp/issue-batches/{iid}.txt")
    wd = "prismatic"
    if cached.exists():
        try:
            cached_text = cached.read_text(errors="replace")
            for line in cached_text.split("\n")[:5]:
                if line.startswith("WORKDIR:"):
                    wd = line.split(":", 1)[1].strip() or "prismatic"
                    break
            print(
                f"  [task-cache] {iid}: using cache WORKDIR only; task body rebuilt from Linear",
                flush=True,
            )
        except Exception as e:
            print(
                f"  [task-cache] {iid}: ignored unreadable cache file: {e}", flush=True
            )

    task_content = build_task_content_from_issue(iid, issue_node)
    if wd != "prismatic":
        task_content = re.sub(
            r"^WORKDIR:.*$", f"WORKDIR: {wd}", task_content, count=1, flags=re.MULTILINE
        )

    return {
        "issue_id": iid,
        "task_content": task_content,
        "workdir": wd,
        "lane": lane,
        "priority_score": task_priority_score(issue_node),
        "labels": issue_labels(issue_node),
    }
