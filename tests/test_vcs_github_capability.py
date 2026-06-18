import unittest
from unittest.mock import patch, MagicMock, call
import json
import urllib.error

from prismatic.providers.github import GitHubProvider
from prismatic.capabilities.vcs_github import GitHubCapability, _emit_event


class TestVcsGithubCapability(unittest.TestCase):
    """Test suite for high-level GitHub VCS capability wrapper."""

    def setUp(self):
        # We mock the underlying provider
        self.mock_provider = MagicMock(spec=GitHubProvider)
        self.mock_provider.token = "test-token"
        self.mock_provider.repo = "owner/repo"
        self.capability = GitHubCapability(provider=self.mock_provider)

    @patch("prismatic.capabilities.vcs_github.send_event_via_socket")
    def test_emit_event_enabled(self, mock_send):
        # Test event emitter helper
        with patch("prismatic.capabilities.vcs_github._HAS_IPC", True):
            _emit_event("test_type", {"foo": "bar"})
            mock_send.assert_called_once_with(
                event_type="test_type",
                source="vcs.github",
                payload={"foo": "bar"}
            )

    @patch("prismatic.capabilities.vcs_github.send_event_via_socket")
    def test_create_branch_new(self, mock_send):
        # Setup: branch does not exist, creation succeeds
        self.mock_provider.get_branch_ref.return_value = None
        self.mock_provider.create_branch.return_value = True

        with patch("prismatic.capabilities.vcs_github._HAS_IPC", True):
            res = self.capability.create_branch("feature/branch-x", "main")
            self.assertTrue(res)
            self.mock_provider.get_branch_ref.assert_called_once_with("feature/branch-x")
            self.mock_provider.create_branch.assert_called_once_with("feature/branch-x", "main")
            mock_send.assert_called_once_with(
                event_type="vcs.branch_created",
                source="vcs.github",
                payload={"provider": "github", "branch": "feature/branch-x", "base": "main"}
            )

    @patch("prismatic.capabilities.vcs_github.send_event_via_socket")
    def test_create_branch_existing(self, mock_send):
        # Setup: branch already exists
        self.mock_provider.get_branch_ref.return_value = {"ref": "refs/heads/feature/branch-x"}
        
        with patch("prismatic.capabilities.vcs_github._HAS_IPC", True):
            res = self.capability.create_branch("feature/branch-x", "main")
            self.assertTrue(res)
            self.mock_provider.get_branch_ref.assert_called_once_with("feature/branch-x")
            self.mock_provider.create_branch.assert_not_called()
            mock_send.assert_called_once_with(
                event_type="vcs.branch_created",
                source="vcs.github",
                payload={"provider": "github", "branch": "feature/branch-x", "base": "main"}
            )

    @patch("urllib.request.urlopen")
    def test_find_existing_pull_request_api_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps([
            {
                "number": 15,
                "html_url": "https://github.com/owner/repo/pull/15",
                "head": {"ref": "feature/branch-x", "sha": "head-sha-123"},
                "base": {"ref": "main"},
                "state": "open"
            }
        ]).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        pr = self.capability.find_existing_pull_request("feature/branch-x", "main")
        self.assertIsNotNone(pr)
        self.assertEqual(pr["number"], 15)
        self.assertEqual(pr["html_url"], "https://github.com/owner/repo/pull/15")
        self.assertEqual(pr["state"], "open")

    @patch("urllib.request.urlopen")
    @patch("subprocess.run")
    def test_find_existing_pull_request_cli_fallback(self, mock_run, mock_urlopen):
        # API fails, CLI succeeds
        mock_urlopen.side_effect = Exception("API error")
        
        mock_cli_resp = MagicMock()
        mock_cli_resp.returncode = 0
        mock_cli_resp.stdout = json.dumps([
            {
                "number": 16,
                "url": "https://github.com/owner/repo/pull/16",
                "headRefName": "feature/branch-x",
                "baseRefName": "main"
            }
        ])
        mock_run.return_value = mock_cli_resp

        pr = self.capability.find_existing_pull_request("feature/branch-x", "main")
        self.assertIsNotNone(pr)
        self.assertEqual(pr["number"], 16)
        self.assertEqual(pr["html_url"], "https://github.com/owner/repo/pull/16")
        self.assertEqual(pr["state"], "open")
        mock_run.assert_called_once()

    @patch("prismatic.capabilities.vcs_github.send_event_via_socket")
    def test_open_pull_request_existing(self, mock_send):
        # Setup: existing PR found
        with patch.object(self.capability, "find_existing_pull_request") as mock_find:
            mock_find.return_value = {
                "number": 15,
                "html_url": "https://github.com/owner/repo/pull/15",
                "state": "open"
            }
            with patch("prismatic.capabilities.vcs_github._HAS_IPC", True):
                pr = self.capability.open_pull_request("PR Title", "PR Body", "feature/branch-x", "main")
                self.assertIsNotNone(pr)
                self.assertEqual(pr["number"], 15)
                self.mock_provider.open_pull_request.assert_not_called()
                mock_send.assert_called_once_with(
                    event_type="vcs.pr_opened",
                    source="vcs.github",
                    payload={"provider": "github", "pr_number": 15, "url": "https://github.com/owner/repo/pull/15"}
                )

    @patch("prismatic.capabilities.vcs_github.send_event_via_socket")
    def test_open_pull_request_new(self, mock_send):
        # Setup: no existing PR, creation succeeds
        with patch.object(self.capability, "find_existing_pull_request") as mock_find:
            mock_find.return_value = None
            self.mock_provider.open_pull_request.return_value = {
                "number": 20,
                "html_url": "https://github.com/owner/repo/pull/20"
            }
            
            with patch("prismatic.capabilities.vcs_github._HAS_IPC", True):
                pr = self.capability.open_pull_request("PR Title", "PR Body", "feature/branch-x", "main")
                self.assertIsNotNone(pr)
                self.assertEqual(pr["number"], 20)
                self.mock_provider.open_pull_request.assert_called_once_with(
                    "PR Title", "PR Body", "feature/branch-x", "main"
                )
                mock_send.assert_called_once_with(
                    event_type="vcs.pr_opened",
                    source="vcs.github",
                    payload={"provider": "github", "pr_number": 20, "url": "https://github.com/owner/repo/pull/20"}
                )

    @patch("urllib.request.urlopen")
    def test_check_comment_exists_api_true(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps([
            {"body": "Expected Comment Body\n"}
        ]).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        exists = self.capability.check_comment_exists(15, "Expected Comment Body")
        self.assertTrue(exists)

    @patch("prismatic.capabilities.vcs_github.send_event_via_socket")
    def test_add_pr_comment_existing(self, mock_send):
        with patch.object(self.capability, "check_comment_exists", return_value=True):
            with patch("prismatic.capabilities.vcs_github._HAS_IPC", True):
                res = self.capability.add_pr_comment(15, "Expected Comment Body")
                self.assertTrue(res)
                self.mock_provider.add_pr_comment.assert_not_called()
                mock_send.assert_called_once_with(
                    event_type="vcs.pr_comment",
                    source="vcs.github",
                    payload={"provider": "github", "pr_number": 15, "body": "Expected Comment Body"}
                )

    @patch("prismatic.capabilities.vcs_github.send_event_via_socket")
    def test_add_pr_comment_new(self, mock_send):
        with patch.object(self.capability, "check_comment_exists", return_value=False):
            self.mock_provider.add_pr_comment.return_value = True
            with patch("prismatic.capabilities.vcs_github._HAS_IPC", True):
                res = self.capability.add_pr_comment(15, "Expected Comment Body")
                self.assertTrue(res)
                self.mock_provider.add_pr_comment.assert_called_once_with(15, "Expected Comment Body")
                mock_send.assert_called_once_with(
                    event_type="vcs.pr_comment",
                    source="vcs.github",
                    payload={"provider": "github", "pr_number": 15, "body": "Expected Comment Body"}
                )

    @patch("urllib.request.urlopen")
    def test_get_pr_details_api(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "state": "open",
            "head": {"sha": "head-sha-xyz"}
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        details = self.capability.get_pr_details(15)
        self.assertEqual(details["state"], "open")
        self.assertEqual(details["head_sha"], "head-sha-xyz")

    @patch("prismatic.capabilities.vcs_github.send_event_via_socket")
    def test_get_pr_status_success_checks(self, mock_send):
        # Setup: details returns sha, checks are successful
        with patch.object(self.capability, "get_pr_details") as mock_details:
            mock_details.return_value = {"state": "open", "head_sha": "sha-123"}
            self.mock_provider.get_pull_request_checks.return_value = [
                {"name": "lint", "status": "completed", "conclusion": "success"}
            ]

            with patch("prismatic.capabilities.vcs_github._HAS_IPC", True):
                status = self.capability.get_pr_status(15)
                self.assertEqual(status["state"], "open")
                self.assertEqual(status["checks"], "success")
                self.mock_provider.get_pull_request_checks.assert_called_once_with("sha-123")
                mock_send.assert_called_once_with(
                    event_type="vcs.pr_status",
                    source="vcs.github",
                    payload={"provider": "github", "pr_number": 15, "state": "open", "checks": "success"}
                )

    @patch("prismatic.capabilities.vcs_github.send_event_via_socket")
    def test_get_pr_status_failure_checks(self, mock_send):
        with patch.object(self.capability, "get_pr_details") as mock_details:
            mock_details.return_value = {"state": "open", "head_sha": "sha-123"}
            self.mock_provider.get_pull_request_checks.return_value = [
                {"name": "lint", "status": "completed", "conclusion": "success"},
                {"name": "test", "status": "completed", "conclusion": "failure"}
            ]

            with patch("prismatic.capabilities.vcs_github._HAS_IPC", True):
                status = self.capability.get_pr_status(15)
                self.assertEqual(status["checks"], "failure")

    @patch("prismatic.capabilities.vcs_github.send_event_via_socket")
    def test_get_pr_status_pending_checks(self, mock_send):
        with patch.object(self.capability, "get_pr_details") as mock_details:
            mock_details.return_value = {"state": "open", "head_sha": "sha-123"}
            self.mock_provider.get_pull_request_checks.return_value = [
                {"name": "lint", "status": "completed", "conclusion": "success"},
                {"name": "test", "status": "in_progress", "conclusion": None}
            ]

            with patch("prismatic.capabilities.vcs_github._HAS_IPC", True):
                status = self.capability.get_pr_status(15)
                self.assertEqual(status["checks"], "pending")
