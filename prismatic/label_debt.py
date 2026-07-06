"""Linear label-debt and queue-hygiene scoring for Prismatic Engine.

The scanner queue can look busy while very little work is actually launchable:
issues may have an agent label but no ``dispatch:ready`` label, completed issues may
still carry dispatch labels, and stale review/duplicate noise can sit in the same
pool as production work.  This module turns a normalized issue list into a
repeatable scorecard and concrete cleanup recommendations.

Input shape is deliberately small so it works with Linear GraphQL responses,
fixtures, and cron pre-run captures::

    {
      "identifier": "GRO-123",
      "title": "Fix dispatcher",
      "description": "...",
      "state": {"name": "Todo", "type": "unstarted"},
      "labels": {"nodes": [{"name": "agent:ned"}, {"name": "dispatch:ready"}]}
    }

Run as a CLI with ``python3 -m prismatic.label_debt issues.json`` or pipe JSON on
stdin.  Output is JSON so dashboards and cron jobs can ingest it without scraping
human prose.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

AGENT_LABEL_PREFIX = "agent:"
DISPATCH_READY = "dispatch:ready"
DISPATCH_LABEL_PREFIX = "dispatch:"
QUARANTINE_LABEL = "queue:quarantine"
REQUIRES_TRIAGE = "requires:triage"

COMPLETED_STATE_TYPES = {"completed", "canceled", "cancelled"}
COMPLETED_STATE_NAMES = {"done", "canceled", "cancelled", "duplicate", "archived"}
REVIEW_STATE_NAMES = {"in review", "review", "needs review"}

DUPLICATE_NEEDLES = ("duplicate", "dupe", "stale clone", "clone of", "superseded by")
REVIEW_NOISE_NEEDLES = ("review noise", "stale review", "post-publish audit", "agent:ned-review")
ARCHIVE_NEEDLES = ("archived", "archive leftover", "legacy leftover")
BLOCKED_NEEDLES = ("blocked", "waiting on", "needs human", "credential", "manual")


@dataclass(frozen=True)
class IssueSnapshot:
    """Small normalized view of a Linear issue used by the hygiene scorer."""

    identifier: str
    title: str = ""
    description: str = ""
    state_name: str = ""
    state_type: str = ""
    labels: frozenset[str] = field(default_factory=frozenset)
    archived: bool = False

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "IssueSnapshot":
        labels = _extract_label_names(raw.get("labels", []))
        state = raw.get("state") or {}
        if isinstance(state, str):
            state_name = state
            state_type = ""
        else:
            state_name = str(state.get("name") or "")
            state_type = str(state.get("type") or "")
        return cls(
            identifier=str(raw.get("identifier") or raw.get("id") or ""),
            title=str(raw.get("title") or ""),
            description=str(raw.get("description") or ""),
            state_name=state_name,
            state_type=state_type,
            labels=frozenset(labels),
            archived=bool(raw.get("archivedAt") or raw.get("archived")),
        )

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.description}".lower()

    @property
    def agent_labels(self) -> tuple[str, ...]:
        return tuple(sorted(label for label in self.labels if label.startswith(AGENT_LABEL_PREFIX)))

    @property
    def has_dispatch_ready(self) -> bool:
        return DISPATCH_READY in self.labels

    @property
    def has_dispatch_label(self) -> bool:
        return any(label.startswith(DISPATCH_LABEL_PREFIX) for label in self.labels)

    @property
    def is_completed(self) -> bool:
        return (
            self.state_type.lower() in COMPLETED_STATE_TYPES
            or self.state_name.lower() in COMPLETED_STATE_NAMES
        )

    @property
    def is_review_state(self) -> bool:
        return self.state_name.lower() in REVIEW_STATE_NAMES

    @property
    def is_duplicate_or_clone(self) -> bool:
        return _contains_any(self.text, DUPLICATE_NEEDLES) or any(
            label in {"duplicate", "stale-clone", "stale clone"} for label in self.labels
        )

    @property
    def is_review_noise(self) -> bool:
        return (
            self.is_review_state
            or "agent:ned-review" in self.labels
            or _contains_any(self.text, REVIEW_NOISE_NEEDLES)
        )

    @property
    def is_archived_leftover(self) -> bool:
        return self.archived or _contains_any(self.text, ARCHIVE_NEEDLES) or "archived" in self.labels

    @property
    def is_blocked(self) -> bool:
        return _contains_any(self.text, BLOCKED_NEEDLES) or any(
            label in {"blocked", "requires:human", "requires:credential", REQUIRES_TRIAGE}
            for label in self.labels
        )

    @property
    def is_labeled_work(self) -> bool:
        return bool(self.agent_labels)

    @property
    def is_launchable(self) -> bool:
        return (
            self.is_labeled_work
            and self.has_dispatch_ready
            and not self.is_completed
            and not self.is_duplicate_or_clone
            and not self.is_review_noise
            and not self.is_archived_leftover
            and not self.is_blocked
        )


@dataclass(frozen=True)
class HygieneAction:
    identifier: str
    action: str
    reason: str
    add_labels: tuple[str, ...] = ()
    remove_labels: tuple[str, ...] = ()
    target_state: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "action": self.action,
            "reason": self.reason,
            "add_labels": list(self.add_labels),
            "remove_labels": list(self.remove_labels),
            "target_state": self.target_state,
        }


def analyze_issues(raw_issues: Iterable[Mapping[str, Any] | IssueSnapshot]) -> dict[str, Any]:
    """Return queue-hygiene metrics and deterministic cleanup actions.

    The function is side-effect free by design.  A caller may apply the returned
    actions through Linear after review, but the analyzer itself only measures
    and classifies.
    """

    issues = [issue if isinstance(issue, IssueSnapshot) else IssueSnapshot.from_mapping(issue) for issue in raw_issues]
    actions = [_recommend_action(issue) for issue in issues]
    actions = [action for action in actions if action is not None]

    labeled = [issue for issue in issues if issue.is_labeled_work]
    launchable = [issue for issue in issues if issue.is_launchable]
    stale_noise = [
        issue
        for issue in issues
        if issue.is_duplicate_or_clone or issue.is_review_noise or issue.is_archived_leftover
    ]
    backfill = [action for action in actions if action.action == "backfill_dispatch_ready"]
    quarantine = [action for action in actions if action.action == "quarantine_noise"]
    cancel = [action for action in actions if action.action == "cancel_duplicate_or_stale_clone"]
    park = [action for action in actions if action.action == "park_archived_leftover"]

    labeled_count = len(labeled)
    launchable_count = len(launchable)
    gap_count = max(labeled_count - launchable_count, 0)
    launchable_ratio = (launchable_count / labeled_count) if labeled_count else 1.0

    return {
        "summary": {
            "total_issues": len(issues),
            "labeled_work": labeled_count,
            "launchable_work": launchable_count,
            "label_to_launchable_gap": gap_count,
            "launchable_ratio": round(launchable_ratio, 4),
            "stale_noise": len(stale_noise),
            "needs_dispatch_ready_backfill": len(backfill),
            "duplicates_or_stale_clones": len(cancel),
            "review_noise_to_quarantine": len(quarantine),
            "archived_leftovers_to_park": len(park),
        },
        "launchable_identifiers": [issue.identifier for issue in launchable],
        "actions": [action.as_dict() for action in actions],
    }


def _recommend_action(issue: IssueSnapshot) -> HygieneAction | None:
    if not issue.is_labeled_work:
        return None

    dispatch_labels = tuple(sorted(label for label in issue.labels if label.startswith(DISPATCH_LABEL_PREFIX)))

    if issue.is_duplicate_or_clone:
        return HygieneAction(
            identifier=issue.identifier,
            action="cancel_duplicate_or_stale_clone",
            reason="Duplicate/stale-clone issue should not be launchable queue work.",
            remove_labels=dispatch_labels,
            target_state="Canceled",
        )

    if issue.is_archived_leftover or issue.is_completed:
        return HygieneAction(
            identifier=issue.identifier,
            action="park_archived_leftover",
            reason="Completed or archived leftover still has agent/dispatch labels.",
            remove_labels=dispatch_labels,
            add_labels=(QUARANTINE_LABEL,),
        )

    if issue.is_review_noise:
        return HygieneAction(
            identifier=issue.identifier,
            action="quarantine_noise",
            reason="Review noise should be parked outside the production dispatch queue.",
            remove_labels=(DISPATCH_READY,) if issue.has_dispatch_ready else (),
            add_labels=(QUARANTINE_LABEL,),
        )

    if issue.is_blocked:
        return HygieneAction(
            identifier=issue.identifier,
            action="mark_requires_triage",
            reason="Blocked work should not silently distort launchable capacity.",
            remove_labels=(DISPATCH_READY,) if issue.has_dispatch_ready else (),
            add_labels=(REQUIRES_TRIAGE,),
        )

    if not issue.has_dispatch_ready:
        return HygieneAction(
            identifier=issue.identifier,
            action="backfill_dispatch_ready",
            reason="Legitimate active agent-labeled work is missing dispatch:ready.",
            add_labels=(DISPATCH_READY,),
        )

    return None


def _extract_label_names(raw_labels: Any) -> set[str]:
    if isinstance(raw_labels, Mapping):
        raw_labels = raw_labels.get("nodes", [])
    names: set[str] = set()
    for label in raw_labels or []:
        if isinstance(label, str):
            names.add(label)
        elif isinstance(label, Mapping) and label.get("name"):
            names.add(str(label["name"]))
    return names


def _contains_any(haystack: str, needles: Sequence[str]) -> bool:
    return any(needle in haystack for needle in needles)


def _load_issues(path: str | None) -> list[Mapping[str, Any]]:
    raw = sys.stdin.read() if path in (None, "-") else open(path, "r", encoding="utf-8").read()
    data = json.loads(raw)
    if isinstance(data, Mapping):
        if "issues" in data:
            data = data["issues"]
        elif "nodes" in data:
            data = data["nodes"]
        elif data.get("data", {}).get("issues", {}).get("nodes") is not None:
            data = data["data"]["issues"]["nodes"]
    if not isinstance(data, list):
        raise SystemExit("expected a JSON list of issues or an object containing issues/nodes")
    return data


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure Prismatic Linear label debt and queue hygiene")
    parser.add_argument("issues_json", nargs="?", default="-", help="JSON file containing issues; defaults to stdin")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args(argv)

    result = analyze_issues(_load_issues(args.issues_json))
    print(json.dumps(result, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
