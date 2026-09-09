"""Unit tests for fail-safe SwarmLock execution wrapper CLI (prismatic exec)."""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from prismatic.client.exec import run_exec_cli, validate_python_ast


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


def test_exec_success():
    with patch("prismatic.client.exec.HypervisorClient") as MockClient:
        mock_instance = MagicMock()
        mock_lease = MagicMock()
        mock_instance.acquire_lease.return_value = mock_lease
        MockClient.return_value = mock_instance

        code = run_exec_cli([
            "--resource", "test.py",
            "--task", "TEST-1",
            "--",
            sys.executable, "-c", "print('executing test')",
        ])

        assert code == 0
        assert mock_lease.acquire.called
        assert mock_lease.release.called
        assert mock_instance.emit_signal.called


def test_exec_nonzero_exit():
    with patch("prismatic.client.exec.HypervisorClient") as MockClient:
        mock_instance = MagicMock()
        mock_lease = MagicMock()
        mock_instance.acquire_lease.return_value = mock_lease
        MockClient.return_value = mock_instance

        code = run_exec_cli([
            "--resource", "test.py",
            "--",
            sys.executable, "-c", "import sys; sys.exit(42)",
        ])

        assert code == 42
        # Lease MUST be released even on failure!
        assert mock_lease.release.called


def test_exec_pre_commit_rejection():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write("this is bad python syntax !!!\n")
        f_path = f.name


    try:
        with patch("prismatic.client.exec.HypervisorClient") as MockClient:
            mock_instance = MagicMock()
            mock_lease = MagicMock()
            mock_instance.acquire_lease.return_value = mock_lease
            MockClient.return_value = mock_instance

            code = run_exec_cli([
                "--pre-commit",
                "--resource", f_path,
                "--",
                sys.executable, "-c", "print('should not run')",
            ])

            assert code == 1
            # Subprocess must not run and lease was not acquired
            assert not mock_lease.acquire.called
    finally:
        if os.path.exists(f_path):
            os.unlink(f_path)


def test_exec_timeout():
    with patch("prismatic.client.exec.HypervisorClient") as MockClient:
        mock_instance = MagicMock()
        mock_lease = MagicMock()
        mock_instance.acquire_lease.return_value = mock_lease
        MockClient.return_value = mock_instance

        code = run_exec_cli([
            "--timeout", "1",
            "--",
            sys.executable, "-c", "import time; time.sleep(5)",
        ])

        assert code == 124
        assert mock_lease.release.called
