from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from prismatic.core.governor import DistributedComputeGovernor


class DummyProcess:
    def __init__(self, pid: int, retcodes: list[int | None]):
        self.pid = pid
        self._retcodes = list(retcodes)
        self.terminated = False

    def poll(self):
        if self._retcodes:
            return self._retcodes.pop(0)
        return None

    def terminate(self):
        self.terminated = True


def _status(path: Path) -> dict:
    return json.loads(path.read_text())


def test_acquire_release_and_capacity(tmp_path: Path):
    status_path = tmp_path / "agent_status.json"
    governor = DistributedComputeGovernor(status_path=status_path)

    assert governor.acquire("agy", "GRO-1", "node-a", max_concurrent=1)
    assert not governor.acquire("agy", "GRO-1", "node-a", max_concurrent=1)
    assert governor.acquire("agy", "GRO-1", "node-a", max_concurrent=1, allow_existing=True)
    assert not governor.acquire("agy", "GRO-2", "node-a", max_concurrent=1)

    state = _status(status_path)
    assert state["agents"]["agy"]["status"] == "busy"
    assert len(state["agents"]["agy"]["active_runs"]) == 1

    governor.update_pid("agy", "GRO-1", 12345)
    assert _status(status_path)["agents"]["agy"]["active_runs"][0]["pid"] == 12345

    governor.release("agy", "GRO-1")
    state = _status(status_path)
    assert state["agents"]["agy"]["active_runs"] == []
    assert state["agents"]["agy"]["status"] == "idle"


def test_prune_stale_handles_invalid_and_old_heartbeats(tmp_path: Path):
    status_path = tmp_path / "agent_status.json"
    governor = DistributedComputeGovernor(status_path=status_path)
    assert governor.acquire("jules", "GRO-old", "node-a")
    assert governor.acquire("agy", "GRO-bad", "node-a")

    state = _status(status_path)
    state["agents"]["jules"]["active_runs"][0]["heartbeat"] = "2000-01-01T00:00:00+00:00"
    state["agents"]["agy"]["active_runs"][0]["heartbeat"] = "not-a-date"
    status_path.write_text(json.dumps(state))

    assert governor.prune_stale(ttl_seconds=60) == 2
    state = _status(status_path)
    assert state["agents"]["jules"]["active_runs"] == []
    assert state["agents"]["agy"]["active_runs"] == []


def test_release_by_pid_returns_removed_count(tmp_path: Path):
    status_path = tmp_path / "agent_status.json"
    governor = DistributedComputeGovernor(status_path=status_path)
    assert governor.acquire("codex", "GRO-1", "node-a")
    governor.update_pid("codex", "GRO-1", 777)

    assert governor.release_by_pid(777) == 1
    assert governor.release_by_pid(777) == 0
    assert _status(status_path)["agents"]["codex"]["status"] == "idle"


def test_concurrent_acquire_respects_capacity(tmp_path: Path):
    status_path = tmp_path / "agent_status.json"
    start_path = tmp_path / "start"
    script = """
import sys, time
from pathlib import Path
from prismatic.core.governor import DistributedComputeGovernor
path, task, start = sys.argv[1], sys.argv[2], Path(sys.argv[3])
while not start.exists():
    time.sleep(0.01)
g = DistributedComputeGovernor(status_path=path)
print(g.acquire('agy', task, 'worker', max_concurrent=2), flush=True)
"""
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(status_path), f"GRO-{idx}", str(start_path)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for idx in range(5)
    ]
    start_path.write_text("go")
    results = [proc.communicate(timeout=10) for proc in procs]
    assert all(proc.returncode == 0 for proc in procs), [stderr for _, stderr in results]
    stdout = [out.strip() for out, _ in results]
    assert stdout.count("True") == 2
    assert stdout.count("False") == 3
    assert len(_status(status_path)["agents"]["agy"]["active_runs"]) == 2


def test_dispatcher_tracks_and_releases_finished_process(tmp_path: Path, monkeypatch):
    from prismatic import dispatcher

    governor = DistributedComputeGovernor(status_path=tmp_path / "agent_status.json")
    monkeypatch.setattr(dispatcher, "_governor", governor)
    monkeypatch.setattr(dispatcher, "_active_processes", {})

    assert governor.acquire("agy", "GRO-1", "node-a")
    proc = DummyProcess(pid=4242, retcodes=[None, 0])
    dispatcher._track_agent_process("agy", "GRO-1", proc)  # type: ignore[arg-type]

    assert dispatcher.check_active_processes() == 0
    first_heartbeat = _status(governor.status_path)["agents"]["agy"]["active_runs"][0]["heartbeat"]
    time.sleep(0.01)
    assert dispatcher.check_active_processes() == 1
    state = _status(governor.status_path)
    assert state["agents"]["agy"]["active_runs"] == []
    assert first_heartbeat


def test_dispatcher_duplicate_same_task_launch_is_deferred(tmp_path: Path, monkeypatch):
    from prismatic import dispatcher

    governor = DistributedComputeGovernor(status_path=tmp_path / "agent_status.json")
    monkeypatch.setattr(dispatcher, "_governor", governor)
    monkeypatch.setattr(dispatcher, "_active_processes", {})
    monkeypatch.setattr(dispatcher, "CODEX_PATH", sys.executable)

    first = dispatcher.launch_codex("GRO-DUP")
    assert first is not None
    try:
        second = dispatcher.launch_codex("GRO-DUP")
        assert second is None
        assert len(_status(governor.status_path)["agents"]["codex"]["active_runs"]) == 1
    finally:
        first.terminate()
        first.wait(timeout=5)
        governor.release("codex", "GRO-DUP")


def test_finalize_launch_failure_terminates_and_releases(tmp_path: Path, monkeypatch):
    from prismatic import dispatcher

    governor = DistributedComputeGovernor(status_path=tmp_path / "agent_status.json")
    assert governor.acquire("agy", "GRO-track", "node-a")
    monkeypatch.setattr(dispatcher, "_governor", governor)
    monkeypatch.setattr(dispatcher, "_active_processes", {})

    def fail_update_pid(agent_name: str, task_id: str, pid: int) -> None:
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(governor, "update_pid", fail_update_pid)
    proc = DummyProcess(pid=5150, retcodes=[None])

    assert not dispatcher._finalize_agent_launch("agy", "GRO-track", proc)  # type: ignore[arg-type]
    assert proc.terminated
    assert _status(governor.status_path)["agents"]["agy"]["active_runs"] == []


def test_finalize_launch_failure_force_kills_sigterm_resistant_child(tmp_path: Path, monkeypatch):
    from prismatic import dispatcher

    governor = DistributedComputeGovernor(status_path=tmp_path / "agent_status.json")
    assert governor.acquire("agy", "GRO-resist", "node-a")
    monkeypatch.setattr(dispatcher, "_governor", governor)
    monkeypatch.setattr(dispatcher, "_active_processes", {})

    def fail_update_pid(agent_name: str, task_id: str, pid: int) -> None:
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(governor, "update_pid", fail_update_pid)
    script = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"
    proc = subprocess.Popen([sys.executable, "-c", script])
    try:
        assert not dispatcher._finalize_agent_launch("agy", "GRO-resist", proc)
        assert proc.poll() is not None
        assert _status(governor.status_path)["agents"]["agy"]["active_runs"] == []
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_dispatch_once_prunes_before_dispatch(tmp_path: Path, monkeypatch):
    from prismatic import dispatcher

    events: list[str] = []

    class FakeGovernor:
        def prune_stale(self, *, ttl_seconds: int = 300) -> int:
            events.append("prune")
            return 1

    def fake_dispatch_local_tasks(*args, **kwargs):
        events.append("local")
        return 0

    monkeypatch.setattr(dispatcher, "_governor", FakeGovernor())
    monkeypatch.setattr(dispatcher, "dispatch_local_tasks", fake_dispatch_local_tasks)
    monkeypatch.setattr(dispatcher, "setup_pipeline_issues", list)
    monkeypatch.setattr(dispatcher, "AGENT_CONFIG", {})
    monkeypatch.setattr(dispatcher, "cleanup_stale_agy", lambda max_age_minutes=5: 0)
    monkeypatch.setattr(dispatcher, "recover_stalled_agy", lambda max_retries=0: None)
    monkeypatch.setattr(dispatcher, "detect_origin_completions", lambda dedup, cycle_id: 0)

    class DummyDedup:
        pass

    counts = dispatcher.dispatch_once(DummyDedup(), pipelines={"pipelines": {}})  # type: ignore[arg-type]
    assert events[:2] == ["prune", "local"]
    assert counts["governor_pruned"] == 1
