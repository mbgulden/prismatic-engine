import unittest
import json
import os
import tempfile
from unittest.mock import patch, MagicMock
from prismatic.plugins.sandbox_pod_manager import SandboxPodManager, PodManagerError, PodState

class TestGVisorSupport(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.pod_mgr = SandboxPodManager(state_dir=self.tmpdir, runtime="gvisor", runtime_class="runsc-custom")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("subprocess.run")
    def test_start_docker_with_gvisor_runtime(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="container123")

        self.pod_mgr.start_pod("test-plugin", {"image": "test-image"})

        # Verify docker run command includes --runtime
        args, kwargs = mock_run.call_args
        cmd = args[0]
        self.assertEqual(cmd[0], "docker")
        self.assertEqual(cmd[1], "run")
        self.assertIn("--runtime", cmd)
        self.assertIn("runsc-custom", cmd)

    @patch("subprocess.run")
    def test_start_k3s_with_runtime_class(self, mock_run):
        k3s_pod_mgr = SandboxPodManager(state_dir=self.tmpdir, runtime="k3s", runtime_class="gvisor")

        # Mocking multiple subprocess calls in _start_k3s
        # 1. kubectl run
        # 2. kubectl get pod (to check Running status)
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="pod created"),
            MagicMock(returncode=0, stdout="Running")
        ]

        k3s_pod_mgr.start_pod("test-plugin", {"image": "test-image"})

        # Verify kubectl run command includes --overrides
        found_overrides = False
        for call in mock_run.call_args_list:
            cmd = call[0][0]
            if "kubectl" in cmd and "run" in cmd:
                for arg in cmd:
                    if arg.startswith("--overrides="):
                        found_overrides = True
                        overrides = json.loads(arg.split("=", 1)[1])
                        self.assertEqual(overrides["spec"]["runtimeClassName"], "gvisor")
        self.assertTrue(found_overrides)

    def test_default_runtime_class_for_gvisor(self):
        with patch.object(SandboxPodManager, "_detect_runtime", return_value="gvisor"):
            mgr = SandboxPodManager(state_dir=self.tmpdir, runtime="auto")
            self.assertEqual(mgr._runtime_class, "runsc")

if __name__ == "__main__":
    unittest.main()
