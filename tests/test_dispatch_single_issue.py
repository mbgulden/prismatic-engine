"""
tests/test_dispatch_single_issue.py
====================================

Verifies the new ``dispatch_issue_by_identifier`` helper used by the
webhook handler for single-issue fast-path dispatch (Tier 7 hardening).

Ref: GRO-2048 + Tier 7. Cost: ~1-2 Linear API calls per webhook event,
vs ~20 for the full dispatch_once cycle.
"""

import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import tempfile
from pathlib import Path

sys.modules["prismatic.providers.signals"] = MagicMock()
sys.modules["prismatic.credit_policy_engine"] = MagicMock()


def _make_issue(identifier, agent_label):
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": f"Test {identifier}",
        "description": "",
        "state": {"name": "Todo", "type": "unstarted"},
        "labels": {"nodes": [{"id": "l1", "name": agent_label}]},
        "url": f"https://linear.app/x/{identifier}",
    }


class TestDispatchSingleIssue(unittest.TestCase):
    """Test the webhook single-issue dispatch path."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "PRISMATIC_STATE_DIR": self._tmp.name,
            "PRISMATIC_TEAM_ID": "test-team",
        })
        self.env.start()

        # Patch AGENT_CONFIG and AGENT_LAUNCHERS to one test lane.
        # dispatch_issue_by_identifier strips the 'agent:' prefix when looking up
        # AGENT_LAUNCHERS, so the dict key here is just the agent short name.
        import prismatic.dispatcher as dispatcher
        self.dispatcher = dispatcher
        self.agy_config_patcher = patch.object(
            dispatcher, "AGENT_CONFIG",
            {"agy": {"next_label": "agent:fred"}},  # short name, no prefix
        )
        self.agy_config_patcher.start()
        self.launcher_patcher = patch.object(
            dispatcher, "AGENT_LAUNCHERS",
            {"agy": MagicMock(return_value=True)},  # short name
        )
        self.launcher_patcher.start()
        self.eval_patcher = patch.object(
            dispatcher, "evaluate_agent_launch",
            return_value=MagicMock(action=MagicMock(value="ALLOW"), reason="ok"),
        )
        self.eval_patcher.start()
        self.dedup_init_patcher = patch.object(
            dispatcher.EventRouterDedup, "__init__", return_value=None,
        )
        # Don't actually init the dedup — we mock its methods.
        self.dedup_init_patcher.start()
        self.dedup_is_patcher = patch.object(
            dispatcher.EventRouterDedup, "is_processed", return_value=False,
        )
        self.dedup_is_patcher.start()
        self.dedup_mark_patcher = patch.object(
            dispatcher.EventRouterDedup, "mark_processed", return_value=None,
        )
        self.dedup_mark_patcher.start()

    def tearDown(self):
        self.dedup_mark_patcher.stop()
        self.dedup_is_patcher.stop()
        self.dedup_init_patcher.stop()
        self.eval_patcher.stop()
        self.launcher_patcher.stop()
        self.agy_config_patcher.stop()
        self.env.stop()
        self._tmp.cleanup()

    def test_dispatches_when_label_matches(self):
        """Returns True when issue has matching agent label."""
        with patch.object(
            self.dispatcher, "get_issue_by_identifier",
            return_value=_make_issue("GRO-2051", "agent:agy"),
        ):
            result = self.dispatcher.dispatch_issue_by_identifier("GRO-2051")
        self.assertTrue(result)
        # Verify launcher was called
        self.dispatcher.AGENT_LAUNCHERS["agy"].assert_called_once()

    def test_returns_false_when_label_doesnt_match(self):
        """Returns False when no configured agent label is on the issue."""
        with patch.object(
            self.dispatcher, "get_issue_by_identifier",
            return_value=_make_issue("GRO-2051", "type:docs"),  # no agent label
        ):
            result = self.dispatcher.dispatch_issue_by_identifier("GRO-2051")
        self.assertFalse(result)
        self.dispatcher.AGENT_LAUNCHERS["agy"].assert_not_called()

    def test_returns_false_when_issue_not_found(self):
        """Returns False when the issue doesn't exist in Linear."""
        with patch.object(
            self.dispatcher, "get_issue_by_identifier", return_value=None,
        ):
            result = self.dispatcher.dispatch_issue_by_identifier("GRO-9999")
        self.assertFalse(result)

    def test_returns_false_when_dedup_says_processed(self):
        """Returns False when issue is in the dedup window."""
        with patch.object(
            self.dispatcher, "get_issue_by_identifier",
            return_value=_make_issue("GRO-2051", "agent:agy"),
        ):
            with patch.object(
                self.dispatcher.EventRouterDedup, "is_processed", return_value=True,
            ):
                result = self.dispatcher.dispatch_issue_by_identifier("GRO-2051")
        self.assertFalse(result)
        self.dispatcher.AGENT_LAUNCHERS["agy"].assert_not_called()

    def test_returns_false_when_credit_policy_denies(self):
        """Returns False when credit-policy gate denies."""
        from prismatic.credit_policy_engine import PolicyAction
        deny_decision = MagicMock()
        deny_decision.action = PolicyAction.DENY
        deny_decision.reason = "rate limit exceeded"
        deny_decision.estimated_cost = 10
        with patch.object(
            self.dispatcher, "get_issue_by_identifier",
            return_value=_make_issue("GRO-2051", "agent:agy"),
        ), patch.object(
            self.dispatcher, "evaluate_agent_launch", return_value=deny_decision,
        ):
            result = self.dispatcher.dispatch_issue_by_identifier("GRO-2051")
        self.assertFalse(result)
        self.dispatcher.AGENT_LAUNCHERS["agy"].assert_not_called()

    def test_accepts_legacy_double_colon_label(self):
        """Returns True when issue has agent::agy (legacy double-colon)."""
        with patch.object(
            self.dispatcher, "get_issue_by_identifier",
            return_value=_make_issue("GRO-2051", "agent::agy"),  # legacy
        ):
            result = self.dispatcher.dispatch_issue_by_identifier("GRO-2051")
        self.assertTrue(result)

    def test_returns_false_when_launcher_returns_false(self):
        """Returns False when launcher returns False."""
        with patch.object(
            self.dispatcher, "get_issue_by_identifier",
            return_value=_make_issue("GRO-2051", "agent:agy"),
        ), patch.dict(
            self.dispatcher.AGENT_LAUNCHERS,
            {"agy": MagicMock(return_value=False)},
        ):
            result = self.dispatcher.dispatch_issue_by_identifier("GRO-2051")
        self.assertFalse(result)

    def test_returns_false_when_launcher_raises(self):
        """Returns False when launcher raises (caller decides what to do)."""
        with patch.object(
            self.dispatcher, "get_issue_by_identifier",
            return_value=_make_issue("GRO-2051", "agent:agy"),
        ), patch.dict(
            self.dispatcher.AGENT_LAUNCHERS,
            {"agy": MagicMock(side_effect=RuntimeError("launch failed"))},
        ):
            result = self.dispatcher.dispatch_issue_by_identifier("GRO-2051")
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()