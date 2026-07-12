import unittest
from unittest.mock import patch, MagicMock, mock_open
import subprocess
import os

from prismatic.agents.sandbox import SandboxAgent
from prismatic.agents.base import AgentConfig
from prismatic.providers.tasks.base import Issue

class TestSandboxAgent(unittest.TestCase):
    def setUp(self):
        self.config = AgentConfig(executable="sandbox", mode="sandbox")
        self.agent_config = {
            "image": "test-image",
            "use_gvisor": True,
            "workspace_path": "/tmp/test-workspace",
            "cmd": ["test-cmd", "--arg"]
        }
        self.issue = Issue(id="TEST-1", identifier="TEST-1", title="Test Issue")

    @patch("subprocess.run")
    @patch("subprocess.Popen")
    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open)
    @patch("pathlib.Path.mkdir")
    def test_execute_with_gvisor(self, mock_mkdir, mock_open_file, mock_exists, mock_popen, mock_run):
        # Setup mocks
        mock_exists.return_value = True

        # Mock runsc --version to simulate gVisor available
        mock_run.return_value = MagicMock(returncode=0)

        agent = SandboxAgent(self.config, self.agent_config)

        with patch.dict(os.environ, {"PRISMATIC_HOME": "/home/test"}):
            proc = agent.execute(self.issue)

        self.assertIsNotNone(proc)

        # Verify Popen was called with --runtime runsc and the correct command
        args, kwargs = mock_popen.call_args
        cmd = args[0]
        self.assertIn("--runtime", cmd)
        self.assertIn("runsc", cmd)
        self.assertIn("test-image", cmd)
        self.assertIn("test-cmd", cmd)
        self.assertIn("--arg", cmd)
        self.assertIn("-v", cmd)
        self.assertIn("/tmp/test-workspace:/workspace", cmd)

    @patch("subprocess.run")
    @patch("subprocess.Popen")
    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open)
    @patch("pathlib.Path.mkdir")
    def test_execute_without_gvisor(self, mock_mkdir, mock_open_file, mock_exists, mock_popen, mock_run):
        # Setup mocks
        mock_exists.return_value = True

        # Mock runsc --version to simulate gVisor NOT available
        mock_run.side_effect = FileNotFoundError()

        agent = SandboxAgent(self.config, self.agent_config)

        proc = agent.execute(self.issue)

        self.assertIsNotNone(proc)

        # Verify Popen was called WITHOUT --runtime runsc
        args, kwargs = mock_popen.call_args
        cmd = args[0]
        self.assertNotIn("--runtime", cmd)
        self.assertNotIn("runsc", cmd)


class TestSandboxVolumeValidation(unittest.TestCase):
    def test_validate_volume_mount_sensitive_paths(self):
        from prismatic.plugins.sandbox_pod_manager import SandboxPodManager, PodManagerError
        from pathlib import Path
        
        home = Path.home().resolve()
        sensitive_paths = [
            home / ".ssh",
            home / ".aws",
            home / ".kube",
            home / ".gemini",
            home / "mounts",
            Path("/home/ubuntu/.ssh"),
            Path("/home/ubuntu/.aws"),
            Path("/home/ubuntu/.kube"),
            Path("/home/ubuntu/.gemini"),
            Path("/home/ubuntu/mounts"),
        ]
        
        for p in sensitive_paths:
            # Check exact path
            with self.assertRaises(PodManagerError):
                SandboxPodManager._validate_volume_mount(f"{p}:/container")
            # Check sub-path
            with self.assertRaises(PodManagerError):
                SandboxPodManager._validate_volume_mount(f"{p}/subpath:/container")

    def test_validate_volume_mount_allowed_paths(self):
        from prismatic.plugins.sandbox_pod_manager import SandboxPodManager
        # Legitimate paths should pass
        res = SandboxPodManager._validate_volume_mount("/tmp/allowed_path:/container")
        self.assertEqual(res, "/tmp/allowed_path:/container")


if __name__ == "__main__":
    unittest.main()
