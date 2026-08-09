import unittest
from unittest.mock import ANY, MagicMock, patch

import prismatic.dispatcher as dispatcher


class TestJulesHostPathRouting(unittest.TestCase):
    def test_detect_host_level_patterns_from_title_and_description(self):
        issue = {
            "title": "Repair crontab for host service",
            "description": (
                f"Needs access to {dispatcher.HOME_UBUNTU_MARKER}/.hermes, "
                "~/.config, and /etc/systemd/system from the host."
            ),
        }

        matches = dispatcher.detect_host_level_patterns(issue)

        self.assertIn(dispatcher.HOME_UBUNTU_MARKER, matches)
        self.assertIn("~/.config", matches)
        self.assertIn("/etc", matches)
        self.assertIn("systemd", matches)
        self.assertIn("crontab", matches)

    @patch("prismatic.dispatcher.add_comment")
    @patch("prismatic.dispatcher.set_labels")
    @patch("prismatic.dispatcher.get_label_id")
    @patch("prismatic.dispatcher.get_issue_labels")
    def test_reroute_removes_jules_adds_ned_and_comments(
        self,
        mock_get_issue_labels,
        mock_get_label_id,
        mock_set_labels,
        mock_add_comment,
    ):
        mock_get_issue_labels.return_value = [
            {"id": "label-jules", "name": "agent:jules"},
            {"id": "label-ready", "name": "dispatch:ready"},
        ]
        mock_get_label_id.return_value = "label-ned"
        mock_set_labels.return_value = True
        mock_add_comment.return_value = True
        issue = {
            "id": "issue-uuid",
            "identifier": "GRO-3570",
            "title": "Jules task with host access",
            "description": f"Read /etc/systemd/system and {dispatcher.HOME_UBUNTU_MARKER} logs.",
        }

        ok = dispatcher.reroute_jules_host_path_issue(issue, ["/etc", dispatcher.HOME_UBUNTU_MARKER])

        self.assertTrue(ok)
        mock_get_label_id.assert_called_once_with("agent:ned")
        mock_set_labels.assert_called_once_with("issue-uuid", ["label-ready", "label-ned"])
        comment = mock_add_comment.call_args.args[1]
        self.assertIn("Jules host-path pre-screen", comment)
        self.assertIn("/etc", comment)
        self.assertIn("agent:ned", comment)

    @patch("prismatic.dispatcher.detect_origin_completions", return_value=0)
    @patch("prismatic.dispatcher.recover_stalled_agy")
    @patch("prismatic.dispatcher.cleanup_stale_agy", return_value=0)
    @patch("prismatic.dispatcher.setup_pipeline_issues", return_value=[])
    @patch("prismatic.dispatcher.reroute_jules_host_path_issue", return_value=True)
    @patch("prismatic.dispatcher.get_issues_with_label")
    def test_dispatch_once_skips_jules_launch_for_host_path_issue(
        self,
        mock_get_issues,
        mock_reroute,
        mock_setup,
        mock_cleanup,
        mock_recover,
        mock_origin,
    ):
        dedup = MagicMock()
        dedup.is_processed.return_value = False
        launches = []
        original_launchers = dispatcher.AGENT_LAUNCHERS.copy()
        try:
            dispatcher.AGENT_LAUNCHERS["jules"] = lambda *args, **kwargs: launches.append("jules") or True
            mock_get_issues.side_effect = lambda label, **kwargs: (
                [
                    {
                        "id": "issue-uuid",
                        "identifier": "GRO-3570",
                        "title": "Host cron repair",
                        "description": f"Requires crontab and {dispatcher.HOME_UBUNTU_MARKER}/.hermes access.",
                        "labels": [label],
                    }
                ]
                if label == "agent:jules"
                else []
            )

            counts = dispatcher.dispatch_once(dedup, pipelines={"pipelines": {}})
        finally:
            dispatcher.AGENT_LAUNCHERS = original_launchers

        self.assertEqual(launches, [])
        self.assertEqual(counts.get("host_path_rerouted"), 1)
        mock_reroute.assert_called_once()
        dedup.mark_processed.assert_any_call("issue-uuid", "agent:jules", ANY)


if __name__ == "__main__":
    unittest.main()
