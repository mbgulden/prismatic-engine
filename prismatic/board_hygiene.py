"""Board hygiene worker for suppressing stale/noise Linear board churn.

The worker is deliberately provider-light: callers pass Linear-like issue dicts or
``IssueSnapshot`` instances, and the worker returns an explainable delta.  It can
persist the last actionable board fingerprint to JSON so recurring pollers only
emit when actionable work actually changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping


COMPLETED_STATE_TYPES = frozenset({"completed", "canceled", "cancelled"})
COMPLETED_STATE_NAMES = frozenset({"done", "completed", "cancelled", "canceled"})
DEFAULT_STALE_AFTER_DAYS = 14


@dataclass(frozen=True)
class IssueSnapshot:
    """Normalized subset of a board issue needed by the hygiene pass."""

    identifier: str
    title: str = ""
    state_name: str = ""
    state_type: str = ""
    labels: tuple[str, ...] = ()
    updated_at: datetime | None = None
    url: str = ""

    @classmethod
    def from_issue(cls, issue: Mapping[str, Any]) -> "IssueSnapshot":
        """Normalize a Linear-like issue mapping.

        Accepts labels as strings, ``{"name": ...}`` dicts, or Linear's
        ``{"nodes": [...]}`` connection shape.  Accepts state as a string or
        ``{"name": ..., "type": ...}`` dict.
        """

        state = issue.get("state") or {}
        if isinstance(state, Mapping):
            state_name = str(state.get("name") or "")
            state_type = str(state.get("type") or "")
        else:
            state_name = str(state)
            state_type = ""

        return cls(
            identifier=str(issue.get("identifier") or issue.get("id") or ""),
            title=str(issue.get("title") or ""),
            state_name=state_name,
            state_type=state_type,
            labels=tuple(normalize_labels(issue.get("labels", ()))),
            updated_at=parse_datetime(
                issue.get("updatedAt") or issue.get("updated_at")
            ),
            url=str(issue.get("url") or ""),
        )


@dataclass(frozen=True)
class BoardHygieneConfig:
    """Tuning knobs for board hygiene classification."""

    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS
    now: datetime | None = None
    stale_labels: frozenset[str] = frozenset({"stale", "noise:stale", "curator:stale"})
    duplicate_labels: frozenset[str] = frozenset({"duplicate", "noise:duplicate"})
    umbrella_labels: frozenset[str] = frozenset(
        {"epic", "umbrella", "noise:umbrella", "project:umbrella"}
    )
    actionable_label_prefixes: tuple[str, ...] = ("agent:",)
    actionable_labels: frozenset[str] = frozenset(
        {"dispatch:ready", "dispatch:priority"}
    )

    @property
    def effective_now(self) -> datetime:
        if self.now is None:
            return datetime.now(UTC)
        if self.now.tzinfo is None:
            return self.now.replace(tzinfo=UTC)
        return self.now


@dataclass(frozen=True)
class BoardDelta:
    """Actionable board changes since the previous worker run."""

    new_actionable: tuple[IssueSnapshot, ...] = ()
    changed_actionable: tuple[IssueSnapshot, ...] = ()
    resolved_actionable: tuple[str, ...] = ()

    @property
    def should_emit(self) -> bool:
        return bool(
            self.new_actionable or self.changed_actionable or self.resolved_actionable
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "should_emit": self.should_emit,
            "new_actionable": [issue.identifier for issue in self.new_actionable],
            "changed_actionable": [
                issue.identifier for issue in self.changed_actionable
            ],
            "resolved_actionable": list(self.resolved_actionable),
        }


@dataclass(frozen=True)
class HygieneResult:
    """Full hygiene pass result with emitted delta and suppressed-noise evidence."""

    delta: BoardDelta
    actionable: tuple[IssueSnapshot, ...]
    suppressed: Mapping[str, tuple[IssueSnapshot, ...]]
    fingerprints: Mapping[str, str]

    @property
    def should_emit(self) -> bool:
        return self.delta.should_emit

    def as_dict(self) -> dict[str, Any]:
        return {
            "delta": self.delta.as_dict(),
            "actionable": [issue.identifier for issue in self.actionable],
            "suppressed": {
                reason: [issue.identifier for issue in issues]
                for reason, issues in self.suppressed.items()
            },
            "fingerprints": dict(self.fingerprints),
        }


class BoardHygieneWorker:
    """Suppress recurring board noise and compute actionable-only deltas."""

    def __init__(
        self,
        state_path: str | Path | None = None,
        config: BoardHygieneConfig | None = None,
    ) -> None:
        self.state_path = Path(state_path) if state_path else None
        self.config = config or BoardHygieneConfig()

    def run(self, issues: Iterable[Mapping[str, Any] | IssueSnapshot]) -> HygieneResult:
        snapshots = tuple(
            issue
            if isinstance(issue, IssueSnapshot)
            else IssueSnapshot.from_issue(issue)
            for issue in issues
        )
        previous = self.load_state()
        result = analyze_board(snapshots, previous, self.config)
        self.save_state(result.fingerprints)
        return result

    def load_state(self) -> dict[str, str]:
        if self.state_path is None or not self.state_path.exists():
            return {}
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        fingerprints = raw.get("fingerprints", raw)
        if not isinstance(fingerprints, Mapping):
            return {}
        return {str(key): str(value) for key, value in fingerprints.items()}

    def save_state(self, fingerprints: Mapping[str, str]) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": datetime.now(UTC).isoformat(),
            "fingerprints": dict(sorted(fingerprints.items())),
        }
        self.state_path.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )


def analyze_board(
    issues: Iterable[IssueSnapshot],
    previous_fingerprints: Mapping[str, str] | None = None,
    config: BoardHygieneConfig | None = None,
) -> HygieneResult:
    """Classify a board snapshot and compute actionable deltas."""

    config = config or BoardHygieneConfig()
    previous = dict(previous_fingerprints or {})
    actionable: list[IssueSnapshot] = []
    suppressed: dict[str, list[IssueSnapshot]] = {}

    for issue in issues:
        reason = classify_noise(issue, config)
        if reason is None:
            actionable.append(issue)
        else:
            suppressed.setdefault(reason, []).append(issue)

    current = {issue.identifier: issue_fingerprint(issue) for issue in actionable}
    by_id = {issue.identifier: issue for issue in actionable}

    new = tuple(by_id[identifier] for identifier in sorted(current - previous.keys()))
    changed = tuple(
        by_id[identifier]
        for identifier in sorted(current.keys() & previous.keys())
        if current[identifier] != previous[identifier]
    )
    resolved = tuple(sorted(previous.keys() - current.keys()))

    return HygieneResult(
        delta=BoardDelta(new, changed, resolved),
        actionable=tuple(sorted(actionable, key=lambda issue: issue.identifier)),
        suppressed={
            reason: tuple(sorted(items, key=lambda issue: issue.identifier))
            for reason, items in sorted(suppressed.items())
        },
        fingerprints=current,
    )


def classify_noise(
    issue: IssueSnapshot, config: BoardHygieneConfig | None = None
) -> str | None:
    """Return the suppression reason for an issue, or ``None`` if actionable."""

    config = config or BoardHygieneConfig()
    labels = {label.lower() for label in issue.labels}
    title = issue.title.strip().lower()
    state_name = issue.state_name.strip().lower()
    state_type = issue.state_type.strip().lower()

    if state_type in COMPLETED_STATE_TYPES or state_name in COMPLETED_STATE_NAMES:
        return "completed"
    if labels & config.duplicate_labels or title.startswith(("duplicate:", "dupe:")):
        return "duplicate"
    if labels & config.umbrella_labels or title.startswith(("epic:", "umbrella:")):
        return "umbrella"
    if labels & config.stale_labels or is_stale(issue, config):
        return "stale"
    if not is_actionable(issue, config):
        return "unassigned"
    return None


def is_actionable(
    issue: IssueSnapshot, config: BoardHygieneConfig | None = None
) -> bool:
    config = config or BoardHygieneConfig()
    labels = {label.lower() for label in issue.labels}
    if labels & config.actionable_labels:
        return True
    return any(
        label.startswith(prefix.lower())
        for label in labels
        for prefix in config.actionable_label_prefixes
    )


def is_stale(issue: IssueSnapshot, config: BoardHygieneConfig | None = None) -> bool:
    config = config or BoardHygieneConfig()
    if issue.updated_at is None or config.stale_after_days <= 0:
        return False
    updated = issue.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return updated < config.effective_now - timedelta(days=config.stale_after_days)


def issue_fingerprint(issue: IssueSnapshot) -> str:
    """Stable fingerprint for changes users care about on actionable issues."""

    payload = {
        "title": issue.title,
        "state_name": issue.state_name,
        "state_type": issue.state_type,
        "labels": sorted(issue.labels),
        "url": issue.url,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def normalize_labels(raw_labels: Any) -> list[str]:
    if isinstance(raw_labels, Mapping):
        raw_labels = raw_labels.get("nodes", ())
    labels: list[str] = []
    for label in raw_labels or ():
        if isinstance(label, str):
            labels.append(label)
        elif isinstance(label, Mapping) and label.get("name"):
            labels.append(str(label["name"]))
    return labels


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
