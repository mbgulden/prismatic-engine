import unittest
from unittest.mock import ANY, MagicMock, patch

import prismatic.dispatcher as dispatcher


class TestJulesHostPathRouting(unittest.TestCase):
    def test_detect_host_level_patterns_from_title_and_description(self):
        home_ubuntu = "/" + "home" + "/" + "ubuntu"
        issue = {
            "title": "Fix crontab dispatch failure",
            "description": f"Needs access to {home_ubuntu}/.hermes and ~/.config on the host.",
        }

        matches = dispatcher.detect_host_level_patterns(issue)

        self.assertIn(home_ubuntu, matches)
        self.assertIn("~/.config", matches)
        self.assertIn("~/.hermes", matches)
        self.assertIn("crontab", matches)

    @patch("prismatic.dispatcher.add_comment")
    @patch("prismatic.dispatcher.set_labels")
    @patch("prismatic.dispatcher.get_label_id")
    @patch("prismatic.dispatcher.get_issue_labels")
    def test_reroute_removes_jules_adds_ned_infra_and_comments(
        self, mock_get_issue_labels, mock_get_label_id, mock_set_labels, mock_add_comment
    ):
        mock_get_issue_labels.return_value = [
            {"id": "label-jules", "name": "agent:jules"},
            {"id": "label-ready", "name": "dispatch:ready"},
        ]
        mock_get_label_id.return_value = "label-ned-infra"
        mock_set_labels.return_value = True
        mock_add_comment.return_value = True
        home_ubuntu = "/" + "home" + "/" + "ubuntu"
        issue = {
            "id": "issue-uuid",
            "identifier": "GRO-3570",
            "title": "Jules task with host access",
            "description": f"Read /etc/systemd/system and {home_ubuntu} logs.",
        }

        self.assertTrue(dispatcher.reroute_jules_host_path_issue(issue, ["/etc", home_ubuntu]))

        mock_get_label_id.assert_called_once_with("agent:ned-infra")
        mock_set_labels.assert_called_once_with(
            "issue-uuid", ["label-ready", "label-ned-infra"]
        )
        comment = mock_add_comment.call_args.args[1]
        self.assertIn("Jules host-path pre-screen", comment)
        self.assertIn("/etc", comment)
        self.assertIn("agent:ned-infra", comment)

    @patch("prismatic.dispatcher.recover_stalled_agy")
    @patch("prismatic.dispatcher.cleanup_stale_agy", return_value=0)
    @patch("prismatic.dispatcher.reroute_jules_host_path_issue", return_value=True)
    @patch("prismatic.dispatcher.get_issues_with_label")
    @patch("prismatic.dispatcher.evaluate_agent_launch")
    def test_dispatch_once_skips_jules_launch_for_host_path_issue(
        self,
        mock_evaluate,
        mock_get_issues,
        mock_reroute,
        mock_cleanup,
        mock_recover,
    ):
        dedup = MagicMock()
        dedup.is_processed.return_value = False
        dedup._conn.cursor.return_value.fetchone.return_value = None
        launches = []
        original_launchers = dispatcher.AGENT_LAUNCHERS.copy()
        home_ubuntu = "/" + "home" + "/" + "ubuntu"
        try:
            dispatcher.AGENT_LAUNCHERS["jules"] = lambda *args, **kwargs: launches.append("jules")
            mock_get_issues.side_effect = lambda label: [
                {
                    "id": "issue-uuid",
                    "identifier": "GRO-3570",
                    "title": "Host cron repair",
                    "description": f"Requires crontab and {home_ubuntu}/.hermes access.",
                    "labels": [label],
                }
            ] if label == "agent::jules" else []

            counts = dispatcher.dispatch_once(dedup, pipelines={"pipelines": {}})
        finally:
            dispatcher.AGENT_LAUNCHERS = original_launchers

        self.assertEqual(launches, [])
        self.assertEqual(counts.get("host_path_rerouted"), 1)
        mock_reroute.assert_called_once()
        dedup.mark_processed.assert_any_call("issue-uuid", "agent::jules", ANY)
