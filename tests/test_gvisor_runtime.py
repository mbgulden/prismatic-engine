"""
Tests for gVisor runtime support in SandboxPodManager.
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from prismatic.plugins.sandbox_pod_manager import SandboxPodManager


class TestGVisorRuntime(unittest.TestCase):

    @patch("subprocess.run")
    def test_detect_gvisor(self, mock_run):
        # Mock runsc --version success
        mock_run.return_value = MagicMock(returncode=0)

        runtime = SandboxPodManager._detect_runtime()
        self.assertEqual(runtime, "gvisor")
        mock_run.assert_called_with(["runsc", "--version"], capture_output=True, text=True, timeout=5)

    @patch("subprocess.run")
    def test_start_pod_gvisor_docker(self, mock_run):
        # Configure manager for gvisor
        mgr = SandboxPodManager(runtime="gvisor", runtime_class="runsc-custom")

        # Mock successful docker run
        mock_run.return_value = MagicMock(returncode=0, stdout="container-123")

        config = {"image": "test-image", "cmd": ["ls"]}
        result = mgr.start_pod("test-plugin", config)

        self.assertEqual(result["runtime"], "gvisor")
        self.assertEqual(result["container_id"], "container-123")

        # Verify docker command contains --runtime runsc-custom
        args, kwargs = mock_run.call_args
        cmd = args[0]
        self.assertIn("docker", cmd)
        self.assertIn("run", cmd)
        self.assertIn("--runtime", cmd)
        self.assertIn("runsc-custom", cmd)

    @patch("subprocess.run")
    def test_start_pod_gvisor_default_class(self, mock_run):
        # Configure manager for gvisor without explicit runtime_class
        mgr = SandboxPodManager(runtime="gvisor")

        # Mock successful docker run
        mock_run.return_value = MagicMock(returncode=0, stdout="container-456")

        config = {"image": "test-image"}
        mgr.start_pod("test-plugin", config)

        # Verify docker command contains --runtime runsc
        args, kwargs = mock_run.call_args
        cmd = args[0]
        self.assertIn("--runtime", cmd)
        self.assertIn("runsc", cmd)

    @patch("subprocess.run")
    def test_start_pod_k3s_with_gvisor_class(self, mock_run):
        # Configure manager for k3s with a gvisor runtime class
        mgr = SandboxPodManager(runtime="k3s", runtime_class="gvisor")

        # Mock successful kubectl run and status check
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="pod created"), # kubectl run
            MagicMock(returncode=0, stdout="Running")     # kubectl get pod status
        ]

        config = {"image": "test-image"}
        mgr.start_pod("test-plugin", config)

        # Verify kubectl command contains --overrides with runtimeClassName
        found_overrides = False
        for i, call in enumerate(mock_run.call_args_list):
            cmd = call[0][0]
            if "--overrides" in cmd:
                found_overrides = True
                overrides_json = cmd[cmd.index("--overrides") + 1]
                overrides = json.loads(overrides_json)
                self.assertEqual(overrides["spec"]["runtimeClassName"], "gvisor")

        self.assertTrue(found_overrides)

if __name__ == "__main__":
    unittest.main()
