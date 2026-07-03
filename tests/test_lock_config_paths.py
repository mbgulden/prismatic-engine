from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path

import prismatic.lock as lock_module


def _load_pre_push_hook():
    repo = Path(__file__).resolve().parents[1]
    path = repo / "scripts" / "pre-push-hook.py"
    spec = importlib.util.spec_from_file_location("pre_push_hook_for_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_lock_module_uses_configured_lock_file_with_expanded_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    configured = home / ".prismatic" / "locks" / "swarm_locks.json"
    repo.mkdir()
    (repo / "PRISMATIC_ENGINE.yaml").write_text(
        'locks:\n  file: "${PRISMATIC_HOME}/.prismatic/locks/swarm_locks.json"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("PRISMATIC_HOME", str(home))
    importlib.reload(lock_module)

    assert lock_module._configured_lock_file(repo) == configured

def test_lock_module_expands_tilde_in_configured_lock_file(tmp_path, monkeypatch):
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "PRISMATIC_ENGINE.yaml").write_text(
        'locks:\n  file: "~/.prismatic/locks.json"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("PRISMATIC_HOME", raising=False)
    importlib.reload(lock_module)

    assert lock_module._configured_lock_file(repo) == home / ".prismatic" / "locks.json"


def test_lock_module_falls_back_to_prismatic_home_without_repo_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("PRISMATIC_HOME", str(home))
    importlib.reload(lock_module)

    assert lock_module._configured_lock_file(repo_root=None) == home / ".antigravity" / "swarm_locks.json"


def test_pre_push_hook_expands_tilde_in_configured_lock_file(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("PRISMATIC_HOME", raising=False)
    hook = _load_pre_push_hook()
    config = {"locks": {"file": "~/.prismatic/hook-locks.json"}}

    assert hook._configured_lock_file(config) == home / ".prismatic" / "hook-locks.json"


def test_pre_push_hook_reads_configured_lock_file(tmp_path, monkeypatch):
    home = tmp_path / "home"
    lock_file = home / "custom" / "locks.json"
    lock_file.parent.mkdir(parents=True)
    lock_file.write_text(json.dumps([{"filePath": "scripts/x.py", "agentId": "fred"}]), encoding="utf-8")
    monkeypatch.setenv("PRISMATIC_HOME", str(home))
    hook = _load_pre_push_hook()
    config = {"locks": {"file": "${PRISMATIC_HOME}/custom/locks.json"}}

    assert hook._configured_lock_file(config) == lock_file
    assert hook._read_locks(config)[0]["agentId"] == "fred"


def test_pre_push_hook_falls_back_to_prismatic_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    lock_file = home / ".antigravity" / "swarm_locks.json"
    lock_file.parent.mkdir(parents=True)
    lock_file.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("PRISMATIC_HOME", str(home))
    hook = _load_pre_push_hook()

    assert hook._configured_lock_file({}) == lock_file
    assert hook._read_locks({}) == []
