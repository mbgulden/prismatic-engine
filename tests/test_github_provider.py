import unittest
from unittest.mock import patch, MagicMock
import json
import urllib.error
from prismatic.providers.github import GitHubProvider


class TestGitHubProvider(unittest.TestCase):
    """Test suite for GitHubProvider API connection layer and capabilities."""

    def setUp(self):
        # Initialize with explicit credentials to avoid env/git/config side effects during test setup
        self.provider = GitHubProvider(token="mock-token-xyz", repo="mock-owner/mock-repo")

    def test_has_credentials(self):
        self.assertTrue(self.provider.has_credentials())
        no_cred_provider = GitHubProvider(token="", repo="")
        self.assertFalse(no_cred_provider.has_credentials())

    @patch("urllib.request.urlopen")
    def test_verify_auth_success(self, mock_urlopen):
        # Mock successful user response
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "login": "test-user",
            "name": "Test User Name"
        }).encode("utf-8")
        
        # Mock headers
        mock_resp.info.return_value = {
            "X-OAuth-Scopes": "repo, workflow, write:discussion"
        }
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        success, user_info, scopes = self.provider.verify_auth()
        self.assertTrue(success)
        self.assertEqual(user_info["login"], "test-user")
        self.assertEqual(scopes, ["repo", "workflow", "write:discussion"])

    @patch("urllib.request.urlopen")
    def test_verify_auth_http_error(self, mock_urlopen):
        # Mock HTTPError 401 Unauthorized
        fp = MagicMock()
        fp.read.return_value = json.dumps({"message": "Bad credentials"}).encode("utf-8")
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://api.github.com/user",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=fp
        )

        success, user_info, scopes = self.provider.verify_auth()
        self.assertFalse(success)
        self.assertIn("HTTP 401", user_info["error"])
        self.assertEqual(user_info["detail"]["message"], "Bad credentials")
        self.assertEqual(scopes, [])

    @patch("urllib.request.urlopen")
    def test_verify_repo_access_success(self, mock_urlopen):
        # Mock successful repo response
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "name": "mock-repo",
            "permissions": {"admin": True, "push": True, "pull": True}
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        success, repo_info = self.provider.verify_repo_access()
        self.assertTrue(success)
        self.assertEqual(repo_info["name"], "mock-repo")
        self.assertTrue(repo_info["permissions"]["push"])

    @patch("urllib.request.urlopen")
    def test_get_branch_ref(self, mock_urlopen):
        # Mock branch ref response
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({
            "ref": "refs/heads/main",
            "object": {"sha": "mock-sha-12345"}
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        ref_data = self.provider.get_branch_ref("main")
        self.assertIsNotNone(ref_data)
        self.assertEqual(ref_data["object"]["sha"], "mock-sha-12345")

    @patch("urllib.request.urlopen")
    def test_create_branch_success(self, mock_urlopen):
        # 1. Mock get_branch_ref urlopen (first call)
        mock_resp_ref = MagicMock()
        mock_resp_ref.status = 200
        mock_resp_ref.read.return_value = json.dumps({
            "ref": "refs/heads/main",
            "object": {"sha": "source-sha-abc"}
        }).encode("utf-8")

        # 2. Mock create branch ref urlopen (second call)
        mock_resp_create = MagicMock()
        mock_resp_create.status = 201
        mock_resp_create.read.return_value = b""

        # Return them sequentially
        mock_urlopen.return_value.__enter__.side_effect = [mock_resp_ref, mock_resp_create]

        success = self.provider.create_branch("feature-branch", "main")
        self.assertTrue(success)

    @patch("urllib.request.urlopen")
    def test_open_pull_request(self, mock_urlopen):
        # Mock successful PR creation response
        mock_resp = MagicMock()
        mock_resp.status = 201
        mock_resp.read.return_value = json.dumps({
            "html_url": "https://github.com/mock-owner/mock-repo/pull/1",
            "number": 1
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        pr_info = self.provider.open_pull_request(
            title="Update README",
            body="Describe additions",
            head="feature-branch",
            base="main"
        )
        self.assertIsNotNone(pr_info)
        self.assertEqual(pr_info["number"], 1)

    @patch("urllib.request.urlopen")
    def test_get_pull_request_diff(self, mock_urlopen):
        # Mock successful PR diff response
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"diff --git a/README.md b/README.md"
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        diff_content = self.provider.get_pull_request_diff(1)
        self.assertEqual(diff_content, "diff --git a/README.md b/README.md")

    @patch("urllib.request.urlopen")
    def test_get_pull_request_checks(self, mock_urlopen):
        # Mock successful check runs and statuses response sequentially
        mock_resp_runs = MagicMock()
        mock_resp_runs.status = 200
        mock_resp_runs.read.return_value = json.dumps({
            "check_runs": [
                {
                    "name": "lint",
                    "status": "completed",
                    "conclusion": "success",
                    "html_url": "https://github.com/check/1"
                }
            ]
        }).encode("utf-8")

        mock_resp_statuses = MagicMock()
        mock_resp_statuses.status = 200
        mock_resp_statuses.read.return_value = json.dumps({
            "statuses": [
                {
                    "context": "continuous-integration",
                    "state": "success",
                    "target_url": "https://github.com/status/1"
                }
            ]
        }).encode("utf-8")

        mock_urlopen.return_value.__enter__.side_effect = [mock_resp_runs, mock_resp_statuses]

        checks = self.provider.get_pull_request_checks("mock-ref-sha")
        self.assertEqual(len(checks), 2)
        self.assertEqual(checks[0]["name"], "lint")
        self.assertEqual(checks[0]["type"], "check_run")
        self.assertEqual(checks[1]["name"], "continuous-integration")
        self.assertEqual(checks[1]["type"], "status")

    @patch("urllib.request.urlopen")
    def test_add_pr_comment(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 201
        mock_resp.read.return_value = b""
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        success = self.provider.add_pr_comment(1, "Looks great!")
        self.assertTrue(success)

    @patch("urllib.request.urlopen")
    def test_post_pr_review(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b""
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        success = self.provider.post_pr_review(
            pr_number=1,
            event="APPROVE",
            body="LGTM",
            comments=[{"path": "file.py", "line": 5, "body": "nit"}]
        )
        self.assertTrue(success)

    def test_verify_webhook_signature(self):
        payload = b'{"action": "opened", "number": 1}'
        secret = "super-secret"
        # Hexdigest of HMAC-SHA256 signature for payload using secret
        # X-Hub-Signature-256 header matches this signature
        import hmac
        import hashlib
        mac = hmac.new(secret.encode("utf-8"), msg=payload, digestmod=hashlib.sha256)
        signature = f"sha256={mac.hexdigest()}"

        self.assertTrue(GitHubProvider.verify_webhook_signature(payload, signature, secret))
        self.assertFalse(GitHubProvider.verify_webhook_signature(payload, "wrong-signature", secret))
