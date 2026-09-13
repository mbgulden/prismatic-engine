"""prismatic/worker/harness.py — AGY Execution Harness Runner for Mesh Workers
===========================================================================

Bridges the distributed WorkerDaemon to the local Google Antigravity CLI,
enforcing structured JSON execution, non-TTY headless isolation, deterministic
receipt generation, and metric parsing.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .protocol import WorkerJob

logger = logging.getLogger("prismatic.worker.harness")


@dataclass
class HarnessResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    artifacts: dict[str, Any] = field(default_factory=dict)


class AgyHarnessRunner:
    """Headless AGY CLI execution adapter for distributed mesh tasks."""

    DEFAULT_BINARY_PATHS = [
        "/home/ubuntu/.local/bin/agy-bin",
        "/home/ubuntu/.local/bin/agy",
    ]

    def __init__(self, binary_path: str | None = None) -> None:
        self.binary_path = self._resolve_binary(binary_path)

    @classmethod
    def _resolve_binary(cls, preferred: str | None = None) -> str | None:
        candidates = [
            preferred,
            os.environ.get("AGY_PATH"),
            os.environ.get("AGY_BINARY"),
            *cls.DEFAULT_BINARY_PATHS,
            shutil.which("agy-bin"),
            shutil.which("agy"),
        ]
        for c in candidates:
            if c and os.path.isfile(c) and os.access(c, os.X_OK):
                return os.path.abspath(c)
        return None

    def is_available(self) -> bool:
        if not self.binary_path:
            self.binary_path = self._resolve_binary()
        if not self.binary_path or not os.path.isfile(self.binary_path):
            return False
        try:
            res = subprocess.run(
                [self.binary_path, "--help"],
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            return res.returncode == 0
        except Exception:
            return False

    def execute(self, job: WorkerJob, cwd: Path | None = None) -> HarnessResult:
        """Execute a WorkerJob via headless AGY CLI."""
        t_start = time.time()
        if not self.binary_path:
            self.binary_path = self._resolve_binary()

        if not self.binary_path:
            return HarnessResult(
                exit_code=127,
                stdout="",
                stderr="AGY CLI binary not found on host. Verify /home/ubuntu/.local/bin/agy-bin.",
                duration_seconds=0.0,
                artifacts={"error": "binary_not_found"},
            )

        # Prepare environment: ensure ~/.local/bin is in PATH
        env = os.environ.copy()
        local_bin = "/home/ubuntu/.local/bin"
        if local_bin not in env.get("PATH", ""):
            env["PATH"] = f"{local_bin}:{env.get('PATH', '')}"

        # Resolve model
        model = job.metadata.get("model") or "gemini-3.8-flash-high"

        # Determine if job.command is a direct CLI invocation or a task prompt
        raw_cmd = job.command.strip()
        if raw_cmd.startswith(("agy ", "agy-bin ", "/home/ubuntu/.local/bin/agy")):
            parts = raw_cmd.split()
            cmd = list(parts)
            if "--output-format" not in cmd:
                cmd.extend(["--output-format", "json"])
            if "--dangerously-skip-permissions" not in cmd:
                cmd.append("--dangerously-skip-permissions")
        else:
            cmd = [
                self.binary_path,
                "--print",
                raw_cmd,
                "--model",
                model,
                "--output-format",
                "json",
                "--dangerously-skip-permissions",
            ]

            ws = job.metadata.get("workspace")
            if ws and os.path.isdir(ws):
                cmd.extend(["--add-dir", str(Path(ws).resolve())])

            conv_id = job.metadata.get("conversation_id")
            if conv_id:
                cmd.extend(["--conversation", str(conv_id)])

            effort = job.metadata.get("effort")
            if effort in {"low", "medium", "high"}:
                cmd.extend(["--effort", effort])

        logger.info("AgyHarnessRunner executing command via %s", cmd[0])

        stdout = ""
        stderr = ""
        exit_code = 0
        artifacts: dict[str, Any] = {"harness": "agy", "model": model}

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=job.timeout_seconds,
                cwd=str(cwd) if cwd else None,
                env=env,
            )
            stdout = proc.stdout
            stderr = proc.stderr
            exit_code = proc.returncode

            try:
                payload = json.loads(stdout)
                artifacts["conversation_id"] = payload.get("conversation_id", "")
                artifacts["status"] = payload.get("status", "")
                artifacts["usage"] = payload.get("usage", {})
                artifacts["num_turns"] = payload.get("num_turns", 1)
                artifacts["agy_duration"] = payload.get("duration_seconds", 0)
                if "response" in payload:
                    stdout = payload["response"]
            except (json.JSONDecodeError, TypeError):
                pass

        except subprocess.TimeoutExpired:
            stderr = f"AGY task timed out after {job.timeout_seconds}s"
            exit_code = 124
            artifacts["error"] = "timeout"
        except Exception as exc:
            stderr = f"AGY execution error: {exc}"
            exit_code = 1
            artifacts["error"] = str(exc)

        duration = round(time.time() - t_start, 3)
        return HarnessResult(
            exit_code=exit_code,
            stdout=stdout[:8000],
            stderr=stderr[:4000],
            duration_seconds=duration,
            artifacts=artifacts,
        )


class HermesProfileRunner:
    """Headless Hermes profile execution adapter for distributed mesh tasks."""

    DEFAULT_BINARY_PATHS = [
        "/home/ubuntu/.local/bin/hermes",
    ]

    def __init__(self, binary_path: str | None = None) -> None:
        self.binary_path = self._resolve_binary(binary_path)

    @classmethod
    def _resolve_binary(cls, preferred: str | None = None) -> str | None:
        candidates = [
            preferred,
            os.environ.get("HERMES_PATH"),
            os.environ.get("HERMES_BINARY"),
            *cls.DEFAULT_BINARY_PATHS,
            shutil.which("hermes"),
        ]
        for c in candidates:
            if c and os.path.isfile(c) and os.access(c, os.X_OK):
                return os.path.abspath(c)
        return None

    def is_available(self) -> bool:
        return self.binary_path is not None

    @staticmethod
    def _try_gateway_socket_dispatch(
        socket_path: Path,
        prompt: str,
        chat_id: str = "8190664947",
        timeout: float = 600.0,
    ) -> dict[str, Any] | None:
        """Attempt to dispatch the turn directly to the running Hermes Gateway control socket.

        This triggers real-time Telegram streaming, in-flight steering (/steer),
        and dashboard visibility.
        """
        import socket
        try:
            if not socket_path.is_socket():
                return None
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(min(timeout, 10.0))
            sock.connect(str(socket_path))
            req = {
                "verb": "inject-turn",
                "text": prompt,
                "chat_id": chat_id,
                "platform": "telegram",
                "user_id": chat_id,
                "user_name": "Michael Gulden",
                "wait": True,
                "timeout": timeout,
            }
            sock.sendall(json.dumps(req).encode("utf-8") + b"\n")
            sock.settimeout(timeout + 5.0)
            raw = b""
            while b"\n" not in raw:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                raw += chunk
            sock.close()
            if raw:
                res = json.loads(raw.decode("utf-8").strip())
                if res.get("ok"):
                    return res.get("result", {})
        except Exception as exc:
            logger.warning("Gateway socket dispatch failed, falling back to batch CLI: %s", exc)
        return None

    def execute(self, job: WorkerJob) -> HarnessResult:
        """Execute a taskEnvelope via Hermes in non-interactive one-shot mode."""
        if not self.is_available():
            return HarnessResult(
                exit_code=127,
                stdout="",
                stderr=f"Hermes binary not found. Checked: {self.DEFAULT_BINARY_PATHS}",
                duration_seconds=0.0,
                artifacts={"error": "binary_not_found", "harness": "hermes"},
            )

        t_start = time.time()
        meta = getattr(job, "metadata", {}) or {}
        profile = meta.get("profile")
        if not profile:
            for tag in job.tags:
                if tag.startswith("hermes:"):
                    profile = tag.split(":", 1)[1]
                    break
                elif tag in {"george", "kai", "ned", "autobot", "fred", "orchestrator", "next-step"}:
                    profile = tag
                    break
        if not profile:
            profile = "george"

        profile_home = Path.home() / ".hermes" / "profiles" / profile
        env = os.environ.copy()
        if profile_home.is_dir():
            env["HERMES_HOME"] = str(profile_home.resolve())
        else:
            env["HERMES_HOME"] = str((Path.home() / ".hermes").resolve())

        prompt = job.command.strip()
        artifacts: dict[str, Any] = {"harness": "hermes", "profile": profile}
        sock_path = profile_home / "gateway.sock"
        chat_id = meta.get("chat_id") or "8190664947"
        use_live_gateway = meta.get("live_gateway", True)

        if use_live_gateway and sock_path.is_socket():
            gw_result = self._try_gateway_socket_dispatch(
                sock_path,
                prompt,
                chat_id=chat_id,
                timeout=float(job.timeout_seconds),
            )
            if gw_result and gw_result.get("status") in {"completed", "dispatched"}:
                stdout = gw_result.get("response") or "Dispatched and completed via Hermes Gateway"
                artifacts["mode"] = "live_gateway_telegram"
                artifacts["streamed_to"] = f"telegram:{chat_id}"
                artifacts["gateway_status"] = gw_result.get("status")
                duration = round(time.time() - t_start, 3)
                return HarnessResult(
                    exit_code=0,
                    stdout=stdout[:8000],
                    stderr="",
                    duration_seconds=duration,
                    artifacts=artifacts,
                )

        cmd = [self.binary_path, "--profile", profile, "-z", prompt]

        model = meta.get("model")
        if model:
            cmd.extend(["-m", model])

        logger.info("HermesProfileRunner executing profile %s via %s", profile, cmd[0])

        stdout = ""
        stderr = ""
        exit_code = 0
        artifacts: dict[str, Any] = {"harness": "hermes", "profile": profile}

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=job.timeout_seconds,
                env=env,
            )
            stdout = proc.stdout
            stderr = proc.stderr
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            stderr = f"Hermes task timed out after {job.timeout_seconds}s"
            exit_code = 124
            artifacts["error"] = "timeout"
        except Exception as exc:
            stderr = f"Hermes execution error: {exc}"
            exit_code = 1
            artifacts["error"] = str(exc)

        duration = round(time.time() - t_start, 3)
        return HarnessResult(
            exit_code=exit_code,
            stdout=stdout[:8000],
            stderr=stderr[:4000],
            duration_seconds=duration,
            artifacts=artifacts,
        )
