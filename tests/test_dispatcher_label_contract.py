"""
tests/test_dispatcher_label_contract.py
========================================

Lane-scan label contract: the dispatcher must query the single-colon
``agent:<name>`` labels that actually exist in Linear
(docs/proof-loop-demo-wedge.md — e.g. ``agent:agy``).

Regression: the lane scan queried ``agent::<name>`` (double colon), which
matches no Linear label — every lane perpetually reported STARVED.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Mocking external dependencies before importing dispatcher.
sys.modules['prismatic.providers.signals'] = MagicMock()
sys.modules['prismatic.credit_policy_engine'] = MagicMock()

import prismatic.dispatcher as dispatcher
from prismatic.mode_switch import OrchestrationMode


class TestDispatcherLabelContract(unittest.TestCase):
    """The lane scan must ask Linear for the labels that exist."""

    @patch('prismatic.dispatcher.recover_stalled_agy')
    @patch('prismatic.dispatcher.gql')
    @patch('prismatic.providers.github.GitHubProvider')
    @patch('prismatic.dispatcher.get_issues_with_label')
    @patch('prismatic.dispatcher.EventRouterDedup')
    @patch('prismatic.dispatcher.evaluate_agent_launch')
    def test_lane_scan_queries_single_colon_agent_labels(
        self,
        mock_evaluate,
        mock_dedup_cls,
        mock_get_issues,
        mock_github_provider_cls,
        mock_gql,
        mock_recover_stalled,
    ):
        dispatcher.mode_switch.set_mode(OrchestrationMode.AUTONOMOUS)

        mock_dedup = mock_dedup_cls.return_value
        mock_dedup.is_processed.return_value = False
        mock_dedup._conn.cursor.return_value.fetchone.return_value = None
        mock_evaluate.return_value.action = dispatcher.PolicyAction.ALLOW
        mock_github_provider_cls.return_value.has_credentials.return_value = True
        mock_gql.return_value = {"issue": {"comments": {"nodes": []}}}

        # No issues anywhere: the scan runs, finds nothing, reports STARVED.
        mock_get_issues.return_value = []

        dispatcher.dispatch_once(mock_dedup, pipelines={"pipelines": {}})

        # Lane-scan calls carry no kwargs; the capability-routing load
        # snapshot passes max_issues=100. recover_stalled_agy is patched out,
        # so every kwarg-free call below is the lane scan.
        lane_labels = [
            call.args[0]
            for call in mock_get_issues.call_args_list
            if not call.kwargs
        ]
        self.assertTrue(lane_labels, "expected the lane scan to query Linear")

        for agent_name in dispatcher.AGENT_CONFIG:
            with self.subTest(agent=agent_name):
                self.assertIn(
                    f"agent:{agent_name}",
                    lane_labels,
                    f"lane scan did not query the canonical label agent:{agent_name}",
                )
        for label in lane_labels:
            with self.subTest(label=label):
                self.assertNotIn(
                    "::", label,
                    f"lane scan queried non-canonical double-colon label {label!r}",
                )


class TestRecoverStalledAgyLabelContract(unittest.TestCase):
    """recover_stalled_agy must query AND transition canonical labels.

    Regression: it queried ``"agent::agy"`` (matches nothing in Linear, so
    stalled AGY work was never recovered) and transitioned with double-colon
    labels. transition_label -> get_label_id CREATES missing labels, so a
    query-only fix would mint bogus ``agent::<name>`` labels in Linear —
    both must change together.
    """

    @patch("prismatic.dispatcher.AGENT_LAUNCHERS", {})
    @patch("prismatic.dispatcher.cleanup_stale_agy")
    @patch("prismatic.dispatcher.add_comment")
    @patch("prismatic.dispatcher.transition_label")
    @patch("prismatic.dispatcher.get_issues_with_label")
    def test_recover_queries_and_transitions_single_colon(
        self,
        mock_get_issues,
        mock_transition,
        mock_add_comment,
        mock_cleanup,
    ):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "recover.db")
            # Seed the stall tracker so this issue escalates on this cycle.
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE agy_stall_tracker ("
                "issue_id TEXT PRIMARY KEY, cycle_count INTEGER DEFAULT 0, "
                "last_seen TEXT, escalated INTEGER DEFAULT 0)"
            )
            conn.execute(
                "INSERT INTO agy_stall_tracker VALUES (?, ?, ?, 0)",
                ("issue-1", 2, "2026-01-01T00:00:00+00:00"),
            )
            conn.commit()
            conn.close()

            mock_get_issues.return_value = [
                {"id": "issue-1", "identifier": "GRO-1", "title": "stalled"}
            ]
            with (
                patch.object(dispatcher, "DEFAULT_DB_PATH", db_path),
                patch.object(
                    dispatcher.mode_switch, "request_approval", return_value=True
                ),
            ):
                dispatcher.recover_stalled_agy(max_retries=3, escalate_to="fred")

        queried = [call.args[0] for call in mock_get_issues.call_args_list]
        self.assertEqual(
            queried,
            ["agent:agy"],
            "recover_stalled_agy must query the canonical label agent:agy",
        )
        for label in queried:
            self.assertNotIn("::", label)

        mock_transition.assert_called_once()
        kwargs = mock_transition.call_args.kwargs
        self.assertEqual(kwargs["remove_label"], "agent:agy")
        self.assertEqual(kwargs["add_label"], "agent:fred")
        self.assertNotIn("::", kwargs["remove_label"])
        self.assertNotIn("::", kwargs["add_label"])


class TestDetectOriginCompletionsLabelContract(unittest.TestCase):
    """detect_origin_completions must signal on canonical single-colon history.

    Regression: origin detection compared ``f"agent::{agent_name}"``
    against label snapshots that only ever contain single-colon names, so
    origin signals never fired. The skip-list comparison against
    ``("agent:agy", "agent:fred", "agent:done")`` was dead code for the same
    reason.
    """

    @patch("prismatic.dispatcher._get_signal_provider")
    @patch("prismatic.dispatcher.get_issues_with_label")
    def test_origin_signal_fires_on_single_colon_history(
        self, mock_get_issues, mock_provider_factory
    ):
        with tempfile.TemporaryDirectory() as tmp:
            dedup = dispatcher.EventRouterDedup(os.path.join(tmp, "dedup.db"))
            try:
                # Prior cycles: the issue went kai -> agy -> fred.
                dedup.snapshot_labels("issue-1", ["agent:kai"], "cycle-1")
                dedup.snapshot_labels("issue-1", ["agent:agy"], "cycle-2")

                def fake_get_issues(label_name, **kwargs):
                    if label_name == "agent:fred":
                        return [
                            {
                                "id": "issue-1",
                                "identifier": "GRO-1",
                                "title": "reviewed",
                                "labels": ["agent:fred"],
                            }
                        ]
                    return []

                mock_get_issues.side_effect = fake_get_issues
                provider = MagicMock()
                provider.send_work.return_value = True
                mock_provider_factory.return_value = provider

                # Neutralize DynamicAgentConfigDict._ensure_fresh: without
                # this, the first AGENT_CONFIG iteration inside
                # detect_origin_completions would wipe the seeded fixture
                # and replace it with live-discovered agents (TTL refresh),
                # making the test pass for the wrong reason (or fail in a
                # clean environment without discovery).
                with (
                    patch.dict(
                        dispatcher.AGENT_CONFIG,
                        {"agy": {}, "fred": {}, "kai": {}},
                        clear=True,
                    ),
                    patch.object(
                        dispatcher.AGENT_CONFIG, "_ensure_fresh", lambda: None
                    ),
                ):
                    self.assertEqual(
                        set(dispatcher.AGENT_CONFIG.keys()),
                        {"agy", "fred", "kai"},
                        "AGENT_CONFIG fixture was not hermetic",
                    )
                    signalled = dispatcher.detect_origin_completions(
                        dedup, "cycle-3"
                    )

                self.assertEqual(
                    signalled,
                    1,
                    "origin completion was not signalled for kai -> agy -> fred",
                )
                provider.send_work.assert_called_once()
                send_kwargs = provider.send_work.call_args.kwargs
                self.assertEqual(send_kwargs["target"], "kai")
                self.assertEqual(send_kwargs["signal_type"], "review_complete")

                # The snapshot loop must not query dead double-colon labels.
                for call in mock_get_issues.call_args_list:
                    self.assertNotIn("::", call.args[0])
            finally:
                dedup.close()


if __name__ == "__main__":
    unittest.main()
