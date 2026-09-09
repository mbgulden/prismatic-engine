"""Fail-safe process execution supervisor with SwarmLock fencing (`prismatic exec`).

Lifecycle:
1. Acquire SwarmLock lease via HypervisorClient (fail-closed, return 423 if locked).
2. Emit "fenced_exec_started" telemetry signal to Prismatic Hub.
3. Run subprocess with SIGINT/SIGTERM trapping.
4. Pre-commit syntax validation check if target resource is a python file.
5. Guaranteed cleanup in `finally:` block: release SwarmLock lease and emit "fenced_exec_finished".
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import py_compile
import signal
import sys
import time
from pathlib import Path
from typing import Any, List, Sequence

from prismatic.client.interceptor import get_hypervisor_client

logger = logging.getLogger("prismatic.client.exec")


def validate_python_ast(paths: list[str] | None = None) -> list[str]:
    """Validate python syntax across specified paths and repository status.

    Returns a list of error strings (empty if all clean).
    """
    import ast
    import subprocess

    errors: list[str] = []
    files_to_check: set[Path] = set()

    if paths:
        for p in paths:
            target = Path(p)
            if target.is_file() and target.suffix == ".py":
                files_to_check.add(target)

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
                parts = line.split(maxsplit=1)
                if len(parts) == 2:
                    raw_path = parts[1].strip().strip('"\'')
                    path_obj = Path(raw_path)
                    if path_obj.suffix == ".py" and path_obj.is_file():
                        files_to_check.add(path_obj)
    except Exception as exc:
        logger.debug("Could not inspect git status for AST validation: %s", exc)

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


async def run_fenced_execution(
    resource: str,
    task_id: str,
    agent_id: str,
    command: List[str],
    lease_seconds: int = 120,
    pre_commit: bool = True,
) -> int:
    """Run an arbitrary command under an exclusive SwarmLock lease and telemetry envelope.

    Enforces:
    - Step 1: Acquire lease (fail-closed if 423 Locked).
    - Step 2: Emit Start Telemetry Signal.
    - Step 3: Run Subprocess with Signal Trapping.
    - Step 3.5: Pre-Commit Syntax Validation Check on python files.
    - Step 4: GUARANTEED Cleanup in `finally:` block (release lease + emit finished signal).
    """
    client = get_hypervisor_client()
    lease_id: str | None = None
    exit_code = 1

    try:
        # Step 1: Acquire lease (fail-closed if 423 Locked)
        acquire_res = await client.acquire_swarmlock(
            resource=resource,
            agent_id=agent_id,
            task_id=task_id,
            lease_seconds=lease_seconds,
        )
        if not acquire_res.get("ok"):
            err_msg = acquire_res.get("error") or f"Resource '{resource}' is locked"
            sys.stderr.write(f"SwarmLock rejection: {err_msg}\n")
            sys.stderr.flush()
            return 423

        lease_id = acquire_res.get("lease_id")

        # Step 2: Emit Start Telemetry Signal
        await client.emit_signal(
            source=agent_id,
            action="fenced_exec_started",
            details={"task_id": task_id, "resource": resource, "command": command},
        )

        # Step 3: Run Subprocess with Signal Trapping
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=sys.stdout,
                stderr=sys.stderr,
            )
        except Exception as proc_err:
            sys.stderr.write(f"[prismatic exec] Failed to start command {command}: {proc_err}\n")
            sys.stderr.flush()
            return 1

        loop = asyncio.get_running_loop()

        def handle_signal() -> None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass

        registered_signals: list[int] = []
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, handle_signal)
                registered_signals.append(sig)
            except (NotImplementedError, RuntimeError):
                pass

        try:
            raw_code = await proc.wait()
            if raw_code < 0:
                exit_code = 128 + abs(raw_code)
            else:
                exit_code = raw_code
        finally:
            for sig in registered_signals:
                try:
                    loop.remove_signal_handler(sig)
                except Exception:
                    pass

        # Step 3.5: Pre-Commit Syntax Validation Check
        # Before releasing the lock, if the modified resource is a .py file:
        # - Execute py_compile.compile(resource, doraise=True).
        # - If AST syntax is invalid, emit a CRITICAL telemetry signal and notify the operator before releasing the lease.
        if pre_commit and resource.endswith(".py") and Path(resource).is_file():
            try:
                py_compile.compile(resource, doraise=True)
            except Exception as syn_err:
                sys.stderr.write(
                    f"[prismatic exec] 🚫 CRITICAL: Pre-commit syntax failure on {resource}: {syn_err}\n"
                )
                sys.stderr.flush()
                await client.emit_signal(
                    source=agent_id,
                    action="pre_commit_syntax_failure",
                    severity="CRITICAL",
                    task_id=task_id,
                    details={"resource": resource, "error": str(syn_err)},
                )
                exit_code = 1

        return exit_code

    finally:
        # Step 4: GUARANTEED Cleanup in finally block
        if lease_id:
            try:
                await asyncio.shield(
                    client.release_swarmlock(
                        resource=resource,
                        agent_id=agent_id,
                        task_id=task_id,
                        lease_id=lease_id,
                    )
                )
            except Exception as exc:
                sys.stderr.write(f"[prismatic exec] Warning: release_swarmlock failed: {exc}\n")
                sys.stderr.flush()

            try:
                await asyncio.shield(
                    client.emit_signal(
                        source=agent_id,
                        action="fenced_exec_finished",
                        details={
                            "task_id": task_id,
                            "resource": resource,
                            "lease_id": lease_id,
                            "exit_code": exit_code,
                        },
                    )
                )
            except Exception as exc:
                sys.stderr.write(f"[prismatic exec] Warning: emit_signal failed: {exc}\n")
                sys.stderr.flush()


def run_exec_cli(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for `prismatic exec`.

    Usage:
        python3 -m prismatic.cli exec \
            --resource "prismatic/mesh/tailscale.py" \
            --task "GRO-4852" \
            --agent-id "kai" \
            --lease-seconds 120 \
            -- python3 build_component.py
    """
    raw_args = list(argv) if argv is not None else sys.argv[1:]

    flags: list[str] = []
    command: list[str] = []

    if "--" in raw_args:
        idx = raw_args.index("--")
        flags = raw_args[:idx]
        command = raw_args[idx + 1 :]
    else:
        flags = raw_args
        command = []

    parser = argparse.ArgumentParser(
        prog="prismatic exec",
        description="Execute a command wrapped inside a fail-safe SwarmLock lease and telemetry envelope.",
    )
    parser.add_argument(
        "--resource",
        "-r",
        default="file:workspace",
        help="Target resource or file path to lock (default: file:workspace)",
    )
    parser.add_argument(
        "--task",
        "-t",
        default="GRO-4852",
        help="Task identifier (default: GRO-4852)",
    )
    parser.add_argument(
        "--agent-id",
        "--agent",
        "-a",
        dest="agent_id",
        default="agy",
        help="Agent identifier holding the SwarmLock (default: agy)",
    )
    parser.add_argument(
        "--lease-seconds",
        "--timeout",
        "-l",
        dest="lease_seconds",
        type=int,
        default=120,
        help="Maximum lease duration in seconds (default: 120)",
    )
    parser.add_argument(
        "--pre-commit",
        dest="pre_commit",
        action="store_true",
        default=True,
        help="Verify python AST syntax on target resource (default: True)",
    )
    parser.add_argument(
        "--no-pre-commit",
        dest="pre_commit",
        action="store_false",
        help="Disable pre-commit syntax check",
    )

    parsed, remaining = parser.parse_known_args(flags)
    if not command and remaining:
        command = remaining

    if not command:
        sys.stderr.write(
            "[prismatic exec] Error: No command specified to execute. Use: prismatic exec [options] -- <command> [args...]\n"
        )
        sys.stderr.flush()
        return 1

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(
                asyncio.run,
                run_fenced_execution(
                    resource=parsed.resource,
                    task_id=parsed.task,
                    agent_id=parsed.agent_id,
                    command=command,
                    lease_seconds=parsed.lease_seconds,
                    pre_commit=parsed.pre_commit,
                ),
            ).result()
    else:
        return asyncio.run(
            run_fenced_execution(
                resource=parsed.resource,
                task_id=parsed.task,
                agent_id=parsed.agent_id,
                command=command,
                lease_seconds=parsed.lease_seconds,
                pre_commit=parsed.pre_commit,
            )
        )


def main() -> None:
    sys.exit(run_exec_cli())


if __name__ == "__main__":
    main()
