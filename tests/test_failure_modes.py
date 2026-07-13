# SPDX-License-Identifier: AGPL-3.0-only
import unittest
import os
import json
import tempfile
import shutil
from unittest.mock import MagicMock, patch, mock_open
from pathlib import Path

import sys
# Add prismatic-engine to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "prismatic-engine")))

# Mock redis BEFORE importing anything that might use it
sys.modules["redis"] = MagicMock()

from prismatic.providers.signals.base import SignalPayload, SignalAction
from prismatic.providers.signals.file import FileSignalProvider
from prismatic.providers.signals.http import HTTPSignalProvider
from prismatic.providers.signals.redis import RedisSignalProvider
from prismatic.sandbox.docker import DockerPodManager
from prismatic.sandbox.gvisor import GVisorPodManager
from prismatic.sandbox.k3s import K3sPodManager
from prismatic.sandbox.base import PodManagerError

class TestFailureModes(unittest.TestCase):

    # ── FileSignalProvider Tests ──────────────────────────────────────────────

    def test_file_provider_disk_full(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = FileSignalProvider(directory=tmpdir)
            payload = SignalPayload(target="test", action=SignalAction.WORK)
            
            with patch("os.rename", side_effect=OSError("No space left on device")):
                success = provider.send("test", payload)
                self.assertFalse(success)

    def test_file_provider_permission_denied(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = FileSignalProvider(directory=tmpdir)
            payload = SignalPayload(target="test", action=SignalAction.WORK)
            
            with patch("tempfile.NamedTemporaryFile", side_effect=OSError("Permission denied")):
                success = provider.send("test", payload)
                self.assertFalse(success)

    def test_file_provider_corrupt_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = FileSignalProvider(directory=tmpdir)
            nudge_path = Path(tmpdir) / "nudge-test"
            nudge_path.write_text("NOT JSON")
            
            # Should return None and clean up the file
            payload = provider.poll("test")
            self.assertIsNone(payload)
            self.assertFalse(nudge_path.exists())

    def test_file_provider_lock_contention(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = FileSignalProvider(directory=tmpdir)
            nudge_path = Path(tmpdir) / "nudge-test"
            nudge_path.write_text('{"signal_id": "123", "target": "test"}')
            
            with patch("fcntl.flock", side_effect=BlockingIOError()):
                with self.assertRaises(BlockingIOError):
                    provider.poll("test")

    # ── HTTPSignalProvider Tests ──────────────────────────────────────────────

    def test_http_provider_no_endpoint(self):
        provider = HTTPSignalProvider(endpoints={})
        payload = SignalPayload(target="unknown", action=SignalAction.WORK)
        success = provider.send("unknown", payload)
        self.assertFalse(success)

    @patch("urllib.request.urlopen")
    def test_http_provider_404_error(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 404
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp
        
        provider = HTTPSignalProvider(endpoints={"test": "http://localhost/signals"})
        payload = SignalPayload(target="test", action=SignalAction.WORK)
        success = provider.send("test", payload)
        self.assertFalse(success)
        self.assertEqual(mock_urlopen.call_count, 1) # No retry on 4xx

    @patch("urllib.request.urlopen")
    @patch("time.sleep")
    def test_http_provider_500_retry(self, mock_sleep, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp
        
        provider = HTTPSignalProvider(endpoints={"test": "http://localhost/signals"}, max_retries=2)
        payload = SignalPayload(target="test", action=SignalAction.WORK)
        success = provider.send("test", payload)
        self.assertFalse(success)
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("urllib.request.urlopen")
    def test_http_provider_timeout(self, mock_urlopen):
        mock_urlopen.side_effect = TimeoutError("Request timed out")
        
        provider = HTTPSignalProvider(endpoints={"test": "http://localhost/signals"}, max_retries=1)
        payload = SignalPayload(target="test", action=SignalAction.WORK)
        success = provider.send("test", payload)
        self.assertFalse(success)

    # ── RedisSignalProvider Tests ─────────────────────────────────────────────

    @patch("redis.Redis")
    def test_redis_provider_connection_failure(self, mock_redis_cls):
        mock_redis = mock_redis_cls.return_value
        mock_redis.ping.side_effect = Exception("Connection refused")
        
        provider = RedisSignalProvider()
        # client property should trigger ping and fail
        payload = SignalPayload(target="test", action=SignalAction.WORK)
        success = provider.send("test", payload)
        self.assertFalse(success)

    @patch("redis.Redis")
    def test_redis_provider_malformed_payload(self, mock_redis_cls):
        mock_redis = mock_redis_cls.return_value
        mock_redis.scan_iter.return_value = ["prismatic:pending:test:123"]
        mock_redis.get.return_value = "NOT JSON"
        
        provider = RedisSignalProvider()
        payload = provider.poll("test")
        self.assertIsNone(payload)
        mock_redis.delete.assert_called_with("prismatic:pending:test:123")

    @patch("redis.Redis")
    def test_redis_provider_poll_timeout(self, mock_redis_cls):
        mock_redis = mock_redis_cls.return_value
        mock_redis.scan_iter.return_value = []
        
        mock_pubsub = MagicMock()
        mock_pubsub.get_message.return_value = None
        mock_redis.pubsub.return_value = mock_pubsub
        
        provider = RedisSignalProvider()
        payload = provider.poll("test", timeout=0.1)
        self.assertIsNone(payload)

    # ── DockerPodManager Tests ───────────────────────────────────────────────

    @patch("shutil.which")
    def test_docker_manager_binary_missing(self, mock_which):
        mock_which.return_value = None
        manager = DockerPodManager()
        with self.assertRaises(PodManagerError) as cm:
            manager.create_pod("test", "ubuntu")
        self.assertIn("binary not found", str(cm.exception))

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_docker_manager_daemon_unreachable(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/docker"
        mock_run.return_value = MagicMock(returncode=1, stderr="Cannot connect to Docker daemon")
        manager = DockerPodManager()
        self.assertFalse(manager.health_check())

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_docker_manager_exec_failure(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/docker"
        # First call for which, second for exec
        mock_run.return_value = MagicMock(returncode=1, stderr="Exec failed", stdout="")
        manager = DockerPodManager()
        stdout, stderr, exit_code = manager.exec_pod("pod1", ["ls"])
        self.assertEqual(exit_code, 1)
        self.assertEqual(stderr, "Exec failed")

    # ── GVisorPodManager Tests ───────────────────────────────────────────────

    @patch("shutil.which")
    def test_gvisor_manager_no_runsc(self, mock_which):
        # Docker not available, runsc not available
        mock_which.return_value = None
        manager = GVisorPodManager(use_docker=False)
        with self.assertRaises(PodManagerError) as cm:
            manager.create_pod("test", "/tmp/rootfs")
        self.assertIn("runsc binary not available", str(cm.exception))

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_gvisor_fallback_logic(self, mock_which, mock_run):
        # mock_which called for docker
        mock_which.side_effect = lambda x: "/usr/bin/docker" if x == "docker" else None
        # mock_run called for docker info
        mock_run.return_value = MagicMock(returncode=0, stdout='{"runsc": {}}')
        
        manager = GVisorPodManager()
        self.assertTrue(manager.use_docker)
        
        # Now mock no runsc in docker
        mock_run.return_value = MagicMock(returncode=0, stdout='{}')
        manager = GVisorPodManager()
        self.assertFalse(manager.use_docker)

    # ── K3sPodManager Tests ──────────────────────────────────────────────────

    @patch("shutil.which")
    def test_k3s_manager_no_binaries(self, mock_which):
        mock_which.return_value = None
        manager = K3sPodManager()
        # health_check calls _kubectl which checks shutil.which
        # But we need to make sure PodManagerError is raised if it fails
        with self.assertRaises(PodManagerError):
            manager.create_pod("test", "ubuntu")

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_k3s_manager_pod_timeout(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/k3s"
        # 1. kubectl run success
        # 2. kubectl get pod returns Pending
        mock_run.side_effect = [
            MagicMock(returncode=0), # run
            MagicMock(returncode=0, stdout="Pending"), # get pod
        ] * 40 # repeat enough times
        
        manager = K3sPodManager()
        with patch("time.sleep"): # don't actually sleep
            with self.assertRaises(PodManagerError) as cm:
                manager.create_pod("test", "ubuntu")
        self.assertIn("did not reach Running state", str(cm.exception))

    # ── Additional Tests to reach 20+ ────────────────────────────────────────

    def test_file_provider_missing_dir(self):
        # Test directory creation
        with tempfile.TemporaryDirectory() as tmpdir:
            subpath = os.path.join(tmpdir, "deep", "dir")
            provider = FileSignalProvider(directory=subpath)
            self.assertTrue(os.path.exists(subpath))

    @patch("urllib.request.urlopen")
    def test_http_provider_500_no_retry_if_max_0(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp
        
        # HTTPSignalProvider doesn't actually use range(0) correctly if max_retries is 0?
        # Let's check http.py: range(1, self._max_retries + 1)
        # If max_retries=0, range(1, 1) is empty.
        
        provider = HTTPSignalProvider(endpoints={"test": "http://localhost/signals"}, max_retries=0)
        payload = SignalPayload(target="test", action=SignalAction.WORK)
        success = provider.send("test", payload)
        self.assertFalse(success)
        self.assertEqual(mock_urlopen.call_count, 0)

    @patch("redis.Redis")
    def test_redis_provider_send_failure(self, mock_redis_cls):
        mock_redis = mock_redis_cls.return_value
        mock_redis.publish.side_effect = Exception("Redis down")
        
        provider = RedisSignalProvider()
        payload = SignalPayload(target="test", action=SignalAction.WORK)
        success = provider.send("test", payload)
        self.assertFalse(success)

    @patch("subprocess.run")
    @patch("shutil.which")
    def test_docker_manager_inspect_failure(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/docker"
        # run success, but inspect fails
        mock_run.side_effect = [
            MagicMock(returncode=0), # run
            MagicMock(returncode=1, stderr="No such object"), # inspect
        ]
        manager = DockerPodManager()
        with self.assertRaises(PodManagerError) as cm:
            manager.create_pod("test", "ubuntu")
        self.assertIn("docker inspect test failed", str(cm.exception))

    @patch("subprocess.run")
    def test_gvisor_manager_create_bundle_failure(self, mock_run):
        # Direct mode
        manager = GVisorPodManager(use_docker=False)
        # Mock runsc version check
        with patch.object(GVisorPodManager, 'check_runsc_available', return_value=True):
             with self.assertRaises(PodManagerError) as cm:
                 # Missing config.json in rootfs
                 manager.create_pod("test", "/tmp/empty_rootfs")
             self.assertIn("missing config.json", str(cm.exception))

if __name__ == "__main__":
    unittest.main()
