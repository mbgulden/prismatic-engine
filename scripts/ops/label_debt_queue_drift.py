#!/usr/bin/env python3
"""Measure Linear label debt and stale queue drift for Prismatic Engine.

The report is intentionally small and repeatable: it can run as a cron smoke,
write JSON/Markdown for humans, and append snapshots to a JSONL history file so
queue drift is visible between runs instead of guessed from anecdotes.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ACTIVE_AGENT_LABEL_PREFIX = "agent:"
TERMINAL_STATE_TYPES = {"completed", "canceled", "triage"}
TERMINAL_STATE_NAMES = {"done", "canceled", "cancelled", "duplicate"}
DISPATCHABLE_STATE_TYPES = {"backlog", "unstarted", "started"}
DISPATCHABLE_STATE_NAMES = {"backlog", "todo", "in progress"}
BLOCKING_LABELS = {"blocked", "agent:needs-human-review", "agent:pending-human"}
DEFAULT_STALE_DAYS = 7
DEFAULT_HISTORY_PATH = (
    Path(os.environ.get("PRISMATIC_STATE_DIR", "/tmp/prismatic_state"))
    / "label_debt_queue_drift.jsonl"
)


@dataclass(frozen=True)
class IssueSummary:
    identifier: str
    title: str
    state_name: str
    state_type: str
    updated_at: datetime
    labels: tuple[str, ...]

    @classmethod
    def from_linear(cls, node: dict[str, Any]) -> IssueSummary:
        state = node.get("state") or {}
        labels = tuple(
            sorted(
                (label.get("name") or "")
                for label in (node.get("labels") or {}).get("nodes", [])
            )
        )
        return cls(
            identifier=node.get("identifier") or node.get("id") or "UNKNOWN",
            title=node.get("title") or "",
            state_name=state.get("name") or "Unknown",
            state_type=state.get("type") or "unknown",
            updated_at=parse_linear_datetime(node.get("updatedAt")),
            labels=labels,
        )


def parse_linear_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_api_key() -> str:
    token = os.environ.get("LINEAR_API_KEY")
    if token:
        return token
    candidate_envs = [
        Path.home() / ".hermes/profiles/ned/.env",
        Path.home() / ".hermes/profiles/orchestrator/.env",
        Path("/home/ubuntu/.hermes/profiles/ned/.env"),
        Path("/home/ubuntu/.hermes/profiles/orchestrator/.env"),
        Path("/home/ubuntu/work/prismatic-engine/.env"),
    ]
    for env_path in candidate_envs:
        if not env_path.exists():
            continue
        for line in env_path.read_text(errors="ignore").splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == "LINEAR_API_KEY":
                token = value.strip().strip('"').strip("'")
                if token:
                    return token
    raise RuntimeError("LINEAR_API_KEY not found in environment or known .env files")


def linear_graphql(
    query: str, variables: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.linear.app/graphql",
        data=payload,
        headers={
            "Authorization": load_api_key(),
            "Content-Type": "application/json",
            "User-Agent": "prismatic-label-debt-queue-drift/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Linear API HTTP {exc.code}: {body[:500]}") from exc
    if data.get("errors"):
        raise RuntimeError(f"Linear API errors: {data['errors']}")
    return data


def fetch_open_issues(page_size: int = 100) -> list[IssueSummary]:
    """Fetch open issues with the fields needed for debt/drift scoring."""
    query = """
    query($after: String, $first: Int!) {
      issues(
        first: $first,
        after: $after,
        filter: { state: { type: { nin: [\"completed\", \"canceled\"] } } }
      ) {
        nodes {
          id
          identifier
          title
          updatedAt
          state { name type }
          labels { nodes { name } }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
    """
    issues: list[IssueSummary] = []
    after = None
    while True:
        data = linear_graphql(query, {"after": after, "first": page_size})
        connection = data["data"]["issues"]
        issues.extend(IssueSummary.from_linear(node) for node in connection["nodes"])
        page_info = connection["pageInfo"]
        if not page_info["hasNextPage"]:
            return issues
        after = page_info["endCursor"]


def agent_labels(issue: IssueSummary) -> list[str]:
    return [
        label
        for label in issue.labels
        if label.startswith(ACTIVE_AGENT_LABEL_PREFIX) and label != "agent:done"
    ]


def is_terminal(issue: IssueSummary) -> bool:
    return (
        issue.state_type.lower() in TERMINAL_STATE_TYPES
        or issue.state_name.lower() in TERMINAL_STATE_NAMES
    )


def is_dispatchable_state(issue: IssueSummary) -> bool:
    return (
        issue.state_type.lower() in DISPATCHABLE_STATE_TYPES
        or issue.state_name.lower() in DISPATCHABLE_STATE_NAMES
    )


def is_blocked(issue: IssueSummary) -> bool:
    lower_labels = {label.lower() for label in issue.labels}
    return bool(lower_labels & BLOCKING_LABELS) or "blocked" in issue.state_name.lower()


def debt_reasons(issue: IssueSummary, now: datetime, stale_days: int) -> list[str]:
    labels = set(issue.labels)
    agents = agent_labels(issue)
    reasons: list[str] = []

    if len(agents) > 1:
        reasons.append("multiple_agent_labels")
    if "dispatch:ready" in labels and not agents:
        reasons.append("dispatch_ready_without_agent")
    if agents and is_terminal(issue):
        reasons.append("agent_label_on_terminal_state")
    if "agent:done" in labels and not (
        issue.state_name.lower() == "done" or issue.state_type.lower() == "completed"
    ):
        reasons.append("agent_done_on_non_done_state")
    if is_dispatchable_state(issue) and not agents and "dispatch:ready" not in labels:
        reasons.append("dispatchable_without_agent_or_ready")
    age_days = (now - issue.updated_at).total_seconds() / 86400
    if is_dispatchable_state(issue) and age_days >= stale_days:
        reasons.append("stale_dispatchable")
    if is_blocked(issue):
        reasons.append("blocked_from_dispatch")
    return reasons


def summarize(
    issues: Iterable[IssueSummary],
    *,
    now: datetime | None = None,
    stale_days: int = DEFAULT_STALE_DAYS,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    issue_list = list(issues)
    debt_counter: Counter[str] = Counter()
    state_counter: Counter[str] = Counter()
    agent_counter: Counter[str] = Counter()
    debt_examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    stale_ages: list[float] = []

    for issue in issue_list:
        state_counter[f"{issue.state_name} ({issue.state_type})"] += 1
        for label in agent_labels(issue):
            agent_counter[label] += 1
        reasons = debt_reasons(issue, now, stale_days)
        age_days = round((now - issue.updated_at).total_seconds() / 86400, 2)
        if "stale_dispatchable" in reasons:
            stale_ages.append(age_days)
        for reason in reasons:
            debt_counter[reason] += 1
            if len(debt_examples[reason]) < 10:
                debt_examples[reason].append(
                    {
                        "identifier": issue.identifier,
                        "state": issue.state_name,
                        "age_days": age_days,
                        "labels": list(issue.labels),
                        "title": issue.title,
                    }
                )

    dispatchable_count = sum(1 for issue in issue_list if is_dispatchable_state(issue))
    blocked_count = debt_counter["blocked_from_dispatch"]
    stale_count = debt_counter["stale_dispatchable"]
    debt_total = sum(1 for issue in issue_list if debt_reasons(issue, now, stale_days))
    max_stale_age = max(stale_ages, default=0.0)

    return {
        "generated_at": now.isoformat(),
        "stale_days": stale_days,
        "totals": {
            "open_issues_scanned": len(issue_list),
            "dispatchable_issues": dispatchable_count,
            "issues_with_label_debt": debt_total,
            "stale_dispatchable_issues": stale_count,
            "blocked_from_dispatch": blocked_count,
            "max_stale_age_days": max_stale_age,
        },
        "label_debt_by_reason": dict(sorted(debt_counter.items())),
        "open_by_state": dict(sorted(state_counter.items())),
        "open_by_agent_label": dict(sorted(agent_counter.items())),
        "examples": dict(debt_examples),
    }


def read_last_snapshot(history_path: Path) -> dict[str, Any] | None:
    if not history_path.exists():
        return None
    last = None
    for line in history_path.read_text(errors="ignore").splitlines():
        if line.strip():
            last = line
    if not last:
        return None
    try:
        return json.loads(last)
    except json.JSONDecodeError:
        return None


def add_drift(
    summary: dict[str, Any], previous: dict[str, Any] | None
) -> dict[str, Any]:
    if not previous:
        summary["drift"] = {"baseline": "none", "deltas": {}}
        return summary
    deltas = {}
    for key, value in summary["totals"].items():
        old = previous.get("totals", {}).get(key)
        if isinstance(value, (int, float)) and isinstance(old, (int, float)):
            deltas[key] = round(value - old, 2)
    summary["drift"] = {
        "baseline": previous.get("generated_at", "unknown"),
        "deltas": deltas,
    }
    return summary


def append_history(history_path: Path, summary: dict[str, Any]) -> None:
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(summary, sort_keys=True) + "\n")


def render_markdown(summary: dict[str, Any]) -> str:
    totals = summary["totals"]
    lines = [
        "# Label debt and stale queue drift",
        "",
        f"Generated: `{summary['generated_at']}`",
        f"Stale threshold: `{summary['stale_days']} days`",
        "",
        "## Headline",
        "",
        f"- Open issues scanned: **{totals['open_issues_scanned']}**",
        f"- Dispatchable issues: **{totals['dispatchable_issues']}**",
        f"- Issues with label debt: **{totals['issues_with_label_debt']}**",
        f"- Stale dispatchable issues: **{totals['stale_dispatchable_issues']}**",
        f"- Blocked from dispatch: **{totals['blocked_from_dispatch']}**",
        f"- Max stale age: **{totals['max_stale_age_days']} days**",
        "",
        "## Drift since previous snapshot",
        "",
    ]
    drift = summary.get("drift", {})
    lines.append(f"Baseline: `{drift.get('baseline', 'none')}`")
    deltas = drift.get("deltas") or {}
    if deltas:
        for key, delta in sorted(deltas.items()):
            sign = "+" if delta > 0 else ""
            lines.append(f"- `{key}`: {sign}{delta}")
    else:
        lines.append("- No previous snapshot found; this run establishes the baseline.")

    lines.extend(["", "## Label debt by reason", ""])
    for reason, count in summary.get("label_debt_by_reason", {}).items():
        lines.append(f"- `{reason}`: **{count}**")
    if not summary.get("label_debt_by_reason"):
        lines.append("- No label debt detected.")

    lines.extend(["", "## Open issues by state", ""])
    for state, count in summary.get("open_by_state", {}).items():
        lines.append(f"- `{state}`: {count}")

    lines.extend(["", "## Open issues by agent label", ""])
    for label, count in summary.get("open_by_agent_label", {}).items():
        lines.append(f"- `{label}`: {count}")
    if not summary.get("open_by_agent_label"):
        lines.append("- No active agent labels found.")

    lines.extend(["", "## Examples", ""])
    for reason, examples in summary.get("examples", {}).items():
        lines.append(f"### `{reason}`")
        for example in examples[:5]:
            labels = ", ".join(example["labels"])
            lines.append(
                f"- {example['identifier']} — {example['state']}, {example['age_days']}d old — {example['title']} (`{labels}`)"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def load_fixture(path: Path) -> list[IssueSummary]:
    data = json.loads(path.read_text())
    nodes = data.get("issues", data) if isinstance(data, dict) else data
    return [IssueSummary.from_linear(node) for node in nodes]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON instead of Markdown"
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        help="Read issue nodes from a fixture JSON file instead of Linear",
    )
    parser.add_argument(
        "--stale-days",
        type=int,
        default=DEFAULT_STALE_DAYS,
        help="Age threshold for stale dispatchable issues",
    )
    parser.add_argument(
        "--history-path",
        type=Path,
        default=DEFAULT_HISTORY_PATH,
        help="JSONL snapshot history path",
    )
    parser.add_argument(
        "--append-history",
        action="store_true",
        help="Append this run to the history file",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    issues = load_fixture(args.fixture) if args.fixture else fetch_open_issues()
    previous = read_last_snapshot(args.history_path)
    summary = add_drift(summarize(issues, stale_days=args.stale_days), previous)
    if args.append_history:
        append_history(args.history_path, summary)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(render_markdown(summary), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
