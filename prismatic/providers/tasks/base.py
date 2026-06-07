"""
Prismatic Engine — Task Provider Interface
===========================================

Abstract base for all issue-tracker backends.  The coordinator calls
methods on whichever provider is configured — Linear, GitHub, Jira,
or a custom bolt-on — without knowing the underlying API.

Every method has a sensible default on failure (empty list, empty Issue,
or False) so callers don't need try/except wrappers around every call.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Issue:
    """A single work item from an issue tracker.

    Fields are deliberately generic — they represent the lowest common
    denominator across Linear, GitHub Issues, Jira, etc.  Provider-specific
    fields live in ``metadata``.
    """
    id: str                              # Provider-internal ID (UUID for Linear)
    identifier: str                       # Human-readable key (GRO-123, #42)
    title: str                            # Issue title / summary
    description: str = ""                 # Body / description text
    state: str = "backlog"                # Current workflow state
    labels: list[str] = field(default_factory=list)
    project: str = ""                     # Project or team name
    comments: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskProvider(ABC):
    """Abstract issue-tracker provider.

    Subclass this to add support for a new backend.  Every method
    includes error handling so callers don't crash on transient API
    failures.
    """

    @abstractmethod
    def get_issues_with_label(self, label: str) -> list[Issue]:
        """Return all open issues that carry a specific label.

        Args:
            label: The label/tag to search for (e.g. ``"pipeline:hermes"``).

        Returns:
            List of matching issues.  Empty list on error or no matches.
        """
        ...

    @abstractmethod
    def add_comment(self, issue_id: str, body: str) -> bool:
        """Post a comment on an issue.

        Args:
            issue_id: The provider-internal issue ID.
            body: Comment text (Markdown supported).

        Returns:
            True if the comment was posted successfully.
        """
        ...

    @abstractmethod
    def set_labels(self, issue_id: str, label_ids: list[str]) -> bool:
        """Replace the label set on an issue.

        Args:
            issue_id: The provider-internal issue ID.
            label_ids: Full list of label IDs (not names) to assign.
                       Passing an empty list removes all labels.

        Returns:
            True if labels were updated successfully.
        """
        ...

    @abstractmethod
    def get_issue(self, issue_id: str) -> Issue | None:
        """Fetch a single issue by its provider-internal ID.

        Args:
            issue_id: The provider-internal issue ID.

        Returns:
            An Issue dataclass, or None if not found / on error.
        """
        ...
