"""
prismatic/observability/ops_feed.py — Review-pipeline Linear ops feed (Gap 12)

Post review-pipeline events as Linear comments using Pattern B
(LinearTaskProvider.add_comment — canonical urllib.request GraphQL client).

Environment variables
---------------------
LINEAR_API_KEY
    Required.  Personal API key from Linear.app Settings → API.
PRISMATIC_OPS_FEED_ISSUE_ID
    Optional.  Target Linear issue ID for ops feed comments.
    If unset, post_review_event_to_linear() falls back to stdout-only
    mode (no Linear posts).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from prismatic.providers.tasks.linear import LinearTaskProvider

# ── Valid event types for the review pipeline ────────────────────────
VALID_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "review.completed",
        "plugin.registered",
        "plugin.register_failed",
        "hook.fired",
        "hook.failed",
        "pipeline.action",
    }
)


def post_review_event_to_linear(
    issue_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    linear_api_key: str | None = None,
) -> bool:
    """Post a review-pipeline event as a Linear comment.

    Falls back to stdout logging if linear_api_key is None or if the
    Linear API call fails. Never raises.

    Args:
        issue_id: Linear issue ID to post the comment on.
        event_type: One of the VALID_EVENT_TYPES.
        payload: Arbitrary dict of event data to serialize.
        linear_api_key: Linear API key override. If None, falls back
            to the LINEAR_API_KEY environment variable. If both are
            missing, uses stdout-only mode (no Linear post).

    Returns:
        True if Linear accepted the comment, False otherwise.
    """
    try:
        # ── Validate event type ─────────────────────────────────
        if event_type not in VALID_EVENT_TYPES:
            print(
                f"prismatic.observability.ops_feed: invalid event_type "
                f"{event_type!r}; must be one of {sorted(VALID_EVENT_TYPES)}",
                file=sys.stderr,
            )
            return False

        # ── Build markdown body ─────────────────────────────────
        body = _build_markdown_body(event_type, payload)

        # ── Determine API key ───────────────────────────────────
        api_key = linear_api_key or os.environ.get("LINEAR_API_KEY", "")
        if not api_key:
            # Stdout-only fallback: no Linear post
            print(
                f"prismatic.observability.ops_feed [ops]: {event_type} "
                f"issue={issue_id}\n{body}",
            )
            return False

        # ── Post via LinearTaskProvider (Pattern B) ─────────────
        provider = LinearTaskProvider()
        # Override the api_key in case the env var differs from the
        # caller-supplied key (e.g., in tests or multi-tenant configs).
        if linear_api_key:
            provider._api_key = linear_api_key  # noqa: SLF001

        success = provider.add_comment(issue_id, body)
        if not success:
            print(
                f"prismatic.observability.ops_feed: Linear add_comment "
                f"returned False for issue {issue_id} (event={event_type})",
                file=sys.stderr,
            )
        return success

    except Exception as exc:  # noqa: BLE001
        print(
            f"prismatic.observability.ops_feed: unhandled exception posting "
            f"{event_type} to {issue_id}: {exc}",
            file=sys.stderr,
        )
        return False


def _build_markdown_body(event_type: str, payload: dict[str, Any]) -> str:
    """Build a markdown comment body from an event type and payload dict."""
    lines = [
        f"## 🔭 Prismatic Ops Feed: `{event_type}`",
        "",
    ]

    # Flatten scalar fields as a table; nested dicts/lists as JSON blocks
    scalar_rows: list[tuple[str, Any]] = []
    nested_items: list[tuple[str, Any]] = []

    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            nested_items.append((key, value))
        else:
            scalar_rows.append((key, value))

    if scalar_rows:
        lines.append("| Field | Value |")
        lines.append("|-------|-------|")
        for key, value in scalar_rows:
            lines.append(f"| `{key}` | {value} |")
        lines.append("")

    for key, value in nested_items:
        lines.append(f"**{key}:**")
        lines.append("```json")
        lines.append(json.dumps(value, indent=2))
        lines.append("```")
        lines.append("")

    return "\n".join(lines)
