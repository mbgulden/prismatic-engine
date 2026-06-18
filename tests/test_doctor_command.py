import unittest
from unittest.mock import patch, MagicMock
import argparse
from prismatic.dispatcher import cmd_doctor


class TestDoctorCommand(unittest.TestCase):
    """Test suite for the doctor subcommand diagnostics execution."""

    @patch("prismatic.providers.github.GitHubProvider")
    @patch("subprocess.run")
    def test_cmd_doctor_run_all(self, mock_run, mock_github_provider_cls):
        # Setup mock run for git & gh version checks
        mock_run.return_value = MagicMock(returncode=0, stdout="mock-version\n")

        # Setup mock GitHubProvider instance
        mock_provider = mock_github_provider_cls.return_value
        mock_provider.has_credentials.return_value = True
        mock_provider.verify_auth.return_value = (True, {"login": "test-user", "name": "Test User"}, ["repo"])
        mock_provider.verify_repo_access.return_value = (True, {"name": "mock-repo", "permissions": {"push": True}})
        mock_provider.repo = "owner/repo"

        # Construct argparse namespace
        args = argparse.Namespace(provider=None)

        with patch("sys.stdout") as mock_stdout:
            exit_code = cmd_doctor(args)
            self.assertEqual(exit_code, 0)

    @patch("prismatic.providers.github.GitHubProvider")
    @patch("subprocess.run")
    def test_cmd_doctor_github_only(self, mock_run, mock_github_provider_cls):
        mock_run.return_value = MagicMock(returncode=0, stdout="mock-version\n")

        mock_provider = mock_github_provider_cls.return_value
        mock_provider.has_credentials.return_value = True
        mock_provider.verify_auth.return_value = (True, {"login": "test-user", "name": "Test User"}, ["repo"])
        mock_provider.verify_repo_access.return_value = (True, {"name": "mock-repo", "permissions": {"push": True}})
        mock_provider.repo = "owner/repo"

        args = argparse.Namespace(provider="github")

        with patch("sys.stdout") as mock_stdout:
            exit_code = cmd_doctor(args)
            self.assertEqual(exit_code, 0)
