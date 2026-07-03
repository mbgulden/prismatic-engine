import os
import json
import unittest
import tempfile
from pathlib import Path
from prismatic.core.governor import DistributedComputeGovernor

class TestDistributedComputeGovernor(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.status_path = os.path.join(self.test_dir.name, "agent_status.json")
        self.governor = DistributedComputeGovernor(status_path=self.status_path)

    def tearDown(self):
        self.test_dir.cleanup()

    def test_acquire_release_lifecycle(self):
        # 1. Acquire
        success = self.governor.acquire("agy", "TASK-1", "INSTANCE-1", max_concurrent=1)
        self.assertTrue(success)

        status = json.loads(Path(self.status_path).read_text())
        self.assertEqual(len(status["agents"]["agy"]["active_runs"]), 1)
        self.assertEqual(status["agents"]["agy"]["status"], "busy")
        self.assertEqual(status["agents"]["agy"]["active_runs"][0]["task_id"], "TASK-1")

        # 2. Try to acquire again (same task) - should be True
        success = self.governor.acquire("agy", "TASK-1", "INSTANCE-1", max_concurrent=1)
        self.assertTrue(success)
        status = json.loads(Path(self.status_path).read_text())
        self.assertEqual(len(status["agents"]["agy"]["active_runs"]), 1)

        # 3. Try to acquire for different task when at capacity - should be False
        success = self.governor.acquire("agy", "TASK-2", "INSTANCE-1", max_concurrent=1)
        self.assertFalse(success)

        # 4. Update PID
        self.governor.update_pid("agy", "TASK-1", 12345)
        status = json.loads(Path(self.status_path).read_text())
        self.assertEqual(status["agents"]["agy"]["active_runs"][0]["pid"], 12345)

        # 5. Heartbeat
        old_hb = status["agents"]["agy"]["active_runs"][0]["heartbeat"]
        import time
        time.sleep(0.1)
        self.governor.heartbeat("agy", "TASK-1")
        status = json.loads(Path(self.status_path).read_text())
        new_hb = status["agents"]["agy"]["active_runs"][0]["heartbeat"]
        self.assertNotEqual(old_hb, new_hb)

        # 6. Release
        self.governor.release("agy", "TASK-1")
        status = json.loads(Path(self.status_path).read_text())
        self.assertEqual(len(status["agents"]["agy"]["active_runs"]), 0)
        self.assertEqual(status["agents"]["agy"]["status"], "idle")

    def test_concurrency_limit(self):
        # Max concurrent 2
        self.assertTrue(self.governor.acquire("agy", "TASK-1", "INSTANCE-1", max_concurrent=2))
        self.assertTrue(self.governor.acquire("agy", "TASK-2", "INSTANCE-1", max_concurrent=2))
        self.assertFalse(self.governor.acquire("agy", "TASK-3", "INSTANCE-1", max_concurrent=2))

    def test_release_by_pid(self):
        self.governor.acquire("agy", "TASK-1", "INSTANCE-1")
        self.governor.update_pid("agy", "TASK-1", 999)
        self.governor.release_by_pid(999)
        status = json.loads(Path(self.status_path).read_text())
        self.assertEqual(len(status["agents"]["agy"]["active_runs"]), 0)

    def test_prune_stale(self):
        self.governor.acquire("agy", "TASK-1", "INSTANCE-1")
        # Mock old heartbeat by modifying file
        status = json.loads(Path(self.status_path).read_text())
        status["agents"]["agy"]["active_runs"][0]["heartbeat"] = "2000-01-01T00:00:00+00:00"
        Path(self.status_path).write_text(json.dumps(status))

        pruned = self.governor.prune_stale(ttl_seconds=60)
        self.assertEqual(pruned, 1)
        status = json.loads(Path(self.status_path).read_text())
        self.assertEqual(len(status["agents"]["agy"]["active_runs"]), 0)

    def test_concurrent_access(self):
        import threading

        def task(id):
            gov = DistributedComputeGovernor(status_path=self.status_path)
            # Try to acquire many times
            for i in range(10):
                gov.acquire("agy", f"TASK-{id}-{i}", "INST", max_concurrent=100)
                gov.release("agy", f"TASK-{id}-{i}")

        threads = []
        for i in range(5):
            t = threading.Thread(target=task, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        status = json.loads(Path(self.status_path).read_text())
        self.assertEqual(len(status["agents"]["agy"]["active_runs"]), 0)

if __name__ == "__main__":
    unittest.main()
