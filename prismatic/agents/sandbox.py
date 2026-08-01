"""
Prismatic Engine — gVisor-aware Sandbox Agent
==============================================

Executes agent tasks inside an OCI-compatible sandbox (Docker) with
optional gVisor (runsc) runtime isolation.

This agent is used for executing untrusted code or providing a clean
runtime environment for specialized agents like AGY or Jules.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from prismatic.providers.tasks.base import Issue

from .base import AgentConfig, BaseAgent

logger = logging.getLogger("prismatic.agents.sandbox")


class SandboxAgent(BaseAgent):
    """Agent that executes work inside a containerized sandbox.

    Supports:
    - Docker runtime (standard)
    - gVisor (runsc) runtime isolation
    - Workspace volume mounting
    - Environment variable injection
    """

    def __init__(
        self,
        config: AgentConfig,
        agent_config: dict[str, Any] | None = None,
    ):
        super().__init__(config, agent_config)
        self._image = self._agent_config.get("image", "prismatic-sandbox:latest")
        self._use_gvisor = self._agent_config.get("use_gvisor", True)
        self._memory_limit = self._agent_config.get("memory_limit", "2g")
        self._cpu_shares = self._agent_config.get("cpu_shares", 1024)
        self._env_whitelist = self._agent_config.get("env_whitelist", ["PRISMATIC_HOME"])

    def execute(self, issue: Issue) -> subprocess.Popen | None:
        """Launch a container and return the process handle.

        Note: Returns a Popen object representing the 'docker run' process.
        The caller is responsible for waiting or polling.
        """
        workspace_path = self._resolve_workspace_path()

        cmd = ["docker", "run", "--rm"]

        # Add gVisor runtime if requested and available
        if self._use_gvisor and self._is_gvisor_available():
            cmd.extend(["--runtime", "runsc"])
            logger.info("Using gVisor (runsc) runtime for isolation")

        # Resource limits
        if self._memory_limit:
            cmd.extend(["--memory", self._memory_limit])
        if self._cpu_shares:
            cmd.extend(["--cpu-shares", str(self._cpu_shares)])

        # Workspace mounting
        if workspace_path and os.path.exists(workspace_path):
            cmd.extend(["-v", f"{workspace_path}:/workspace"])
            cmd.extend(["--workdir", "/workspace"])

        # Environment variables
        cmd.extend(["-e", f"PRISMATIC_ISSUE_ID={issue.identifier}"])
        cmd.extend(["-e", f"PRISMATIC_PROJECT={issue.project or ''}"])

        # Pass through whitelisted env vars from host
        for env_var in self._env_whitelist:
            val = os.environ.get(env_var)
            if val:
                cmd.extend(["-e", f"{env_var}={val}"])

        # Container Name
        container_name = f"prismatic-sandbox-{issue.identifier.lower().replace('-', '_')}-{int(time.time())}"
        cmd.extend(["--name", container_name])

        # Image
        cmd.append(self._image)

        # Command (if specified in config)
        exec_cmd = self._agent_config.get("cmd", [])
        if exec_cmd:
            if isinstance(exec_cmd, str):
                cmd.append(exec_cmd)
            else:
                cmd.extend(exec_cmd)

        logger.info("Launching sandbox container: %s", " ".join(cmd))

        # Log file setup
        log_dir = Path(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state")) / "logs" / "sandbox"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file_path = log_dir / f"{issue.identifier}.log"

        try:
            log_file = open(log_file_path, "a")
            proc = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
            # We don't close the log_file here, Popen needs it.
            # It will be closed when the process finishes or is reaped.
            return proc
        except Exception as exc:
            logger.error("Unexpected error launching sandbox for %s: %s", issue.identifier, exc)
            return None

    def get_id(self) -> str:
        return f"sandbox/{self._image}"

    def _is_gvisor_available(self) -> bool:
        """Check if runsc (gVisor) is installed on the host."""
        try:
            result = subprocess.run(
                ["runsc", "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError):
            return False

    def _resolve_workspace_path(self) -> str:
        """Find the host path to mount as /workspace."""
        # Prefer explicit config
        config_path = self._agent_config.get("workspace_path")
        if config_path:
            return os.path.abspath(config_path)

        # Fallback to standard locations
        prismatic_home = os.environ.get("PRISMATIC_HOME", "/home/ubuntu")
        candidates = [
            Path(prismatic_home) / "work",
            Path(".").resolve(),
        ]

        for cand in candidates:
            if cand.exists():
                return str(cand.absolute())

        return os.getcwd()
