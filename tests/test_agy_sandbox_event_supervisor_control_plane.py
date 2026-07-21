from __future__ import annotations

import importlib.util
import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR_PATH = REPO_ROOT / "scripts" / "agy_sandbox_event_supervisor.py"
SUPERVISOR_HOME = str(Path("/home") / "ubuntu")
AGY_PROFILE_HOME = str(Path("/home") / "ubuntu" / ".hermes" / "profiles" / "kai" / "home")


def _load_supervisor():
    spec = importlib.util.spec_from_file_location(
        "agy_sandbox_event_supervisor_under_test", SUPERVISOR_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_supervisor_default_model_is_agy_accepted_display_label():
    supervisor = _load_supervisor()

    assert supervisor.DEFAULT_MODEL == "Gemini 3.5 Flash (Medium)"


def test_agy_cli_child_env_uses_agy_cli_home_without_mutating_supervisor_home(monkeypatch):
    supervisor = _load_supervisor()
    monkeypatch.setenv("HOME", SUPERVISOR_HOME)
    monkeypatch.setenv("AGY_CLI_HOME", AGY_PROFILE_HOME)

    child_env = supervisor.agy_cli_child_env()

    assert child_env["HOME"] == AGY_PROFILE_HOME
    assert os.environ["HOME"] == SUPERVISOR_HOME
    assert Path.home() == Path(SUPERVISOR_HOME)


def test_preflight_uses_agy_cli_home_and_does_not_spawn_real_agy(monkeypatch):
    supervisor = _load_supervisor()
    monkeypatch.setenv("HOME", SUPERVISOR_HOME)
    monkeypatch.setenv("AGY_CLI_HOME", AGY_PROFILE_HOME)
    calls = []

    class Proc:
        returncode = 0
        stdout = "OK\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return Proc()

    monkeypatch.setattr(supervisor.subprocess, "run", fake_run)

    ok, message = supervisor.preflight_agy_backend("Gemini 3.5 Flash (Medium)")

    assert ok is True
    assert message == "AGY backend OK"
    assert calls == [
        (
            [
                supervisor.AGY_BIN,
                "--print",
                "Reply with exactly: OK",
                "--print-timeout",
                "30s",
                "--model",
                "Gemini 3.5 Flash (Medium)",
            ],
            {
                "capture_output": True,
                "text": True,
                "timeout": 45,
                "env": {**os.environ, "HOME": AGY_PROFILE_HOME},
            },
        )
    ]


def test_agy_launch_and_relaunch_paths_use_child_env_static_guard():
    source = SUPERVISOR_PATH.read_text()

    assert 'env={**os.environ, "HOME": os.environ.get("HOME", str(Path.home()))}' not in source
    assert source.count("env=agy_cli_child_env(),") >= 3
    assert "proc = subprocess.Popen(" in source
    assert "Relaunching with cmd" in source


def test_cron_wrapper_preserves_supervisor_home_and_sets_child_agy_home():
    cron = (REPO_ROOT / "scripts" / "agy_sandbox_event_supervisor_cron.sh").read_text()

    assert f"export HOME={SUPERVISOR_HOME}" in cron
    assert f'export AGY_CLI_HOME="${{AGY_CLI_HOME:-{AGY_PROFILE_HOME}}}"' in cron
    assert "--max-concurrent 3" in cron
