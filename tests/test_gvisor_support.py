"""
Tests for gVisor support in SandboxPodManager.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch
from prismatic.plugins.sandbox_pod_manager import SandboxPodManager

class TestGVisorSupport(unittest.TestCase):
    """Verify that gVisor support correctly applies runtime settings."""

    @patch("prismatic.plugins.sandbox_pod_manager.subprocess.run")
    @patch("prismatic.plugins.sandbox_pod_manager.shutil.which")
    def test_gvisor_docker_runtime_flag(self, mock_which, mock_run):
        """gVisor on Docker should use --runtime runsc."""
        # Setup mocks
        mock_which.side_effect = lambda x: "/usr/bin/" + x
        mock_run.return_value = MagicMock(returncode=0, stdout="container-id-123")

        mgr = SandboxPodManager(runtime="gvisor")
        mgr.start_pod("test-plugin", {"image": "python:3.12-slim"})

        # Check docker run call
        args, kwargs = mock_run.call_args
        cmd = args[0]
        self.assertIn("--runtime", cmd)
        self.assertIn("runsc", cmd)

    @patch("prismatic.plugins.sandbox_pod_manager.subprocess.run")
    @patch("prismatic.plugins.sandbox_pod_manager.shutil.which")
    def test_gvisor_k3s_runtime_class(self, mock_which, mock_run):
        """gVisor on k3s should use --overrides with runtimeClassName."""
        # Setup mocks: No docker, but kubectl exists
        mock_which.side_effect = lambda x: "/usr/bin/kubectl" if x == "kubectl" else None

        # mock_run needs to handle:
        # 1. runsc --version (in _detect_runtime)
        # 2. kubectl run (in _start_k3s)
        # 3. kubectl get pod (in _start_k3s wait loop)

        def mock_run_side_effect(cmd, *args, **kwargs):
            if cmd[0] == "runsc":
                return MagicMock(returncode=0)
            if cmd[0] == "kubectl" and cmd[1] == "run":
                return MagicMock(returncode=0, stdout="pod-started")
            if cmd[0] == "kubectl" and cmd[1] == "get":
                return MagicMock(returncode=0, stdout="Running")
            return MagicMock(returncode=0)

        mock_run.side_effect = mock_run_side_effect

        mgr = SandboxPodManager(runtime="gvisor")
        mgr.start_pod("test-plugin", {"image": "python:3.12-slim"})

        # Check kubectl run call
        found_overrides = False
        for call in mock_run.call_args_list:
            cmd = call[0][0]
            if "kubectl" in cmd and "run" in cmd and "--overrides" in cmd:
                found_overrides = True
                idx = cmd.index("--overrides")
                overrides = json.loads(cmd[idx+1])
                self.assertEqual(overrides["spec"]["runtimeClassName"], "runsc")

        self.assertTrue(found_overrides, "kubectl run should be called with --overrides")

    @patch("prismatic.plugins.sandbox_pod_manager.subprocess.run")
    def test_custom_runtime_class(self, mock_run):
        """Custom runtime_class should be respected."""
        mock_run.return_value = MagicMock(returncode=0, stdout="container-id")

        mgr = SandboxPodManager(runtime="docker", runtime_class="custom-gvisor")
        mgr.start_pod("test-plugin", {})

        args, kwargs = mock_run.call_args
        cmd = args[0]
        self.assertIn("--runtime", cmd)
        self.assertIn("custom-gvisor", cmd)

if __name__ == "__main__":
    unittest.main()
