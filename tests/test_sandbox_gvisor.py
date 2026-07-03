
import unittest
import json
from unittest.mock import patch, MagicMock
from prismatic.plugins.sandbox_pod_manager import SandboxPodManager

class TestSandboxPodManagerGVisor(unittest.TestCase):
    def setUp(self):
        # Patch detect_runtime to return k3s for these tests
        with patch("prismatic.plugins.sandbox_pod_manager.SandboxPodManager._detect_runtime", return_value="k3s"):
            self.mgr = SandboxPodManager(runtime="k3s", runtime_class="runsc")

    @patch("subprocess.run")
    def test_start_k3s_with_runtime_class(self, mock_run):
        # Mocking the status check loop to return Running immediately
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="pod/test-plugin created"), # kubectl run
            MagicMock(returncode=0, stdout="Running") # kubectl get pod status
        ]

        config = {"image": "test-image", "cmd": ["echo", "hello"]}
        self.mgr.start_pod("test-plugin", config)

        # Verify kubectl run was called with --overrides
        args, kwargs = mock_run.call_args_list[0]
        cmd = args[0]

        self.assertIn("--overrides", cmd)
        overrides_idx = cmd.index("--overrides")
        overrides_json = cmd[overrides_idx + 1]
        overrides = json.loads(overrides_json)

        self.assertEqual(overrides["spec"]["runtimeClassName"], "runsc")

    @patch("subprocess.run")
    def test_start_docker_with_runtime_class(self, mock_run):
        # Patch detect_runtime to return docker
        with patch("prismatic.plugins.sandbox_pod_manager.SandboxPodManager._detect_runtime", return_value="docker"):
            mgr = SandboxPodManager(runtime="docker", runtime_class="custom-runtime")

        mock_run.return_value = MagicMock(returncode=0, stdout="container-id")

        config = {"image": "test-image"}
        mgr.start_pod("test-plugin", config)

        args, kwargs = mock_run.call_args_list[0]
        cmd = args[0]

        self.assertIn("--runtime", cmd)
        self.assertIn("custom-runtime", cmd)

if __name__ == "__main__":
    unittest.main()
