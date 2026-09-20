"""
TDD Tests for prismatic-lock CLI commands backed by Swarmlock v0.2.0.
"""

import time
import pytest
from pathlib import Path
import prismatic.lock as lock_module


def test_cli_lock_and_unlock_flow(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("PRISMATIC_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    # Claim file
    res = lock_module.cmd_lock("src/server.py", "agent-fred")
    assert res == 0

    # Lock conflict by another agent
    res_conflict = lock_module.cmd_lock("src/server.py", "agent-kai")
    assert res_conflict == 1

    # Heartbeat refresh by owner
    res_hb = lock_module.cmd_heartbeat("src/server.py", "agent-fred")
    assert res_hb == 0

    # Unlock file
    res_unlock = lock_module.cmd_unlock("src/server.py", "agent-fred")
    assert res_unlock == 0

    # Verify status is empty
    res_status = lock_module.cmd_status()
    assert res_status == 0
