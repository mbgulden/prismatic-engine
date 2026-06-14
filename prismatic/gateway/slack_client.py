"""Slack command bot and interactive approval gateway for the Prismatic Engine.

Provides:
  - /prismatic status → active agent runs and leases (markdown table)
  - /prismatic lock list → active file locks
  - Approval card builder for agent review/interactive mode
  - Interactive action receiver for approval callbacks

Requires slack-sdk and the following env vars:
  SLACK_BOT_TOKEN      — Bot user OAuth token
  SLACK_SIGNING_SECRET — Signing secret for request verification
  SLACK_CHANNEL_ID     — Default channel for approvals / alerts
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from prismatic.lock import _read_locks as read_swarm_locks
from prismatic.run_records import AgentRunRecordStore

logger = logging.getLogger("prismatic.gateway.slack_client")

# ── Helpers ──────────────────────────────────────────────


def _bot_token() -> str:
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        logger.warning("SLACK_BOT_TOKEN not set — Slack commands disabled")
    return token


def _channel_id() -> str:
    return os.environ.get("SLACK_CHANNEL_ID", "C01ABC23DE4")


def _signing_secret() -> str:
    return os.environ.get("SLACK_SIGNING_SECRET", "")


# ── Request Verification ─────────────────────────────────


def verify_slack_request(body: bytes, timestamp: str, signature: str) -> bool:
    """Verify Slack's signed secret using versioned hash (v0).

    Args:
        body: Raw request body bytes.
        timestamp: X-Slack-Request-Timestamp header value.
        signature: X-Slack-Signature header value.

    Returns:
        True if the signature matches the computed HMAC-SHA256.
    """
    secret = _signing_secret()
    if not secret:
        logger.warning("SLACK_SIGNING_SECRET not set — slack verification disabled")
        return False
    base_string = f"v0:{timestamp}:{body.decode('utf-8')}".encode("utf-8")
    computed = "v0=" + hmac.new(secret.encode("utf-8"), base_string, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature)


# ── Interactive Approval Card Builder ────────────────────


@dataclass
class ApprovalCard:
    """An interactive Slack message card for human approval."""

    channel: str = ""
    title: str = "Approval Required"
    agent_name: str = "unknown"
    issue_id: str = ""
    issue_title: str = ""
    description: str = ""
    action_value: str = ""
    blocks: list[dict[str, Any]] = field(default_factory=list)

    def build(self) -> dict[str, Any]:
        """Build the Slack Blocks payload for the approval card."""
        action_block_id = f"approval_{self.issue_id or 'unknown'}"
        return {
            "channel": self.channel or _channel_id(),
            "text": f"{self.title}: {self.issue_title}",
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*{self.title}*\n"
                            f"*Agent:* `{self.agent_name}`\n"
                            f"*Issue:* {self.issue_id} — {self.issue_title}\n"
                            f"{'  _' + self.description + '_' if self.description else ''}"
                        ),
                    },
                },
                {
                    "type": "actions",
                    "block_id": action_block_id,
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "✅ Approve"},
                            "style": "primary",
                            "value": f"approve:{self.issue_id}",
                            "action_id": f"approve_{self.issue_id}",
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "❌ Reject"},
                            "style": "danger",
                            "value": f"reject:{self.issue_id}",
                            "action_id": f"reject_{self.issue_id}",
                        },
                    ],
                },
            ],
        }


# ── Slack Client Wrapper ─────────────────────────────────


class SlackBot:
    """High-level Slack bot client for Prismatic Engine operations.

    Usage:
        bot = SlackBot()
        bot.post_approval(ApprovalCard(agent_name="fred", issue_id="GRO-1", ...))
        response = bot.handle_slash_command("/prismatic status")
    """

    def __init__(self, run_store: AgentRunRecordStore | None = None) -> None:
        token = _bot_token()
        self._client = WebClient(token=token) if token else None
        self._run_store = run_store
        logger.info(
            "SlackBot initialized (client=%s, store=%s)",
            "connected" if self._client else "disabled (no token)",
            "available" if run_store else "None",
        )

    # ── Command Handlers ──────────────────────────────────

    def handle_command(self, command: str, channel: str | None = None) -> str:
        """Route a Slack slash command and return the response text.

        Supported commands:
          /prismatic status       — active runs + leases
          /prismatic lock list    — active file locks
          /prismatic help         — available commands
        """
        cmd = command.strip().lower()
        if cmd in ("status", ""):
            return self._cmd_status()
        if cmd in ("lock list", "locks"):
            return self._cmd_lock_list()
        if cmd in ("help", "--help", "-h"):
            return self._cmd_help()
        return f"Unknown command: `{command}`. Use `/prismatic help` for available commands."

    def _cmd_status(self) -> str:
        """Return a markdown table of active agent runs."""
        lines = ["*Active Agent Runs*\n"]
        if self._run_store:
            records = self._run_store.get_recent_runs(limit=20)
            if not records:
                lines.append("No recent runs recorded.\n")
            else:
                header = "| Run ID | Agent | Issue | Status | Started |"
                sep = "|--------|-------|-------|--------|---------|"
                lines.append(header)
                lines.append(sep)
                for rec in records[:10]:
                    if rec.started_at:
                        try:
                            dt = datetime.fromisoformat(rec.started_at)
                            started = dt.strftime("%H:%M UTC")
                        except (ValueError, TypeError):
                            started = rec.started_at[:16] if len(rec.started_at) > 16 else rec.started_at
                    else:
                        started = "—"
                    lines.append(
                        f"| `{rec.run_id[:8]}` | `{rec.agent_name}` | {rec.issue_id} | {rec.status} | {started} |"
                    )
        else:
            lines.append("Run store not available.\n")

        locks = read_swarm_locks()
        if locks:
            lines.append(f"\n*Active Locks ({len(locks)})*\n")
            for lock in locks[:10]:
                fpath = lock.get("file", "?")
                holder = lock.get("holder", "?")
                lines.append(f"  • `{fpath}` — held by `{holder}`")
        else:
            lines.append("\nNo active file locks.\n")

        return "\n".join(lines)

    def _cmd_lock_list(self) -> str:
        """Return a markdown list of all active file locks."""
        locks = read_swarm_locks()
        if not locks:
            return "No active file locks."

        now_ms = (datetime.now(tz=timezone.utc).timestamp()) * 1000
        lines = ["*Active File Locks*\n", "| File | Holder | Age (s) | Stale? |", "|------|--------|---------|--------|"]
        for lock in locks:
            fpath = lock.get("file", "?")
            holder = lock.get("holder", "?")
            hb = lock.get("heartbeat_ms", now_ms)
            age_s = round((now_ms - hb) / 1000, 1) if hb else 0
            stale = "⚠️" if age_s > 300 else "✓"
            lines.append(f"| `{fpath}` | `{holder}` | {age_s}s | {stale} |")
        return "\n".join(lines)

    @staticmethod
    def _cmd_help() -> str:
        return (
            "*Prismatic Engine — Slack Commands*\n"
            "• `/prismatic status` — Show active agent runs and file locks\n"
            "• `/prismatic lock list` — List all active file locks\n"
            "• `/prismatic help` — Show this help message"
        )

    # ── Outbound Actions ──────────────────────────────────

    def post_message(self, text: str, channel: str | None = None) -> dict[str, Any]:
        """Post a plain text message to a Slack channel.

        Returns the API response dict, or an error dict on failure.
        """
        if not self._client:
            return {"ok": False, "error": "Slack client not initialized — no token"}
        try:
            resp = self._client.chat_postMessage(channel=channel or _channel_id(), text=text)
            logger.info("Slack message posted to %s (ts=%s)", channel or _channel_id(), resp.get("ts"))
            return resp
        except SlackApiError as e:
            logger.error("Slack post_message failed: %s", e)
            return {"ok": False, "error": str(e)}

    def post_approval(self, card: ApprovalCard) -> dict[str, Any]:
        """Post an interactive approval card to Slack.

        Returns the API response dict, or an error dict on failure.
        """
        if not self._client:
            return {"ok": False, "error": "Slack client not initialized — no token"}
        payload = card.build()
        try:
            resp = self._client.chat_postMessage(**payload)
            logger.info(
                "Approval card posted to %s for issue %s (ts=%s)",
                payload["channel"],
                card.issue_id,
                resp.get("ts"),
            )
            return resp
        except SlackApiError as e:
            logger.error("Slack post_approval failed: %s", e)
            return {"ok": False, "error": str(e)}

    def process_interactive_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Process a Slack interactive action callback (button click).

        Args:
            payload: The parsed Slack interactive payload dict.

        Returns:
            Response dict with action result and message to display.
        """
        actions = payload.get("actions", [])
        if not actions:
            return {"ok": False, "error": "No actions in payload"}

        action = actions[0]
        action_id = action.get("action_id", "")
        value = action.get("value", "")

        # Extract issue ID from value (format: "approve:GRO-123" or "reject:GRO-123")
        verb = "approved" if value.startswith("approve:") else "rejected" if value.startswith("reject:") else "unknown"
        issue_id = value.split(":", 1)[1] if ":" in value else "unknown"

        user = payload.get("user", {}).get("name", "unknown")
        channel = payload.get("channel", {}).get("id", _channel_id())

        response_text = (
            f":white_check_mark: *{issue_id} {verb}* by @{user}\n"
            "The agent pipeline has been updated accordingly."
        )

        logger.info("Slack interactive action: %s %s by %s", verb, issue_id, user)
        return {
            "ok": True,
            "action": verb,
            "issue_id": issue_id,
            "user": user,
            "response_text": response_text,
            "channel": channel,
        }
