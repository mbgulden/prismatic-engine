"""PR Reviewer — Phase 2 Gap 4 (Task #1 stub).

This module defines the **public interface** that the
``agent:ned-review`` trigger in ``prismatic.quality.gates`` calls.
Tasks #1–5 of Gap 4 fill in the real implementation (GitHub diff fetch,
secret scan, complexity metrics, lint, coverage heuristics, GitHub API
inline comments). This file ships the contract + a deterministic stub
so the trigger wiring (this task, GRO-2876) can be developed and tested
independently of the heavier reviewer logic.

Verdict values
--------------
- ``APPROVE``         — clean PR; auto-transition Linear to Done
- ``REQUEST_CHANGES`` — issues found; re-route to original worker
- ``NEEDS_DISCUSSION``— ambiguous; flag for Michael

The stub returns ``APPROVE`` with an empty comment list. Tests and the
trigger rely on these values; the real reviewer (tasks #1–5) plugs in
without changing the trigger contract.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────

NED_REVIEW_LABEL = "agent:ned-review"
"""Linear label that marks a task as needing a ned-review pass."""

APPROVE = "APPROVE"
REQUEST_CHANGES = "REQUEST_CHANGES"
NEEDS_DISCUSSION = "NEEDS_DISCUSSION"

VERDICTS = {APPROVE, REQUEST_CHANGES, NEEDS_DISCUSSION}


# ─────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────


@dataclass
class InlineComment:
    """One inline comment the reviewer wants to post on the PR.

    The real reviewer (tasks #1–5) produces these from the diff scan;
    the stub leaves the list empty.
    """

    path: str
    line: int
    body: str


@dataclass
class PRReviewResult:
    """Structured verdict returned by :class:`PRReviewer.review_pr`.

    ``verdict`` is one of ``APPROVE`` / ``REQUEST_CHANGES`` /
    ``NEEDS_DISCUSSION``. ``summary`` is the markdown body posted to
    Linear. ``inline_comments`` are posted to the PR via GitHub API.
    ``metadata`` is opaque — kept for the trigger to log and for tests
    to assert against without coupling to internal reviewer fields.
    """

    verdict: str
    summary: str
    inline_comments: list[InlineComment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(
                f"Invalid verdict {self.verdict!r}; must be one of {sorted(VERDICTS)}"
            )

    @property
    def passed(self) -> bool:
        """Convenience flag — True iff verdict is APPROVE."""
        return self.verdict == APPROVE

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "summary": self.summary,
            "inline_comments": [
                {"path": c.path, "line": c.line, "body": c.body}
                for c in self.inline_comments
            ],
            "metadata": self.metadata,
        }


# ─────────────────────────────────────────────────────────────────────
# Reviewer protocol
# ─────────────────────────────────────────────────────────────────────


@runtime_checkable
class PRReviewer(Protocol):
    """Pluggable PR reviewer interface.

    Any object that implements ``review_pr(pr_url: str) -> PRReviewResult``
    satisfies this protocol. Tests pass fakes; production uses the real
    GitHub-API-backed reviewer built in tasks #1–5.
    """

    def review_pr(self, pr_url: str) -> PRReviewResult: ...


# ─────────────────────────────────────────────────────────────────────
# Stub implementation
# ─────────────────────────────────────────────────────────────────────


class StubPRReviewer:
    """Deterministic reviewer used until tasks #1–5 land.

    Behavior:
      - If env ``NED_REVIEW_STUB_VERDICT`` is set to a valid verdict,
        return that verdict (useful for integration tests).
      - Otherwise return ``APPROVE`` with an empty comment list.

    This mirrors the Phase 1 pattern of small, deterministic stubs that
    can be replaced without rewriting callers.
    """

    def __init__(self, default_verdict: str | None = None) -> None:
        if default_verdict is None:
            default_verdict = os.environ.get("NED_REVIEW_STUB_VERDICT", APPROVE)
        if default_verdict not in VERDICTS:
            raise ValueError(
                f"Invalid default_verdict {default_verdict!r}; must be one of {sorted(VERDICTS)}"
            )
        self._default_verdict = default_verdict

    def review_pr(self, pr_url: str) -> PRReviewResult:
        summary = (
            f"🤖 **Ned-Review (stub)** — `{self._default_verdict}`\n\n"
            f"_Stub reviewer; real implementation lands with Gap 4 tasks #1–5._\n\n"
            f"PR: `{pr_url}`"
        )
        return PRReviewResult(
            verdict=self._default_verdict,
            summary=summary,
            inline_comments=[],
            metadata={"reviewer": "stub", "pr_url": pr_url},
        )
