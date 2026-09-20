"""Unit tests for fail-safe SwarmLock execution supervisor CLI (prismatic exec).

Directive 02 Verification:
- Successful command execution under SwarmLock lease.
- Lock collision deflection returning HTTP 423 without running command.
- Process crash / failure recovery guaranteeing lock release and finished signal.
- Subprocess signal interruption (SIGINT / cancellation) with guaranteed cleanup.
- Pre-commit AST syntax failure on .py resources emitting CRITICAL telemetry and exiting 1.
- CLI argument parsing semantics (--resource, --task, --agent-id, --lease-seconds, --).
"""

import asyncio
import os
import signal
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prismatic.client.exec import run_exec_cli, run_fenced_execution, validate_python_ast
from prismatic.client.interceptor import DualReturn


def test_validate_python_ast_clean():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("def hello():\n    return 'world'\n")
        f_path = f.name

    try:
        errors = validate_python_ast([f_path])
        assert errors == []
    finally:
        if os.path.exists(f_path):
            os.unlink(f_path)


def test_validate_python_ast_syntax_error():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("def broken(:\n    pass\n")
        f_path = f.name

    try:
        errors = validate_python_ast([f_path])
        assert len(errors) == 1
        assert "SyntaxError" in errors[0]
    finally:
        if os.path.exists(f_path):
            os.unlink(f_path)


def test_exec_missing_command():
    res = run_exec_cli([])
    assert res == 1


@pytest.mark.asyncio
async def test_successful_fenced_execution():
    mock_client = MagicMock()
    mock_client.acquire_swarmlock = MagicMock(return_value=DualReturn({"ok": True, "lease_id": "test-lease-123"}))
    mock_client.release_swarmlock = MagicMock(return_value=DualReturn({"ok": True}))
    mock_client.emit_signal = MagicMock(return_value=DualReturn(True))

    with patch("prismatic.client.exec.get_hypervisor_client", return_value=mock_client):
        code = await run_fenced_execution(
            resource="prismatic/mesh/tailscale.py",
            task_id="GRO-4852",
            agent_id="kai",
            command=[sys.executable, "-c", "print('hello from kai')"],
            lease_seconds=60,
            pre_commit=False,
        )

        assert code == 0
        mock_client.acquire_swarmlock.assert_called_once_with(
            resource="prismatic/mesh/tailscale.py",
            agent_id="kai",
            task_id="GRO-4852",
            lease_seconds=60,
        )
        assert mock_client.release_swarmlock.called
        assert mock_client.emit_signal.call_count == 2
        # Check start and finish actions
        calls = [c.kwargs.get("action") for c in mock_client.emit_signal.call_args_list]
        assert "fenced_exec_started" in calls
        assert "fenced_exec_finished" in calls


@pytest.mark.asyncio
async def test_lock_collision_deflection_423():
    mock_client = MagicMock()
    mock_client.acquire_swarmlock = MagicMock(return_value=DualReturn({
        "ok": False,
        "status": "deflected",
        "holder": "george",
        "error": "Resource 'prismatic/mesh/tailscale.py' is currently locked by 'george'",
    }))
    mock_client.release_swarmlock = MagicMock(return_value=DualReturn({"ok": True}))
    mock_client.emit_signal = MagicMock(return_value=DualReturn(True))

    with patch("prismatic.client.exec.get_hypervisor_client", return_value=mock_client):
        code = await run_fenced_execution(
            resource="prismatic/mesh/tailscale.py",
            task_id="GRO-4852",
            agent_id="kai",
            command=[sys.executable, "-c", "print('should not execute')"],
            lease_seconds=60,
        )

        assert code == 423
        # Ensure lock was never released (since it was never acquired) and finished signal not sent
        assert not mock_client.release_swarmlock.called


@pytest.mark.asyncio
async def test_crashing_command_recovery_and_release():
    mock_client = MagicMock()
    mock_client.acquire_swarmlock = MagicMock(return_value=DualReturn({"ok": True, "lease_id": "crash-lease-456"}))
    mock_client.release_swarmlock = MagicMock(return_value=DualReturn({"ok": True}))
    mock_client.emit_signal = MagicMock(return_value=DualReturn(True))

    with patch("prismatic.client.exec.get_hypervisor_client", return_value=mock_client):
        code = await run_fenced_execution(
            resource="prismatic/fleet/manager.py",
            task_id="GRO-4852",
            agent_id="ned",
            command=[sys.executable, "-c", "import sys; sys.exit(7)"],
            lease_seconds=60,
            pre_commit=False,
        )

        assert code == 7
        # Crucial check: release_swarmlock MUST be called in finally block even on crash
        mock_client.release_swarmlock.assert_called_once_with(
            resource="prismatic/fleet/manager.py",
            agent_id="ned",
            task_id="GRO-4852",
            lease_id="crash-lease-456",
        )
        assert mock_client.emit_signal.call_count == 2


@pytest.mark.asyncio
async def test_pre_commit_syntax_validation_failure():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("def syntax_error_func(\n    return 42\n")
        bad_file = f.name

    try:
        mock_client = MagicMock()
        mock_client.acquire_swarmlock = MagicMock(return_value=DualReturn({"ok": True, "lease_id": "syntax-lease-789"}))
        mock_client.release_swarmlock = MagicMock(return_value=DualReturn({"ok": True}))
        mock_client.emit_signal = MagicMock(return_value=DualReturn(True))

        with patch("prismatic.client.exec.get_hypervisor_client", return_value=mock_client):
            code = await run_fenced_execution(
                resource=bad_file,
                task_id="GRO-4852",
                agent_id="fred",
                command=[sys.executable, "-c", "print('subprocess ran')"],
                lease_seconds=60,
                pre_commit=True,
            )

            assert code == 1
            # Verify CRITICAL signal emitted
            critical_signals = [
                c for c in mock_client.emit_signal.call_args_list
                if c.kwargs.get("action") == "pre_commit_syntax_failure"
            ]
            assert len(critical_signals) == 1
            assert critical_signals[0].kwargs.get("severity") == "CRITICAL"
            # Verify lease was STILL released in finally block
            assert mock_client.release_swarmlock.called
    finally:
        if os.path.exists(bad_file):
            os.unlink(bad_file)


@pytest.mark.asyncio
async def test_signal_interruption_cleanup():
    mock_client = MagicMock()
    mock_client.acquire_swarmlock = MagicMock(return_value=DualReturn({"ok": True, "lease_id": "sig-lease-999"}))
    mock_client.release_swarmlock = MagicMock(return_value=DualReturn({"ok": True}))
    mock_client.emit_signal = MagicMock(return_value=DualReturn(True))

    with patch("prismatic.client.exec.get_hypervisor_client", return_value=mock_client):
        # Run a sleep command and cancel it via asyncio task cancellation
        task = asyncio.create_task(
            run_fenced_execution(
                resource="test_resource",
                task_id="GRO-4852",
                agent_id="kai",
                command=[sys.executable, "-c", "import time; time.sleep(10)"],
                lease_seconds=60,
                pre_commit=False,
            )
        )
        await asyncio.sleep(0.2)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        # Guaranteed release in finally:
        assert mock_client.release_swarmlock.called


def test_cli_argument_parsing():
    mock_client = MagicMock()
    mock_client.acquire_swarmlock = MagicMock(return_value=DualReturn({"ok": True, "lease_id": "cli-lease"}))
    mock_client.release_swarmlock = MagicMock(return_value=DualReturn({"ok": True}))
    mock_client.emit_signal = MagicMock(return_value=DualReturn(True))

    with patch("prismatic.client.exec.get_hypervisor_client", return_value=mock_client):
        code = run_exec_cli([
            "--resource", "scratch/test.txt",
            "--task", "GRO-4852",
            "--agent-id", "kai",
            "--lease-seconds", "90",
            "--no-pre-commit",
            "--",
            sys.executable, "-c", "print('cli args test')",
        ])

        assert code == 0
        mock_client.acquire_swarmlock.assert_called_once_with(
            resource="scratch/test.txt",
            agent_id="kai",
            task_id="GRO-4852",
            lease_seconds=90,
        )
        assert mock_client.release_swarmlock.called
