
import unittest
import os
import shutil
import tempfile
import json
from unittest.mock import patch, MagicMock
from prismatic.plugins.sandbox_pod_manager import SandboxPodManager, PodState, PodInfo

class TestSandboxPodManagerGVisor(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("subprocess.run")
    def test_runtime_property(self, mock_run):
        # Mock runsc version check
        mock_run.return_value = MagicMock(returncode=0)

        mgr = SandboxPodManager(state_dir=self.tmpdir, runtime="auto")
        self.assertEqual(mgr.runtime, "gvisor")

    def test_runtime_class_persistence(self):
        # Use simulated mode
        mgr = SandboxPodManager(state_dir=self.tmpdir, runtime="none", runtime_class="gvisor-class")
        mgr.start_pod("test-plugin", {})

        # Check PodInfo in memory
        status = mgr.get_pod_status("test-plugin")
        self.assertEqual(status["runtime_class"], "gvisor-class")

        # Check persistence on disk
        state_file = os.path.join(self.tmpdir, "test-plugin.json")
        with open(state_file, "r") as f:
            data = json.load(f)
            self.assertEqual(data["runtime_class"], "gvisor-class")

    @patch("subprocess.run")
    def test_k3s_gvisor_overrides(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="Running")

        mgr = SandboxPodManager(
            state_dir=self.tmpdir,
            runtime="k3s",
            runtime_class="runsc",
            cpu_limit=0.8
        )
        mgr.start_pod("k3s-test", {"image": "python:3.12"})

        # Verify kubectl run command
        calls = [call[0][0] for call in mock_run.call_args_list]
        flattened_calls = []
        for c in calls:
            flattened_calls.extend(c)

        self.assertIn("kubectl", flattened_calls)
        self.assertIn("run", flattened_calls)
        self.assertIn("--limits=cpu=0.8", flattened_calls)
        self.assertIn("--overrides", flattened_calls)

        # Check overrides content
        overrides_found = False
        for i, val in enumerate(flattened_calls):
            if val == "--overrides":
                overrides_json = json.loads(flattened_calls[i+1])
                self.assertEqual(overrides_json["spec"]["runtimeClassName"], "runsc")
                overrides_found = True
        self.assertTrue(overrides_found)

if __name__ == "__main__":
    unittest.main()
