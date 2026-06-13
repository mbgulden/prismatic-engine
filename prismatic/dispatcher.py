"""
Prismatic Engine — Event Dispatcher
====================================

Standalone event loop that polls Linear for label-assigned issues,
routes work to the right agent via signal providers or CLI launches,
and manages pipeline state transitions.

No Hermes dependencies — uses only stdlib, ``requests`` (optional),
and the ``prismatic`` package modules.

Usage
-----
Direct::

    python -m prismatic.dispatcher

Entry-point (after ``pip install``)::

    prismatic-engine
"""

from __future__ import annotations

import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable

# ── Relative package imports ──────────────────────────────────
from .providers.signals import create_signal_provider, SignalPayload
from .credit_policy_engine import (
    CreditPolicyEngine,
    PolicyAction,
    PolicyDecision,
    evaluate_agent_launch,
    AGENT_PROVIDER_MAP,
)
from .telemetry import get_collector


# ═══════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════

TEAM_ID: str = os.environ.get("PRISMATIC_TEAM_ID", "")

DEFAULT_DB_PATH: str = os.path.join(
    os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"),
    "event_router.db",
)

# Agent binary paths — override via env vars
AGY_PATH: str = os.environ.get("AGY_PATH", "agy")
JULES_PATH: str = os.environ.get("JULES_PATH", "jules")
CODEX_PATH: str = os.environ.get("CODEX_PATH", "codex")

# Polling interval (seconds)
POLL_INTERVAL: int = int(os.environ.get("PRISMATIC_POLL_INTERVAL", "30"))
MAX_CYCLES_BEFORE_RECOVER: int = int(
    os.environ.get("PRISMATIC_MAX_CYCLES_BEFORE_RECOVER", "6")
)

# Nudge directory for file-based signal providers
NUDGE_DIR: str = os.environ.get("PRISMATIC_NUDGE_DIR", "/tmp/prismatic")

# Headless environment for subprocess agents (prevents TTY ioctl crashes)
HEADLESS_ENV: dict[str, str] = {
    **os.environ,
    "TERM": "xterm",
    "DEBIAN_FRONTEND": "noninteractive",
    "PYTHONUNBUFFERED": "1",
}

# Pipeline metrics log for dashboard consumption
PIPELINE_METRICS_PATH: str = os.environ.get(
    "PRISMATIC_METRICS_PATH", "/tmp/pipeline_metrics.jsonl"
)


def log_completed_pipeline_metrics(
    issue_id: str,
    agent: str,
    status: str,
    reason: str = "",
    cost: int = 0,
    **kwargs,
) -> None:
    """Append a line to the pipeline metrics JSONL file.

    This is consumed by ``scripts/pipeline_dashboard.py`` for
    real-time pipeline health monitoring.
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "issue_id": issue_id,
        "agent": agent,
        "status": status,
        "reason": reason,
        "cost": cost,
        **kwargs,
    }
    try:
        metrics_dir = os.path.dirname(PIPELINE_METRICS_PATH)
        if metrics_dir:
            os.makedirs(metrics_dir, exist_ok=True)
        with open(PIPELINE_METRICS_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as exc:
        print(f"[dispatcher] Failed to write metrics: {exc}")


# ═══════════════════════════════════════════════════════════════
# Linear GraphQL helpers
# ═══════════════════════════════════════════════════════════════


def _linear_api_key() -> str:
    """Get the Linear API key from the environment.

    Raises RuntimeError if ``LINEAR_API_KEY`` is not set.
    """
    key = os.environ.get("LINEAR_API_KEY")
    if not key:
        raise RuntimeError(
            "LINEAR_API_KEY environment variable is required"
        )
    return key


def gql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute a Linear GraphQL query or mutation.

    Uses the ``LINEAR_API_KEY`` env var for authentication. No
    ``Bearer`` prefix is added — the raw key value is used directly
    as the HTTP ``Authorization`` header (Linear's API token format).

    Args:
        query: GraphQL query/mutation string.
        variables: Optional dict of variable values.

    Returns:
        The ``data`` dict from the response.

    Raises:
        RuntimeError: On HTTP or GraphQL errors.
    """
    import urllib.request
    import urllib.error

    api_key = _linear_api_key()
    payload = json.dumps({
        "query": query,
        "variables": variables or {},
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": api_key,  # No "Bearer" prefix
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise RuntimeError(
            f"Linear API HTTP {exc.code}: {body[:500]}"
        ) from exc
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Linear API request failed: {exc}") from exc

    if "errors" in body:
        raise RuntimeError(
            f"Linear API error(s): {json.dumps(body['errors'], indent=2)[:1000]}"
        )

    return body.get("data", {})


# ═══════════════════════════════════════════════════════════════
# Linear Label / Issue helpers
# ═══════════════════════════════════════════════════════════════


def get_label_id(label_name: str, *, team_id: str | None = None) -> str | None:
    """Get (or create) the Linear label ID for *label_name*.

    If the label does not exist in the given team, it is created
    automatically.

    Args:
        label_name: Display name of the label (e.g. ``"agent::fred"``).
        team_id: Team ID override. Falls back to ``TEAM_ID`` constant.

    Returns:
        Label ID string, or ``None`` if lookup/creation failed.
    """
    tid = team_id or TEAM_ID
    if not tid:
        raise RuntimeError("TEAM_ID is not set — provide team_id or set PRISMATIC_TEAM_ID")

    # Look up existing label
    query = """
    query GetTeamLabels($teamId: String!) {
        team(id: $teamId) {
            labels {
                nodes {
                    id
                    name
                }
            }
        }
    }
    """
    data = gql(query, {"teamId": tid})
    labels = data.get("team", {}).get("labels", {}).get("nodes", [])
    for label in labels:
        if label["name"] == label_name:
            return label["id"]

    # Create the label
    create_query = """
    mutation CreateLabel($teamId: String!, $name: String!) {
        issueLabelCreate(input: {teamId: $teamId, name: $name}) {
            issueLabel { id name }
            success
        }
    }
    """
    result = gql(create_query, {"teamId": tid, "name": label_name})
    created = result.get("issueLabelCreate", {}).get("issueLabel")
    if created:
        return created["id"]

    return None


def get_issues_with_label(
    label_name: str,
    *,
    team_id: str | None = None,
    max_issues: int = 20,
) -> list[dict[str, Any]]:
    """Query issues that have a specific label by *label_name*.

    Uses the label label (name-based) approach — iterates all issues
    in the team and filters by label name client-side. Efficient for
    teams with fewer than ~200 active issues.

    Args:
        label_name: Label name to search for.
        team_id: Team ID override.
        max_issues: Maximum issues to return.

    Returns:
        List of issue dicts with keys: ``id``, ``title``, ``description``,
        ``state``, ``assignee``, ``labels``, ``url``.
    """
    tid = team_id or TEAM_ID
    if not tid:
        raise RuntimeError("TEAM_ID is not set")

    query = """
    query TeamIssues($teamId: String!, $first: Int!) {
        team(id: $teamId) {
            issues(first: $first, orderBy: updatedAt) {
                nodes {
                    id
                    identifier
                    title
                    description
                    state { name type }
                    assignee { id name }
                    labels { nodes { id name } }
                    url
                }
            }
        }
    }
    """
    data = gql(query, {"teamId": tid, "first": max_issues})
    issues = data.get("team", {}).get("issues", {}).get("nodes", [])

    results = []
    for issue in issues:
        label_names = [
            lab["name"]
            for lab in issue.get("labels", {}).get("nodes", [])
        ]
        if label_name in label_names:
            results.append({
                "id": issue["id"],
                "identifier": issue.get("identifier", ""),
                "title": issue.get("title", ""),
                "description": issue.get("description", ""),
                "state": issue.get("state", {}),
                "assignee": issue.get("assignee"),
                "labels": label_names,
                "url": issue.get("url", ""),
            })

    return results


def get_issue_labels(issue_id: str) -> list[dict[str, str]]:
    """Get the current labels on a specific issue.

    Args:
        issue_id: Linear issue UUID.

    Returns:
        List of ``{"id": ..., "name": ...}`` dicts.
    """
    query = """
    query IssueLabels($issueId: String!) {
        issue(id: $issueId) {
            labels { nodes { id name } }
        }
    }
    """
    data = gql(query, {"issueId": issue_id})
    return (
        data.get("issue", {})
        .get("labels", {})
        .get("nodes", [])
    )


def set_labels(issue_id: str, label_ids: list[str]) -> bool:
    """Set the exact set of labels on an issue (replaces all existing).

    Args:
        issue_id: Linear issue UUID.
        label_ids: Complete list of label IDs to assign.

    Returns:
        ``True`` on success.
    """
    query = """
    mutation SetLabels($issueId: String!, $labelIds: [String!]!) {
        issueUpdate(id: $issueId, input: {labelIds: {set: $labelIds}}) {
            success
        }
    }
    """
    data = gql(query, {"issueId": issue_id, "labelIds": label_ids})
    return data.get("issueUpdate", {}).get("success", False)


def transition_label(
    issue_id: str,
    remove_label: str,
    add_label: str,
    *,
    team_id: str | None = None,
) -> bool:
    """Remove one label by name and add another.

    This is the core pipeline handoff operation: remove the current
    agent label and add the next agent label.

    Args:
        issue_id: Linear issue UUID.
        remove_label: Name of the label to remove (e.g. ``"agent::fred"``).
        add_label: Name of the label to add (e.g. ``"agent::kai"``).
        team_id: Team ID for label resolution.

    Returns:
        ``True`` if the transition succeeded.
    """
    tid = team_id or TEAM_ID
    current = get_issue_labels(issue_id)
    current_ids = [lab["id"] for lab in current]
    current_names = [lab["name"] for lab in current]

    # Remove the old label
    new_ids = [
        lab["id"]
        for lab in current
        if lab["name"] != remove_label
    ]

    # Add the new label if not already present
    if add_label and add_label not in current_names:
        add_id = get_label_id(add_label, team_id=tid)
        if add_id:
            new_ids.append(add_id)

    # If nothing changed, skip the mutation
    if set(new_ids) == set(current_ids):
        return True

    return set_labels(issue_id, new_ids)


def add_comment(issue_id: str, body: str) -> bool:
    """Post a comment on a Linear issue.

    Args:
        issue_id: Linear issue UUID.
        body: Comment text (Markdown-supported).

    Returns:
        ``True`` if the comment was posted.
    """
    query = """
    mutation AddComment($issueId: String!, $body: String!) {
        commentCreate(input: {issueId: $issueId, body: $body}) {
            success
            comment { id }
        }
    }
    """
    data = gql(query, {"issueId": issue_id, "body": body})
    return data.get("commentCreate", {}).get("success", False)


# ═══════════════════════════════════════════════════════════════
# Agent Configuration
# ═══════════════════════════════════════════════════════════════

AGENT_CONFIG: dict[str, dict[str, Any]] = {
    "fred": {
        "executable": AGY_PATH,  # fred is a Hermes/AGY instance
        "mode": "signal",
        "timeout": 300,
        "next_label": "agent::kai",
        "description": "Hermes orchestrator — first in pipeline",
    },
    "kai": {
        "executable": "kai",
        "mode": "signal",
        "timeout": 600,
        "next_label": "agent::agy",
        "description": "Active Oahu Tours bot — review & deploy",
    },
    "agy": {
        "executable": AGY_PATH,
        "mode": "launch",
        "timeout": 900,
        "next_label": "agent::jules",
        "description": "Antigravity CLI — code generation",
    },
    "jules": {
        "executable": JULES_PATH,
        "mode": "launch",
        "timeout": 600,
        "next_label": "agent::codex",
        "description": "Jules CLI — testing & QA",
    },
    "codex": {
        "executable": CODEX_PATH,
        "mode": "launch",
        "timeout": 1200,
        "next_label": "",  # terminal — pipeline complete
        "description": "Codex CLI — final polish & PR",
    },
}


# ═══════════════════════════════════════════════════════════════
# Agent Launchers
# ═══════════════════════════════════════════════════════════════

# Default signal provider instance — file-based, writing to NUDGE_DIR
_signal_provider: Any = None


def _get_signal_provider():
    """Lazy-init the default file-based signal provider."""
    global _signal_provider
    if _signal_provider is None:
        _signal_provider = create_signal_provider({
            "type": "file",
            "directory": NUDGE_DIR,
        })
    return _signal_provider


def signal_fred(issue_id: str, title: str = "", priority: int = 3) -> bool:
    """Signal agent:fred by writing a nudge file.

    Args:
        issue_id: Linear issue identifier/ID.
        title: Human-readable summary.
        priority: Signal priority (0-5).

    Returns:
        ``True`` if the signal was written.
    """
    provider = _get_signal_provider()
    return provider.send_work(
        target="fred",
        issue_id=issue_id,
        title=title or f"Work on {issue_id}",
        priority=priority,
    )


def signal_kai(
    issue_id: str,
    title: str = "",
    priority: int = 3,
    signal_type: str = "",
) -> bool:
    """Signal agent:kai by writing a nudge file.

    Args:
        issue_id: Linear issue identifier/ID.
        title: Human-readable summary.
        priority: Signal priority (0-5).
        signal_type: Optional signal classification
            (e.g. ``"agy_review_complete"``).

    Returns:
        ``True`` if the signal was written.
    """
    provider = _get_signal_provider()
    return provider.send_work(
        target="kai",
        issue_id=issue_id,
        title=title or f"Work on {issue_id}",
        priority=priority,
        signal_type=signal_type,
    )


def launch_agy(issue_id: str, task: str = "") -> subprocess.Popen | None:
    """Launch the AGY CLI in headless mode for the given issue.

    The AGY process is started as a background subprocess with the
    issue ID passed via the ``--issue`` flag.

    Args:
        issue_id: Linear issue UUID or identifier.
        task: Optional task description.

    Returns:
        ``subprocess.Popen`` handle, or ``None`` if launch failed.
    """
    if not os.path.exists(AGY_PATH):
        print(f"[dispatcher] AGY binary not found at {AGY_PATH}")
        return None

    try:
        cmd = [
            AGY_PATH,
            "--headless",
            "--issue", issue_id,
        ]
        if task:
            cmd.extend(["--task", task])

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            env=HEADLESS_ENV,
        )
        print(f"[dispatcher] Launched AGY (pid={proc.pid}) for issue {issue_id}")
        return proc
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[dispatcher] Failed to launch AGY: {exc}")
        return None


def launch_jules(issue_id: str, task: str = "") -> subprocess.Popen | None:
    """Launch the Jules CLI for the given issue.

    Args:
        issue_id: Linear issue UUID or identifier.
        task: Optional task description.

    Returns:
        ``subprocess.Popen`` handle, or ``None`` if launch failed.
    """
    if not os.path.exists(JULES_PATH):
        print(f"[dispatcher] Jules binary not found at {JULES_PATH}")
        return None

    try:
        cmd = [JULES_PATH, "--issue", issue_id]
        if task:
            cmd.extend(["--task", task])

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            env=HEADLESS_ENV,
        )
        print(f"[dispatcher] Launched Jules (pid={proc.pid}) for issue {issue_id}")
        return proc
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[dispatcher] Failed to launch Jules: {exc}")
        return None


def launch_codex(issue_id: str, task: str = "") -> subprocess.Popen | None:
    """Launch the Codex CLI for the given issue.

    Args:
        issue_id: Linear issue UUID or identifier.
        task: Optional task description.

    Returns:
        ``subprocess.Popen`` handle, or ``None`` if launch failed.
    """
    if not os.path.exists(CODEX_PATH):
        print(f"[dispatcher] Codex binary not found at {CODEX_PATH}")
        return None

    try:
        cmd = [CODEX_PATH, "--issue", issue_id]
        if task:
            cmd.extend(["--task", task])

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            env=HEADLESS_ENV,
        )
        print(f"[dispatcher] Launched Codex (pid={proc.pid}) for issue {issue_id}")
        return proc
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[dispatcher] Failed to launch Codex: {exc}")
        return None


# Map agent name → launch function
AGENT_LAUNCHERS: dict[str, Callable[..., Any]] = {
    "fred": signal_fred,
    "kai": signal_kai,
    "agy": launch_agy,
    "jules": launch_jules,
    "codex": launch_codex,
}


# ═══════════════════════════════════════════════════════════════
# Pipeline Router (thin wrapper around prismatic.router)
# ═══════════════════════════════════════════════════════════════

def load_pipeline_templates(config_path: str = "") -> dict[str, Any]:
    """Load pipeline definitions from a YAML/JSON config file.

    The default config path is ``~/.config/prismatic/pipelines.yaml``, 
    overridable via the ``PRISMATIC_PIPELINE_CONFIG`` env var.

    Delegates to :func:`prismatic.router.load_pipeline_templates`.
    """
    from .router import load_pipeline_templates as _load

    default_config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "prismatic" / "pipelines.yaml"
    
    path = (
        config_path
        or os.environ.get("PRISMATIC_PIPELINE_CONFIG")
        or str(default_config)
    )
    return _load(path)


def detect_pipeline_type(
    issue: dict[str, Any],
    pipelines: dict[str, Any] | None = None,
) -> str | None:
    """Auto-detect pipeline type from issue labels/keywords.

    Delegates to :func:`prismatic.router.detect_pipeline_type`.
    """
    from .router import detect_pipeline_type as _detect

    if pipelines is None:
        try:
            pipelines = load_pipeline_templates()
        except (FileNotFoundError, ValueError):
            pipelines = {"pipelines": {}}

    return _detect(issue, pipelines)


def apply_pipeline(
    issue_id: str,
    pipeline_type: str,
    pipelines: dict[str, Any] | None = None,
) -> bool:
    """Set the first agent label and append pipeline context.

    Delegates to :func:`prismatic.router.apply_pipeline`.

    Returns:
        ``True`` on success.
    """
    from .router import apply_pipeline as _apply

    if pipelines is None:
        try:
            pipelines = load_pipeline_templates()
        except (FileNotFoundError, ValueError):
            pipelines = {"pipelines": {}}

    try:
        _apply(issue_id, pipeline_type, pipelines)
        return True
    except (ValueError, RuntimeError) as exc:
        print(f"[dispatcher] apply_pipeline failed: {exc}")
        return False


def setup_pipeline_issues(max_issues: int = 20) -> list[dict[str, Any]]:
    """Discover issues that match pipeline triggers and set them up.

    Scans the team's issues for pipeline-type labels (``pipeline::*``)
    or keyword triggers, then applies the first agent label.

    Args:
        max_issues: Max issues to scan.

    Returns:
        List of issue dicts that were successfully set up.
    """
    pipelines = load_pipeline_templates()
    if not pipelines.get("pipelines"):
        print("[dispatcher] No pipeline templates found")
        return []

    query = """
    query TeamIssues($teamId: String!, $first: Int!) {
        team(id: $teamId) {
            issues(first: $first, orderBy: updatedAt) {
                nodes {
                    id
                    identifier
                    title
                    description
                    labels { nodes { id name } }
                }
            }
        }
    }
    """
    data = gql(query, {"teamId": TEAM_ID, "first": max_issues})
    issues = data.get("team", {}).get("issues", {}).get("nodes", [])

    setup_issues = []
    for issue in issues:
        issue_dict = {
            "id": issue["id"],
            "identifier": issue.get("identifier", ""),
            "title": issue.get("title", ""),
            "description": issue.get("description", ""),
            "labels": [
                lab["name"]
                for lab in issue.get("labels", {}).get("nodes", [])
            ],
        }

        # Skip if already has an agent label
        if any(lab.startswith("agent::") for lab in issue_dict["labels"]):
            continue

        pipeline_type = detect_pipeline_type(issue_dict, pipelines)
        if pipeline_type:
            success = apply_pipeline(issue["id"], pipeline_type, pipelines)
            if success:
                print(
                    f"[dispatcher] Set up pipeline {pipeline_type!r} "
                    f"on {issue_dict['identifier']}: {issue_dict['title']}"
                )
                setup_issues.append(issue_dict)

    return setup_issues


# ═══════════════════════════════════════════════════════════════
# AGY Process Management
# ═══════════════════════════════════════════════════════════════


def cleanup_stale_agy(max_age_minutes: int = 5) -> int:
    """Kill AGY processes older than *max_age_minutes*.

    Scans the process table for ``agy`` processes and sends SIGTERM
    to any that have been running longer than the threshold.

    Args:
        max_age_minutes: Maximum allowed lifetime in minutes.

    Returns:
        Number of processes killed.
    """
    import subprocess as _subprocess

    killed = 0
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=max_age_minutes)

    try:
        # Use ps to find AGY processes with their start times
        result = _subprocess.run(
            ["ps", "-eo", "pid,etime,args", "--no-headers"],
            capture_output=True,
            text=True,
            timeout=10,
        )

        for line in result.stdout.splitlines():
            if "agy" not in line.lower():
                continue
            parts = line.strip().split(None, 2)
            if len(parts) < 3:
                continue
            pid_str = parts[0]
            etime_str = parts[1]

            # Parse elapsed time format: [[DD-]hh:]mm:ss
            age_seconds = _parse_etime(etime_str)
            if age_seconds is None:
                continue

            # Kill if older than max_age_minutes
            if age_seconds > max_age_minutes * 60:
                try:
                    os.kill(int(pid_str), signal.SIGTERM)
                    killed += 1
                    print(
                        f"[dispatcher] Killed stale AGY pid={pid_str} "
                        f"(age={etime_str})"
                    )
                except (OSError, ValueError) as exc:
                    print(
                        f"[dispatcher] Failed to kill AGY pid={pid_str}: {exc}"
                    )

    except (subprocess.SubprocessError, FileNotFoundError, OSError) as exc:
        print(f"[dispatcher] cleanup_stale_agy failed: {exc}")

    return killed


def _parse_etime(etime_str: str) -> float | None:
    """Parse ``ps`` elapsed-time format into total seconds.

    Formats handled::

        MM:SS
        HH:MM:SS
        DD-HH:MM:SS
    """
    try:
        if "-" in etime_str:
            days, rest = etime_str.split("-", 1)
            days = int(days)
        else:
            days = 0
            rest = etime_str

        parts = rest.strip().split(":")
        if len(parts) == 2:
            hours, minutes, seconds = 0, int(parts[0]), int(parts[1])
        elif len(parts) == 3:
            hours, minutes, seconds = int(parts[0]), int(parts[1]), int(parts[2])
        else:
            return None

        return float(days * 86400 + hours * 3600 + minutes * 60 + seconds)
    except (ValueError, IndexError):
        return None


def recover_stalled_agy(
    max_retries: int = 3,
    escalate_to: str = "fred",
) -> None:
    """Retry stalled AGY tasks, then escalate to another agent.

    A stalled AGY task is one where the issue still has an
    ``agent::agy`` label after ``MAX_CYCLES_BEFORE_RECOVER``
    dispatcher cycles with no visible progress.

    .. note::
        This requires the deduplication database to track cycle
        counts per issue. The database path is ``DEFAULT_DB_PATH``.

    Args:
        max_retries: Max retry attempts before escalation.
        escalate_to: Agent to escalate to after exhausting retries.
    """
    db_path = DEFAULT_DB_PATH
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS agy_stall_tracker (
            issue_id TEXT PRIMARY KEY,
            cycle_count INTEGER DEFAULT 0,
            last_seen TEXT,
            escalated INTEGER DEFAULT 0
        )
        """
    )

    try:
        # Find issues with agent::agy label that have been seen multiple cycles
        issues = get_issues_with_label("agent::agy")

        for issue in issues:
            issue_id = issue["id"]

            # Update or increment cycle count
            cursor.execute(
                "SELECT cycle_count FROM agy_stall_tracker WHERE issue_id = ?",
                (issue_id,),
            )
            row = cursor.fetchone()

            if row:
                cycle_count = row[0] + 1
            else:
                cycle_count = 1

            cursor.execute(
                """
                INSERT OR REPLACE INTO agy_stall_tracker
                    (issue_id, cycle_count, last_seen, escalated)
                VALUES (?, ?, ?, 0)
                """,
                (issue_id, cycle_count, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()

            if cycle_count >= max_retries:
                # Escalate — kill AGY and transition to escalate_to agent
                cleanup_stale_agy(max_age_minutes=0)  # Kill all AGY processes

                # Transition label
                transition_label(
                    issue_id,
                    remove_label="agent::agy",
                    add_label=f"agent::{escalate_to}",
                )

                # Post escalation comment
                add_comment(
                    issue_id,
                    f"⚠️ **AGY stalled** after {max_retries} cycles. "
                    f"Escalating to **{escalate_to}**.",
                )

                # Mark as escalated
                cursor.execute(
                    "UPDATE agy_stall_tracker SET escalated = 1 WHERE issue_id = ?",
                    (issue_id,),
                )
                conn.commit()

                # Signal the escalation target
                launcher = AGENT_LAUNCHERS.get(escalate_to)
                if launcher:
                    launcher(
                        issue_id,
                        title=f"Escalated: {issue.get('title', issue_id)}",
                        priority=5,
                    )

                print(
                    f"[dispatcher] Escalated stalled AGY issue "
                    f"{issue.get('identifier', issue_id)} to {escalate_to}"
                )

            else:
                print(
                    f"[dispatcher] AGY issue {issue.get('identifier', issue_id)} "
                    f"stalled (cycle {cycle_count}/{max_retries})"
                )

    except Exception as exc:
        print(f"[dispatcher] recover_stalled_agy failed: {exc}")
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
# Agent Command Parsing
# ═══════════════════════════════════════════════════════════════


def process_agent_commands(
    issue: dict[str, Any],
    pipelines: dict[str, Any] | None = None,
) -> list[str]:
    """Parse ``/agent:<name>`` commands from Linear issue comments.

    Looks at the most recent comments on the issue and extracts
    any ``/agent:<name>`` directives. This allows users to manually
    route work by commenting on an issue.

    Args:
        issue: Issue dict (must have ``id`` key).
        pipelines: Optional pipeline config (loaded automatically if
            not provided).

    Returns:
        List of agent names that were successfully dispatched to.
    """
    issue_id = issue.get("id", "")
    if not issue_id:
        return []

    # Fetch the last 5 comments
    query = """
    query IssueComments($issueId: String!) {
        issue(id: $issueId) {
            comments(first: 5, orderBy: createdAt, includeArchived: false) {
                nodes {
                    id
                    body
                    createdAt
                }
            }
        }
    }
    """
    try:
        data = gql(query, {"issueId": issue_id})
    except RuntimeError as exc:
        print(f"[dispatcher] Failed to fetch comments: {exc}")
        return []

    comments = data.get("issue", {}).get("comments", {}).get("nodes", [])
    dispatched = []

    # Process from newest to oldest
    for comment in reversed(comments):
        body = comment.get("body", "")
        # Match /agent:name or /agent:name:arg
        matches = re.findall(r"/agent:(\w+)(?::(\w+))?", body)
        for agent_name, arg in matches:
            agent_name = agent_name.lower()

            if agent_name in AGENT_LAUNCHERS:
                launcher = AGENT_LAUNCHERS[agent_name]
                result = launcher(
                    issue_id,
                    title=issue.get("title", ""),
                    priority=3 if arg != "urgent" else 5,
                )
                if result:
                    dispatched.append(agent_name)
                    print(
                        f"[dispatcher] Dispatched /agent:{agent_name} "
                        f"on {issue.get('identifier', issue_id)}"
                    )
            else:
                print(
                    f"[dispatcher] Unknown agent '{agent_name}' "
                    f"in command on {issue.get('identifier', issue_id)}"
                )

    return dispatched


# ═══════════════════════════════════════════════════════════════
# Deduplication (stub)
# ═══════════════════════════════════════════════════════════════

class EventRouterDedup:
    """Deduplication tracker for event router cycles.

    Tracks which issues have been processed in which cycle to prevent
    redundant dispatches. Uses a SQLite database at *db_path*.

    This is a minimal standalone version (no external deps).
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or DEFAULT_DB_PATH
        db_dir = os.path.dirname(self._db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dedup_log (
                issue_id TEXT NOT NULL,
                agent_label TEXT NOT NULL,
                cycle_id TEXT NOT NULL,
                processed_at TEXT NOT NULL,
                PRIMARY KEY (issue_id, agent_label, cycle_id)
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_dedup_cycle
            ON dedup_log(cycle_id)
            """
        )
        # ── Label snapshots — track which labels issues had per cycle ──
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS label_snapshots (
                issue_id TEXT NOT NULL,
                label_name TEXT NOT NULL,
                cycle_id TEXT NOT NULL,
                seen_at TEXT NOT NULL,
                PRIMARY KEY (issue_id, label_name, cycle_id)
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_label_snapshots_issue
            ON label_snapshots(issue_id, label_name)
            """
        )
        self._conn.commit()

    def is_processed(self, issue_id: str, agent_label: str, cycle_id: str) -> bool:
        """Check if this issue+agent+cycle was already processed."""
        cursor = self._conn.execute(
            "SELECT 1 FROM dedup_log WHERE issue_id=? AND agent_label=? AND cycle_id=?",
            (issue_id, agent_label, cycle_id),
        )
        return cursor.fetchone() is not None

    def mark_processed(self, issue_id: str, agent_label: str, cycle_id: str) -> None:
        """Record that this issue+agent+cycle was processed."""
        self._conn.execute(
            """
            INSERT OR IGNORE INTO dedup_log (issue_id, agent_label, cycle_id, processed_at)
            VALUES (?, ?, ?, ?)
            """,
            (issue_id, agent_label, cycle_id, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def get_cycle_count(self, issue_id: str, agent_label: str) -> int:
        """Count how many cycles this issue has been sitting on a label."""
        cursor = self._conn.execute(
            "SELECT COUNT(*) FROM dedup_log WHERE issue_id=? AND agent_label=?",
            (issue_id, agent_label),
        )
        return cursor.fetchone()[0]

    def snapshot_labels(
        self, issue_id: str, label_names: list[str], cycle_id: str
    ) -> None:
        """Record the current label set for an issue in this cycle."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.executemany(
            """
            INSERT OR IGNORE INTO label_snapshots
            (issue_id, label_name, cycle_id, seen_at)
            VALUES (?, ?, ?, ?)
            """,
            [(issue_id, name, cycle_id, now) for name in label_names],
        )
        self._conn.commit()

    def had_label(self, issue_id: str, label_name: str) -> bool:
        """Check if an issue ever had a specific label in any cycle."""
        cursor = self._conn.execute(
            "SELECT 1 FROM label_snapshots WHERE issue_id=? AND label_name=?",
            (issue_id, label_name),
        )
        return cursor.fetchone() is not None

    def had_labels(self, issue_id: str, label_names: list[str]) -> bool:
        """Check if an issue ever had ALL of the specified labels."""
        placeholders = ",".join("?" * len(label_names))
        params = [issue_id] + label_names
        cursor = self._conn.execute(
            f"SELECT COUNT(DISTINCT label_name) FROM label_snapshots "
            f"WHERE issue_id=? AND label_name IN ({placeholders})",
            params,
        )
        count = cursor.fetchone()[0]
        return count == len(label_names)

    def close(self) -> None:
        self._conn.close()


# Re-export for external callers
dedup = EventRouterDedup


# ═══════════════════════════════════════════════════════════════
# Origin Completion Detection (Generalized Peer-Review Loop)
# ═══════════════════════════════════════════════════════════════


def detect_origin_completions(
    dedup: EventRouterDedup,
    cycle_id: str,
) -> int:
    """Detect completed review loops and signal origin agents.

    When an issue transitions through a reviewer agent (e.g. ``agent:agy``)
    and lands at ``agent:fred``, the dispatcher determines which agent
    originally requested the review and signals them to pick up results.

    This closes the feedback loop: Kai→AGY→Kai, Ned→AGY→Ned, etc.
    — any agent can request peer review and get results back automatically.

    Strategy:
      1. Record label snapshots for all agent-labeled issues (builds
         history across cycles).
      2. Query all ``agent:fred`` issues.
      3. For each, check the label history for a reviewer agent (typically
         ``agent:agy``) and an origin agent (any other agent that preceded
         the reviewer).
      4. If origin found and not already signalled this cycle, signal the
         origin with ``signal_type: "review_complete"`` and
         ``origin_agent`` metadata.

    Returns:
        Number of origin signals sent.
    """
    signalled = 0

    # 1. Snapshot: record current labels for issues the dispatcher
    #    has seen (builds label history over cycles)
    agent_labels = [
        f"agent::{name}" for name in AGENT_CONFIG
    ] + [
        # Also track single-colon variants (actual Linear label names)
        f"agent:{name}" for name in AGENT_CONFIG
    ]
    for label_name in agent_labels:
        try:
            issues = get_issues_with_label(label_name, max_issues=50)
        except Exception:
            continue
        for issue in issues:
            try:
                dedup.snapshot_labels(
                    issue["id"],
                    issue.get("labels", []),
                    cycle_id,
                )
            except Exception:
                pass

    # 2. Query agent:fred issues (the ACTUAL Linear label format)
    try:
        fred_issues = get_issues_with_label("agent:fred", max_issues=100)
    except Exception:
        return 0

    # 3. Detect origin→reviewer→fred transitions
    for issue in fred_issues:
        issue_id = issue["id"]
        identifier = issue.get("identifier", issue_id)
        current_labels = issue.get("labels", [])

        # Skip if still has agent:agy (transition not complete)
        if "agent:agy" in current_labels:
            continue

        # Build the dedup key for this specific detection
        dedup_key = f"origin_complete:{identifier}"

        # Skip if already signalled this cycle
        if dedup.is_processed(issue_id, dedup_key, cycle_id):
            continue

        # Determine the origin agent: look through all configured agents
        # for one that both (a) previously appeared on this issue and
        # (b) is NOT the current reviewer (agy) or the terminal (fred)
        origin_agent = None
        for agent_name in AGENT_CONFIG:
            label = f"agent:{agent_name}"
            if label in ("agent:agy", "agent:fred", "agent:done"):
                continue
            if dedup.had_label(issue_id, label):
                # Also require that agent:agy was in the history
                # (confirms this was a review, not a direct dispatch)
                if dedup.had_label(issue_id, "agent:agy"):
                    origin_agent = agent_name
                    break

        if not origin_agent:
            continue

        # Record snapshot for current labels
        dedup.snapshot_labels(issue_id, current_labels, cycle_id)

        # 4. Signal the origin agent
        provider = _get_signal_provider()
        ok = provider.send_work(
            target=origin_agent,
            issue_id=identifier,
            title=(
                f"Review complete: {issue.get('title', identifier)}"
            ),
            priority=2,  # High priority — origin should act on this
            signal_type="review_complete",
            origin_agent=origin_agent,
        )
        if ok:
            dedup.mark_processed(issue_id, dedup_key, cycle_id)
            signalled += 1
            print(
                f"[dispatcher] 🔔 {origin_agent.capitalize()} signalled "
                f"(review complete): {identifier}"
            )
            # Post a brief comment
            try:
                add_comment(
                    issue_id,
                    f"🔔 **Review complete** — **{origin_agent}** has been "
                    f"notified to review the results.",
                )
            except Exception:
                pass

    return signalled


# ═══════════════════════════════════════════════════════════════
# Main Dispatch Loop
# ═══════════════════════════════════════════════════════════════


def dispatch_once(
    dedup: EventRouterDedup,
    pipelines: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Run a single dispatch cycle.

    Process flow:
      1. Discover new pipeline issues (``setup_pipeline_issues``).
      2. For each configured agent, find issues with ``agent::<name>``
         label that haven't been dispatched this cycle.
      3. Dispatch each issue to its agent's launch function.
      4. Clean up stale AGY processes.
      5. Recover stalled AGY tasks (after N cycles).

    Args:
        dedup: Deduplication tracker instance.
        pipelines: Pre-loaded pipeline templates (loaded automatically
            if not provided).

    Returns:
        Dict with counts: ``dispatched``, ``pipeline_setup``,
        ``stale_killed``, ``errors``.
    """
    counts: dict[str, int] = {
        "dispatched": 0,
        "pipeline_setup": 0,
        "stale_killed": 0,
        "errors": 0,
    }
    cycle_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    if pipelines is None:
        try:
            pipelines = load_pipeline_templates()
        except (FileNotFoundError, ValueError):
            pipelines = {"pipelines": {}}

    # 1. Set up new pipeline issues
    try:
        setup_issues = setup_pipeline_issues()
        counts["pipeline_setup"] = len(setup_issues)
    except Exception as exc:
        print(f"[dispatcher] setup_pipeline_issues error: {exc}")
        counts["errors"] += 1

    # 2. Dispatch to each agent
    for agent_name, config in AGENT_CONFIG.items():
        label = f"agent::{agent_name}"
        try:
            issues = get_issues_with_label(label)
        except Exception as exc:
            print(f"[dispatcher] Error fetching issues for {label}: {exc}")
            counts["errors"] += 1
            continue

        for issue in issues:
            issue_id = issue["id"]

            # Skip if already dispatched this cycle
            if dedup.is_processed(issue_id, label, cycle_id):
                continue

            launcher = AGENT_LAUNCHERS.get(agent_name)
            if not launcher:
                continue

            # ── Credit policy enforcement ───────────────────────────
            label = f"agent:{agent_name}" if not agent_name.startswith("agent:") else agent_name
            decision = evaluate_agent_launch(
                label, issue_id, operation="code_generation"
            )
            if decision.action == PolicyAction.DENY:
                identifier = issue.get("identifier", issue_id)
                print(
                    f"[dispatcher] 🚫 BLOCKED {agent_name} → {identifier}: "
                    f"{decision.reason}"
                )
                try:
                    add_comment(
                        issue_id,
                        f"🚫 **Credit policy blocked**: {decision.reason}\n"
                        f"Estimated cost: {decision.estimated_cost} credits.",
                    )
                except Exception:
                    pass
                # Log to metrics
                log_completed_pipeline_metrics(
                    issue_id=issue_id,
                    agent=agent_name,
                    status="blocked",
                    reason=decision.reason,
                    cost=decision.estimated_cost,
                )
                counts["blocked"] = counts.get("blocked", 0) + 1
                dedup.mark_processed(issue_id, label, cycle_id)
                continue
            elif decision.action == PolicyAction.WARN:
                identifier = issue.get("identifier", issue_id)
                print(
                    f"[dispatcher] ⚠️  WARN {agent_name} → {identifier}: "
                    f"{decision.reason}"
                )
            elif decision.action == PolicyAction.ASK_USER:
                # Headless dispatcher cannot ask user — log and skip
                identifier = issue.get("identifier", issue_id)
                print(
                    f"[dispatcher] ❓ ASK_USER {agent_name} → {identifier}: "
                    f"{decision.reason}"
                )
                counts["pending_approval"] = counts.get("pending_approval", 0) + 1
                dedup.mark_processed(issue_id, label, cycle_id)
                continue
            # ── End credit policy ──────────────────────────────────

            try:
                result = launcher(issue_id, title=issue.get("title", ""))
                if result:
                    dedup.mark_processed(issue_id, label, cycle_id)
                    counts["dispatched"] += 1
                    agent_name_pretty = agent_name.capitalize()
                    identifier = issue.get("identifier", issue_id)
                    print(
                        f"[dispatcher] 🚀 Dispatched {agent_name_pretty} "
                        f"→ {identifier}: {issue.get('title', '')}"
                    )
                    # ── Telemetry: record agent run ──────────────────
                    run_id = f"{cycle_id}-{agent_name}-{identifier}"
                    collector = get_collector()
                    collector.record_agent_run(
                        run_id=run_id,
                        agent=agent_name,
                        issue_id=identifier,
                        provider=AGENT_PROVIDER_MAP.get(agent_name, ""),
                        status="dispatched",
                        credits_spent=decision.estimated_cost,
                    )
                    # ── End telemetry ──────────────────────────────────
                    # Post a comment tracking the dispatch
                    try:
                        add_comment(
                            issue_id,
                            f"🤖 **{agent_name_pretty}** picked up this issue "
                            f"(cycle {cycle_id})",
                        )
                    except Exception:
                        pass  # Non-critical
            except Exception as exc:
                print(
                    f"[dispatcher] Error dispatching {agent_name} "
                    f"→ {issue.get('identifier', issue_id)}: {exc}"
                )
                counts["errors"] += 1

    # 3. Clean up stale AGY processes
    try:
        killed = cleanup_stale_agy(max_age_minutes=5)
        counts["stale_killed"] = killed
    except Exception as exc:
        print(f"[dispatcher] cleanup_stale_agy error: {exc}")
        counts["errors"] += 1

    # 4. Recover stalled AGY (after enough cycles)
    try:
        recover_stalled_agy(max_retries=MAX_CYCLES_BEFORE_RECOVER)
    except Exception as exc:
        print(f"[dispatcher] recover_stalled_agy error: {exc}")
        counts["errors"] += 1

    # 5. Detect origin completions — signal origin agents when reviews finish
    try:
        origin_count = detect_origin_completions(dedup, cycle_id)
        if origin_count:
            print(
                f"[dispatcher] 🔔 Signaled {origin_count} "
                f"origin agent(s) for review completion"
            )
            counts["dispatched"] += origin_count
    except Exception as exc:
        print(f"[dispatcher] detect_origin_completions error: {exc}")
        counts["errors"] += 1

    return counts


def main_loop(
    interval: int = POLL_INTERVAL,
    once: bool = False,
) -> None:
    """Run the dispatcher event loop.

    Args:
        interval: Seconds between dispatch cycles.
        once: If ``True``, run a single cycle and exit.
    """
    print(f"[dispatcher] Prismatic Engine v{__import__('prismatic').__version__}")
    print(f"[dispatcher] TEAM_ID={TEAM_ID}")
    print(f"[dispatcher] Poll interval={interval}s")
    print(f"[dispatcher] State DB={DEFAULT_DB_PATH}")
    print()

    dedup = EventRouterDedup()
    collector = get_collector()
    print(f"[dispatcher] Telemetry collector active → {DEFAULT_DB_PATH}")

    try:
        pipelines = load_pipeline_templates()
        pipeline_count = len(pipelines.get("pipelines", {}))
        print(f"[dispatcher] Loaded {pipeline_count} pipeline template(s)")
    except FileNotFoundError:
        print("[dispatcher] No pipeline config found — running in ad-hoc mode")
        pipelines = {"pipelines": {}}
    except Exception as exc:
        print(f"[dispatcher] Warning: could not load pipelines: {exc}")
        pipelines = {"pipelines": {}}

    cycle = 0
    while True:
        cycle += 1
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"\n{'='*60}")
        print(f"[dispatcher] Cycle {cycle} — {now}")
        print(f"{'='*60}")

        try:
            counts = dispatch_once(dedup, pipelines)
            print(
                f"[dispatcher] Cycle {cycle} summary: "
                f"{counts['dispatched']} dispatched, "
                f"{counts['pipeline_setup']} pipeline setups, "
                f"{counts['stale_killed']} stale killed, "
                f"{counts['errors']} errors"
            )
            # ── Telemetry: log cycle metrics ─────────────────────
            if counts.get("dispatched", 0) > 0:
                dashboard = collector.get_dashboard_data(hours=1)
                loops = sum(r.get("cnt", 0) for r in dashboard.get("loops", []))
                tripped = dashboard.get("breakers_tripped", 0)
                if loops or tripped:
                    print(
                        f"[telemetry] Last hour: {loops} loop events, "
                        f"{tripped} breaker(s) tripped"
                    )
            # ── End telemetry ─────────────────────────────────────
        except KeyboardInterrupt:
            print("\n[dispatcher] Interrupted — shutting down")
            break
        except Exception as exc:
            print(f"[dispatcher] Fatal error in cycle {cycle}: {exc}")
            counts = {"dispatched": 0, "pipeline_setup": 0,
                       "stale_killed": 0, "errors": 1}

        if once:
            break

        time.sleep(interval)

    dedup.close()
    print("[dispatcher] Shutdown complete")


def init_config(force: bool = False) -> None:
    """Initialize default configuration files in the user's config directory."""
    config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "prismatic"
    config_dir.mkdir(parents=True, exist_ok=True)

    template_dir = Path(__file__).parent / "templates" / "config"
    if not template_dir.is_dir():
        print(f"[dispatcher] Error: Template directory not found at {template_dir}")
        return

    copied = 0
    skipped = 0
    for template_file in template_dir.glob("*.yaml"):
        target_file = config_dir / template_file.name
        if target_file.exists() and not force:
            print(f"[dispatcher] Skipping existing config: {target_file}")
            skipped += 1
            continue
        
        try:
            import shutil
            shutil.copy2(template_file, target_file)
            print(f"[dispatcher] Initialized {target_file}")
            copied += 1
        except OSError as exc:
            print(f"[dispatcher] Error copying {template_file.name}: {exc}")

    print(f"\n[dispatcher] Initialization complete: {copied} copied, {skipped} skipped.")
    print(f"[dispatcher] Config directory: {config_dir}")


def main() -> None:
    """Entry point: parse CLI arguments and start the dispatcher.

    Supports:
        ``serve``     Start the dispatcher event loop.
        ``init``      Initialize default configuration files.
        ``skills``    Skill marketplace subcommands.
        ``--help``    Show usage.

    Legacy Support (for backward compatibility):
        ``--once``, ``--interval``, ``--setup-pipelines`` work as before.
    """
    import argparse

    # ── Legacy support: Rewrite sys.argv ─────────────────────────
    # If the first argument is a legacy flag, insert 'serve' before it.
    legacy_flags = {"--once", "--interval", "--setup-pipelines"}
    if len(sys.argv) > 1 and sys.argv[1] in legacy_flags:
        sys.argv.insert(1, "serve")

    parser = argparse.ArgumentParser(
        description="Prismatic Engine — agent orchestration dispatcher",
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to run")

    # ── Serve Subcommand ──────────────────────────────────────
    serve_parser = subparsers.add_parser("serve", help="Start the dispatcher event loop")
    serve_parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single dispatch cycle and exit",
    )
    serve_parser.add_argument(
        "--interval",
        type=int,
        default=POLL_INTERVAL,
        help=f"Poll interval in seconds (default: {POLL_INTERVAL})",
    )
    serve_parser.add_argument(
        "--setup-pipelines",
        action="store_true",
        help="Run pipeline setup on all matching issues, then exit",
    )

    # ── Init Subcommand ───────────────────────────────────────
    init_parser = subparsers.add_parser("init", help="Initialize default configuration files")
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing configuration files",
    )

    # ── Skills Subcommand ─────────────────────────────────────
    subparsers.add_parser("skills", help="Skill marketplace subcommands (run 'skills --help' for details)")

    # ── Help / No Command ─────────────────────────────────────
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    # Handle 'skills' early to delegate to skills.py
    if sys.argv[1] == "skills":
        from .skills import cli_skills
        sys.exit(cli_skills(sys.argv[2:]))

    args = parser.parse_args()

    if args.command == "init":
        init_config(force=args.force)
    elif args.command == "serve":
        if args.setup_pipelines:
            issues = setup_pipeline_issues()
            print(f"Set up {len(issues)} pipeline issues")
            return
        main_loop(interval=args.interval, once=args.once)
    else:
        # Default fallback
        if args.command is None:
            print("Please specify a command: serve, init, or skills.")
            parser.print_help()


# ═══════════════════════════════════════════════════════════════
# Hook for ``python -m prismatic.dispatcher``
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    main()
