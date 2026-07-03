import unittest
import json
from unittest.mock import patch, MagicMock
import subprocess
from prismatic.plugins.sandbox_pod_manager import SandboxPodManager, PodState, PodManagerError

class TestSandboxPodManager(unittest.TestCase):
    def setUp(self):
        self.state_dir = "./tmp_test_state"

    @patch("subprocess.run")
    def test_detect_runtime_gvisor(self, mock_run):
        # Mock runsc --version to succeed
        mock_run.return_value = MagicMock(returncode=0)

        runtime = SandboxPodManager._detect_runtime()
        self.assertEqual(runtime, "gvisor")
        # Find runsc call
        mock_run.assert_any_call(["runsc", "--version"], capture_output=True, text=True, timeout=5)

    @patch("subprocess.run")
    def test_detect_runtime_docker_fallback(self, mock_run):
        # Mock runsc to fail, docker to succeed
        def side_effect(cmd, **kwargs):
            if cmd[0] == "runsc":
                raise FileNotFoundError
            if cmd[0] == "docker":
                return MagicMock(returncode=0)
            return MagicMock(returncode=1)

        mock_run.side_effect = side_effect

        runtime = SandboxPodManager._detect_runtime()
        self.assertEqual(runtime, "docker")

    @patch("subprocess.run")
    def test_start_pod_gvisor(self, mock_run):
        # Mock runtime detection to return gvisor
        with patch.object(SandboxPodManager, "_detect_runtime", return_value="gvisor"):
            mgr = SandboxPodManager(state_dir=self.state_dir)

            mock_run.return_value = MagicMock(returncode=0, stdout="container-123")

            config = {"image": "test-image", "cmd": ["echo", "hello"]}
            result = mgr.start_pod("test-pod", config)

            self.assertEqual(result["state"], "RUNNING")
            self.assertEqual(result["runtime"], "gvisor")
            self.assertEqual(result["container_id"], "container-123")

            # Verify docker was called with --runtime runsc
            args, kwargs = mock_run.call_args
            self.assertIn("--runtime", args[0])
            self.assertIn("runsc", args[0])

    @patch("subprocess.run")
    def test_start_pod_k3s_gvisor_override(self, mock_run):
        # Mock runtime detection to return k3s
        with patch.object(SandboxPodManager, "_detect_runtime", return_value="k3s"):
            mgr = SandboxPodManager(state_dir=self.state_dir)

            # Mock kubectl get pod to return Running
            def side_effect(cmd, **kwargs):
                if "get" in cmd:
                    return MagicMock(returncode=0, stdout="Running")
                return MagicMock(returncode=0, stdout="pod/test-pod-k3s created")

            mock_run.side_effect = side_effect

            config = {
                "image": "test-image",
                "runtime": "k3s",
                "runtime_class": "runsc"
            }

            result = mgr.start_pod("test-pod-k3s", config)
            self.assertEqual(result["state"], "RUNNING")

            # Verify kubectl run was called with --overrides containing runtimeClassName
            found_overrides = False
            for call in mock_run.call_args_list:
                args = call[0][0]
                for arg in args:
                    if arg.startswith("--overrides="):
                        overrides = json.loads(arg.split("=", 1)[1])
                        if overrides.get("spec", {}).get("runtimeClassName") == "runsc":
                            found_overrides = True
            self.assertTrue(found_overrides, "Did not find --overrides with runtimeClassName")

if __name__ == "__main__":
    unittest.main()
