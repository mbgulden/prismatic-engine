"""Fail-safe SwarmLock wrapper CLI for Prismatic Engine (`prismatic exec`).

Wraps process execution inside a fail-safe lifecycle:
1. Acquires SwarmLock lease with background heartbeat renewal.
2. Emits real-time telemetry signal to Prismatic Hub.
3. Streams stdout and stderr live with zero buffer lag.
4. Optionally verifies python AST integrity (--pre-commit).
5. Unconditionally releases lease in finally block, with SIGINT/SIGTERM cleanup.
"""

from __future__ import annotations

import argparse
import ast
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Sequence

from prismatic.client.interceptor import HypervisorClient, LeaseContext, SignalPayload

logger = logging.getLogger("prismatic.client.exec")


def validate_python_ast(paths: list[str] | None = None) -> list[str]:
    """Validate python syntax across specified paths and repository status.

    Returns a list of error strings (empty if all clean).
    """
    errors: list[str] = []
    files_to_check: set[Path] = set()

    # 1. Check explicitly passed files
    if paths:
        for p in paths:
            target = Path(p)
            if target.is_file() and target.suffix == ".py":
                files_to_check.add(target)

    # 2. Check git-modified python files in current workspace
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                # Line format: ' M path/to/file.py' or '?? path/to/file.py'
                parts = line.split(maxsplit=1)
                if len(parts) == 2:
                    raw_path = parts[1].strip().strip('"')
                    path_obj = Path(raw_path)
                    if path_obj.suffix == ".py" and path_obj.is_file():
                        files_to_check.add(path_obj)
    except Exception as exc:
        logger.debug("Could not inspect git status for AST validation: %s", exc)

    # 3. Parse AST for each file
    for file_path in sorted(files_to_check):
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            ast.parse(content, filename=str(file_path))
        except SyntaxError as syn_err:
            errors.append(
                f"{file_path}:{syn_err.lineno}:{syn_err.offset}: SyntaxError: {syn_err.msg}"
            )
        except Exception as exc:
            errors.append(f"{file_path}: Error parsing AST: {exc}")

    return errors


def run_exec_cli(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for `prismatic exec`.

    Usage:
        prismatic exec [--resource <file>] [--task <id>] [--timeout <sec>] [--agent <name>] [--pre-commit] -- <command> [args...]
    """
    raw_args = list(argv) if argv is not None else sys.argv[1:]

    # Separate options from command after '--' if present
    flags: list[str] = []
    command: list[str] = []
    if "--" in raw_args:
        idx = raw_args.index("--")
        flags = raw_args[:idx]
        command = raw_args[idx + 1 :]
    else:
        # Find first non-flag argument to treat as command
        i = 0
        while i < len(raw_args):
            arg = raw_args[i]
            if arg in {"-r", "--resource", "-t", "--task", "--timeout", "-a", "--agent"}:
                flags.append(arg)
                if i + 1 < len(raw_args):
                    flags.append(raw_args[i + 1])
                    i += 2
                    continue
            elif arg in {"--pre-commit", "-h", "--help"}:
                flags.append(arg)
                i += 1
                continue
            else:
                command = raw_args[i:]
                break
            i += 1

    parser = argparse.ArgumentParser(
        prog="prismatic exec",
        description="Execute a command wrapped inside a fail-safe SwarmLock lease and telemetry envelope.",
    )
    parser.add_argument(
        "--resource",
        "-r",
        action="append",
        default=None,
        help="Target resource or file path to lock (can specify multiple times or comma-separated)",
    )
    parser.add_argument(
        "--task",
        "-t",
        default=None,
        help="Task identifier (e.g. GRO-3319 or custom task ID)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Maximum execution timeout in seconds (default: 300)",
    )
    parser.add_argument(
        "--agent",
        "-a",
        default="agy",
        help="Agent identifier holding the SwarmLock (default: agy)",
    )
    parser.add_argument(
        "--pre-commit",
        action="store_true",
        help="Verify python AST syntax on target/modified files before and after execution",
    )

    args = parser.parse_args(flags)

    if not command:
        print("[prismatic exec] Error: No command specified to execute. Use: prismatic exec -- <command> [args...]", file=sys.stderr)
        return 1

    # 1. Resolve resources
    resources: list[str] = []
    if args.resource:
        for item in args.resource:
            for piece in item.split(","):
                piece = piece.strip()
                if piece and piece not in resources:
                    resources.append(piece)
    if not resources:
        resources = ["file:workspace"]

    task_id = args.task or f"TASK-EXEC-{int(time.time())}"
    agent_id = args.agent or "agy"
    timeout_sec = max(5, int(args.timeout))

    # 2. Pre-execution AST check
    if args.pre_commit:
        ast_errors = validate_python_ast(resources)
        if ast_errors:
            print("[prismatic exec] 🚫 Pre-commit AST syntax check failed:", file=sys.stderr)
            for err in ast_errors:
                print(f"  {err}", file=sys.stderr)
            return 1

    # 3. Setup client and lease
    client = HypervisorClient()
    lease = client.acquire_lease(paths=resources, ttl=timeout_sec, owner=agent_id, task_id=task_id)

    proc: subprocess.Popen | None = None
    interrupted = False

    def _sig_handler(signum: int, _frame: Any) -> None:
        nonlocal interrupted
        interrupted = True
        sig_name = signal.Signals(signum).name
        print(f"[prismatic exec] Received {sig_name}; terminating process and releasing locks...", file=sys.stderr)
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    prev_sigint = signal.signal(signal.SIGINT, _sig_handler)
    prev_sigterm = signal.signal(signal.SIGTERM, _sig_handler)

    start_time = time.time()
    exit_code = 1

    try:
        # Acquire SwarmLock
        lease.acquire()
        client.emit_signal(
            SignalPayload(
                agent_id=agent_id,
                stage="EXEC_STARTED",
                message=f"prismatic exec started: {' '.join(command)}",
                task_id=task_id,
                metadata={"command": command, "resources": resources, "timeout": timeout_sec},
            )
        )

        # Launch child process
        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        # Stream stdout and stderr live concurrently
        def _stream(pipe: Any, out_stream: Any) -> None:
            try:
                for line in iter(pipe.readline, ''):
                    out_stream.write(line)
                    out_stream.flush()
            except Exception:
                pass
            finally:
                pipe.close()

        stdout_thread = threading.Thread(target=_stream, args=(proc.stdout, sys.stdout), daemon=True)
        stderr_thread = threading.Thread(target=_stream, args=(proc.stderr, sys.stderr), daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        # Wait with timeout
        try:
            exit_code = proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            print(f"[prismatic exec] Command timed out after {timeout_sec}s: {' '.join(command)}", file=sys.stderr)
            proc.kill()
            exit_code = 124
            client.emit_signal(
                SignalPayload(
                    agent_id=agent_id,
                    stage="EXEC_TIMEOUT",
                    message=f"Command timed out after {timeout_sec}s",
                    task_id=task_id,
                    metadata={"command": command, "timeout": timeout_sec},
                )
            )

        stdout_thread.join(timeout=2.0)
        stderr_thread.join(timeout=2.0)

        # 4. Post-execution AST check
        if args.pre_commit and exit_code == 0:
            post_ast_errors = validate_python_ast(resources)
            if post_ast_errors:
                print("[prismatic exec] 🚫 Post-commit AST syntax check failed:", file=sys.stderr)
                for err in post_ast_errors:
                    print(f"  {err}", file=sys.stderr)
                exit_code = 1

        duration = round(time.time() - start_time, 3)
        stage = "EXEC_COMPLETED" if exit_code == 0 else "EXEC_FAILED"
        client.emit_signal(
            SignalPayload(
                agent_id=agent_id,
                stage=stage,
                message=f"prismatic exec finished with code {exit_code} ({duration}s)",
                task_id=task_id,
                metadata={"command": command, "exit_code": exit_code, "duration_seconds": duration},
            )
        )

    finally:
        # Restore signal handlers
        signal.signal(signal.SIGINT, prev_sigint)
        signal.signal(signal.SIGTERM, prev_sigterm)

        # Guaranteed fail-safe release
        lease.release()

    if interrupted:
        return 130
    return exit_code


def main() -> None:
    sys.exit(run_exec_cli())


if __name__ == "__main__":
    main()
