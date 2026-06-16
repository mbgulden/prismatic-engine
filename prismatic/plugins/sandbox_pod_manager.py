"""
prismatic/plugins/sandbox_pod_manager.py — SandboxPodManager for plugin pods.

Manages the lifecycle of plugin sandbox pods using Docker or k3s.  Each
plugin runs in its own ephemeral container/pod with resource limits,
read-only root filesystem option, and automatic cleanup on exit.

Runtime detection order: gVisor (runsc) > Docker > k3s.  Falls back to
the next available runtime if the preferred one is not installed.

Usage
-----
.. code-block:: python

    mgr = SandboxPodManager(state_dir="/tmp/plugin-state")
    result = mgr.start_pod("my-plugin", {"image": "python:3.12", "cmd": ["python", "-m", "my_plugin"]})
    assert result["state"] == "RUNNING"
    mgr.stop_pod("my-plugin")
    mgr.purge_pod("my-plugin")
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("prismatic.plugins.sandbox")


class PodState(str, Enum):
    """Observable states of a sandbox pod."""

    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    FAILED = "FAILED"
    PURGED = "PURGED"


class PodManagerError(Exception):
    """Raised when a pod operation fails."""


@dataclass
class PodInfo:
    """Runtime metadata about a running sandbox pod."""

    name: str
    state: PodState = PodState.STOPPED
    container_id: str = ""
    runtime: str = ""  # "docker", "k3s", "gvisor"
    pid: int = 0
    started_at: float = 0.0
    error_message: str = ""


class SandboxPodManager:
    """Launch, monitor, and destroy plugin sandbox pods.

    Supports Docker and k3s runtimes with automatic detection.
    All pods are ephemeral: container is removed on purge.
    """

    def __init__(
        self,
        state_dir: str = "./prismatic_state/plugins/",
        runtime: str = "auto",
        network: str = "bridge",
        read_only_root: bool = False,
        memory_limit: str = "512m",
        cpu_limit: float = 0.5,
    ) -> None:
        """
        Args:
            state_dir: Directory for pod metadata and logs.
            runtime: One of "auto", "docker", "k3s". "auto" detects available runtime.
            network: Docker network name (ignored for k3s).
            read_only_root: Mount container rootfs as read-only.
            memory_limit: Container memory limit (e.g. "512m", "1g").
            cpu_limit: Container CPU limit (fraction of a core).
        """
        self._state_dir = Path(state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._network = network
        self._read_only_root = read_only_root
        self._memory_limit = memory_limit
        self._cpu_limit = cpu_limit
        self._pods: Dict[str, PodInfo] = {}

        # Detect runtime
        self._runtime = runtime
        if self._runtime == "auto":
            self._runtime = self._detect_runtime()
        logger.info("SandboxPodManager initialized with runtime=%s", self._runtime)

    # ── Runtime detection ──────────────────────────────────────────

    @staticmethod
    def _detect_runtime() -> str:
        """Detect available container runtime.

        Priority: gVisor (runsc) > Docker > k3s.
        Returns the first available runtime name.
        """
        # Check gVisor
        try:
            result = subprocess.run(
                ["runsc", "--version"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return "gvisor"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Check Docker
        try:
            result = subprocess.run(
                ["docker", "--version"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return "docker"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Check k3s kubectl
        try:
            result = subprocess.run(
                ["kubectl", "version", "--short"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return "k3s"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        logger.warning("No container runtime detected — pods will be simulated")
        return "none"

    # ── Public API ─────────────────────────────────────────────────

    @staticmethod
    def _validate_volume_mount(volume_spec: str, seccomp_profile_name: str | None = None) -> str:
        """Validate a volume mount string for path traversal and symlink attacks.

        Checks:
        1. No ``..`` path components (``../``, ``..\\``, URL-encoded variants)
        2. No symlink-based escape — resolves the host path and verifies it is
           rooted under an allowed base directory.
        3. Returns the validated volume spec unchanged if it passes; raises
           ``PodManagerError`` on violation.

        Args:
            volume_spec: Docker-style volume mount like ``/host/path:/container/path:ro``
            seccomp_profile_name: If provided, inject a seccomp label for audit.

        Returns:
            The validated volume spec (unchanged on success).

        Raises:
            PodManagerError: If the mount contains path traversal attempts.
        """
        host_part = volume_spec.split(":")[0] if ":" in volume_spec else volume_spec

        # Check for path traversal sequences
        traversal_patterns = [
            "..",
            "%2e%2e",   # URL-encoded ..
            "%252e%252e",  # Double-URL-encoded ..
            "..\\",
            "..%5c",
        ]
        for pattern in traversal_patterns:
            if pattern.lower() in host_part.lower().replace("\\", "/"):
                raise PodManagerError(
                    f"Path traversal detected in volume mount: '{volume_spec}' "
                    f"(forbidden pattern: '{pattern}')"
                )

        # Resolve symlinks if the path exists
        resolved = Path(host_part).resolve()
        allowed_bases = [
            Path("/home"),
            Path("/tmp"),
            Path("/data"),
            Path("/opt"),
            Path("/mnt"),
            Path("/var"),
        ]
        is_valid = any(
            str(resolved).startswith(str(base))
            for base in allowed_bases
        )
        if not is_valid:
            raise PodManagerError(
                f"Volume mount path '{host_part}' resolves to '{resolved}' "
                f"which is outside allowed base directories: "
                f"{[str(b) for b in allowed_bases]}"
            )

        if seccomp_profile_name:
            logger.debug(
                "Volume mount '%s' passed validation (seccomp=%s)",
                volume_spec, seccomp_profile_name,
            )

        return volume_spec

    def start_pod(
        self,
        name: str,
        config: Dict[str, Any],
        timeout_s: float = 60.0,
    ) -> Dict[str, Any]:
        """Start a sandbox pod for the given plugin.

        Args:
            name: Plugin name (used as container name).
            config: Container configuration dict with optional keys:
                - image (str): Container image (default "python:3.12-slim")
                - cmd (list[str]): Entrypoint command
                - env (dict[str, str]): Environment variables
                - ports (list[str]): Port mappings like "8080:80"
                - volumes (list[str]): Volume mounts like "/host:/container"
            timeout_s: Max seconds to wait for pod to reach RUNNING.

        Returns:
            Dict with keys: state (str), container_id (str), runtime (str).

        Raises:
            PodManagerError: If pod fails to start.
        """
        if name in self._pods and self._pods[name].state == PodState.RUNNING:
            raise PodManagerError(f"Pod '{name}' is already running")

        info = PodInfo(name=name, state=PodState.STARTING, runtime=self._runtime)
        info.started_at = time.time()
        self._pods[name] = info

        try:
            if self._runtime == "docker":
                result = self._start_docker(name, config)
            elif self._runtime == "k3s":
                result = self._start_k3s(name, config)
            elif self._runtime == "gvisor":
                result = self._start_docker(name, config, runtime_flag="runsc")
            else:
                result = self._start_simulated(name, config)

            info.state = PodState.RUNNING
            info.container_id = result.get("container_id", "")
            if result.get("pid"):
                info.pid = result["pid"]

            self._save_pod_info(name)
            logger.info("Pod '%s' started (id=%s, runtime=%s)", name, info.container_id, self._runtime)
            return {"state": info.state.value, "container_id": info.container_id, "runtime": self._runtime}

        except Exception as exc:
            info.state = PodState.FAILED
            info.error_message = str(exc)
            self._save_pod_info(name)
            logger.error("Pod '%s' failed to start: %s", name, exc)
            raise PodManagerError(f"Failed to start pod '{name}': {exc}") from exc

    def stop_pod(self, name: str, force: bool = False, timeout_s: float = 30.0) -> Dict[str, Any]:
        """Stop a running sandbox pod.

        Args:
            name: Plugin name.
            force: If True, use SIGKILL instead of graceful shutdown.
            timeout_s: Max seconds to wait for pod to stop.

        Returns:
            Dict with state after stop operation.

        Raises:
            PodManagerError: If pod cannot be stopped.
        """
        info = self._pods.get(name)
        if info is None:
            raise PodManagerError(f"Pod '{name}' not found")

        info.state = PodState.STOPPING

        try:
            if self._runtime in ("docker", "gvisor"):
                self._stop_docker(name, force)
            elif self._runtime == "k3s":
                self._stop_k3s(name, force)
            else:
                self._stop_simulated(name)

            info.state = PodState.STOPPED
            self._save_pod_info(name)
            logger.info("Pod '%s' stopped (force=%s)", name, force)
            return {"state": info.state.value}

        except Exception as exc:
            info.state = PodState.FAILED
            info.error_message = str(exc)
            self._save_pod_info(name)
            logger.error("Pod '%s' failed to stop: %s", name, exc)
            raise PodManagerError(f"Failed to stop pod '{name}': {exc}") from exc

    def get_pod_status(self, name: str) -> Dict[str, Any]:
        """Return current status of a pod.

        Args:
            name: Plugin name.

        Returns:
            Dict with pod metadata, or None if not found.
        """
        info = self._pods.get(name)
        if info is None:
            # Try loading from disk
            info = self._load_pod_info(name)
            if info is None:
                return {"state": "NOT_FOUND"}
            self._pods[name] = info

        return asdict(info)

    def list_pods(self) -> List[Dict[str, Any]]:
        """Return all tracked pods."""
        return [asdict(info) for info in self._pods.values()]

    def purge_pod(self, name: str) -> Dict[str, Any]:
        """Completely remove a pod (stop + delete container + clean metadata).

        Args:
            name: Plugin name.

        Returns:
            Dict with final state.
        """
        info = self._pods.get(name)
        if info is not None and info.state in (PodState.RUNNING, PodState.STARTING, PodState.STOPPING):
            try:
                self.stop_pod(name, force=True)
            except PodManagerError:
                pass

        try:
            if self._runtime in ("docker", "gvisor") and info and info.container_id:
                subprocess.run(
                    ["docker", "rm", "-f", info.container_id],
                    capture_output=True, timeout=10
                )
            elif self._runtime == "k3s":
                subprocess.run(
                    ["kubectl", "delete", "pod", name, "--now", "--ignore-not-found"],
                    capture_output=True, timeout=10
                )
        except Exception as exc:
            logger.warning("Pod '%s' container removal failed: %s", name, exc)

        # Clean state files
        state_file = self._state_dir / f"{name}.json"
        state_file.unlink(missing_ok=True)
        log_file = self._state_dir / f"{name}.log"
        log_file.unlink(missing_ok=True)

        if name in self._pods:
            old = self._pods[name]
            old.state = PodState.PURGED
            old.container_id = ""
            self._save_pod_info(name)

        logger.info("Pod '%s' purged", name)
        return {"state": PodState.PURGED.value}

    def health_check(self, name: str) -> bool:
        """Check if a pod's container process is still alive.

        Args:
            name: Plugin name.

        Returns:
            True if pod is RUNNING and container process exists.
        """
        info = self._pods.get(name)
        if info is None or info.state != PodState.RUNNING:
            return False

        # Simulated runtime — assume alive
        if self._runtime == "none":
            return True

        if not info.container_id:
            return True  # simulated — assume alive

        try:
            if self._runtime in ("docker", "gvisor"):
                result = subprocess.run(
                    ["docker", "inspect", "--format", "{{.State.Status}}", info.container_id],
                    capture_output=True, text=True, timeout=5
                )
                return result.returncode == 0 and result.stdout.strip() == "running"

            elif self._runtime == "k3s":
                result = subprocess.run(
                    ["kubectl", "get", "pod", name, "-o", "jsonpath={.status.phase}"],
                    capture_output=True, text=True, timeout=5
                )
                return result.returncode == 0 and result.stdout.strip() == "Running"
        except Exception:
            pass

        return False

    # ── Docker helpers ─────────────────────────────────────────────

    def _start_docker(self, name: str, config: Dict[str, Any], runtime_flag: str = "") -> Dict[str, Any]:
        """Launch a Docker container for the plugin."""
        cmd = ["docker", "run", "-d", "--name", name]

        if runtime_flag:
            cmd.extend(["--runtime", runtime_flag])

        if self._read_only_root:
            cmd.append("--read-only")

        if self._memory_limit:
            cmd.extend(["--memory", self._memory_limit])

        if self._cpu_limit:
            cmd.extend(["--cpus", str(self._cpu_limit)])

        if self._network:
            cmd.extend(["--network", self._network])

        # Environment variables
        for key, val in config.get("env", {}).items():
            cmd.extend(["-e", f"{key}={val}"])

        # Port mappings
        for port in config.get("ports", []):
            cmd.extend(["-p", port])

        # Volume mounts (with path traversal guard)
        for vol in config.get("volumes", []):
            validated_vol = self._validate_volume_mount(vol, "plugin_default.json")
            cmd.extend(["-v", validated_vol])

        # Labels for tracking
        cmd.extend(["-l", f"prismatic-plugin={name}"])

        # Image and command
        image = config.get("image", "python:3.12-slim")
        cmd.append(image)

        entry_cmd = config.get("cmd", [])
        if entry_cmd:
            cmd.extend(entry_cmd)

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise PodManagerError(f"Docker start failed: {result.stderr.strip()}")

        container_id = result.stdout.strip()
        return {"container_id": container_id}

    def _stop_docker(self, name: str, force: bool) -> None:
        """Stop a Docker container."""
        info = self._pods.get(name)
        if not info or not info.container_id:
            return

        signal = ["docker", "kill", info.container_id] if force else ["docker", "stop", info.container_id]
        result = subprocess.run(signal, capture_output=True, timeout=30)
        if result.returncode != 0 and "not running" not in result.stderr.decode():
            logger.warning("Docker stop warning for '%s': %s", name, result.stderr.decode())

    # ── k3s helpers ────────────────────────────────────────────────

    def _start_k3s(self, name: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Launch a k3s pod for the plugin via kubectl run."""
        image = config.get("image", "python:3.12-slim")
        cmd_parts = config.get("cmd", ["sleep", "infinity"])

        # Build kubectl run command
        kube_cmd = ["kubectl", "run", name, "--image=" + image, "--restart=Never"]

        if self._memory_limit:
            kube_cmd.extend(["--limits=memory=" + self._memory_limit])

        if self._read_only_root:
            kube_cmd.append("--read-only-root-filesystem=true")

        # Env vars
        for key, val in config.get("env", {}).items():
            kube_cmd.extend(["--env", f"{key}={val}"])

        # Ports
        for port in config.get("ports", []):
            kube_cmd.extend(["--port", port.split(":")[0]])

        # Labels
        kube_cmd.extend(["-l", f"prismatic-plugin={name}"])

        # Command (override default entrypoint)
        if cmd_parts:
            kube_cmd.append("--command")
            kube_cmd.extend(["--"] + cmd_parts)

        result = subprocess.run(kube_cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise PodManagerError(f"k3s pod start failed: {result.stderr.strip()}")

        # Wait for pod to be Running
        deadline = time.time() + 30
        pod_name = name
        while time.time() < deadline:
            status_result = subprocess.run(
                ["kubectl", "get", "pod", pod_name, "-o", "jsonpath={.status.phase}"],
                capture_output=True, text=True, timeout=10
            )
            phase = status_result.stdout.strip()
            if phase == "Running":
                return {"container_id": pod_name}
            elif phase in ("Failed", "Error", "CrashLoopBackOff"):
                raise PodManagerError(f"Pod '{name}' entered state {phase}")
            time.sleep(1)

        raise PodManagerError(f"Pod '{name}' did not reach Running within 30s")

    def _stop_k3s(self, name: str, force: bool) -> None:
        """Delete a k3s pod."""
        cmd = ["kubectl", "delete", "pod", name, "--now"]
        if force:
            cmd.append("--grace-period=0")
            cmd.append("--force")
        subprocess.run(cmd, capture_output=True, timeout=30)

    # ── Simulated (no runtime) helpers ─────────────────────────────

    def _start_simulated(self, name: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Simulate a pod start when no container runtime is available.

        Used for development and testing environments.
        """
        state_file = self._state_dir / f"{name}.json"
        state = {
            "name": name,
            "state": "RUNNING",
            "runtime": "simulated",
            "started_at": time.time(),
            "config": config,
        }
        state_file.write_text(json.dumps(state, indent=2))
        return {"container_id": f"sim-{name}"}

    def _stop_simulated(self, name: str) -> None:
        """Stop a simulated pod by writing STOPPED state."""
        state_file = self._state_dir / f"{name}.json"
        if state_file.exists():
            state = json.loads(state_file.read_text())
            state["state"] = "STOPPED"
            state_file.write_text(json.dumps(state, indent=2))

    # ── Persistence ────────────────────────────────────────────────

    def _save_pod_info(self, name: str) -> None:
        """Persist pod info to disk."""
        info = self._pods.get(name)
        if info is None:
            return
        state_file = self._state_dir / f"{name}.json"
        state_file.write_text(json.dumps(asdict(info), indent=2, default=str))

    def _load_pod_info(self, name: str) -> Optional[PodInfo]:
        """Load pod info from disk."""
        state_file = self._state_dir / f"{name}.json"
        print(f"DEBUG _load_pod_info: state_file path is {state_file.resolve()}, exists={state_file.exists()}")
        if not state_file.exists():
            return None
        try:
            data = json.loads(state_file.read_text())
            print(f"DEBUG _load_pod_info: parsed json data: {data}")
            return PodInfo(**data)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("Failed to load pod info for '%s': %s", name, exc)
            return None
