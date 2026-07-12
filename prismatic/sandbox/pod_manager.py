"""
prismatic/sandbox/pod_manager.py — Ephemeral k3s Pod Lifecycle Manager

Manages tenant-isolated sandbox pods for agent execution:

    - create_pod(tenant_id, task_id, hw_profile)  → spins up ephemeral pod
    - exec_in_pod(pod_name, command)               → runs process inside pod
    - stream_logs(pod_name)                         → real-time log streaming
    - destroy_pod(pod_name)                        → guaranteed cleanup
    - list_pods()                                   → inventory of active sandboxes

Supports two modes:
    1. **k3s mode** — wraps ``kubectl`` to manage pods on a local k3s cluster
    2. **Docker fallback** (local dev) — wraps ``docker run`` for single-node dev

Integration:
    - prismatic/dispatcher.py — launches sandboxed agent tasks
    - prismatic/gateway/security.py — path traversal guard for workspace mounts
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger("prismatic.sandbox.pod_manager")

# ── Environment / Configuration ────────────────────────────────────────────

WORKSPACE_BASE = os.environ.get(
    "PRISMATIC_SANDBOX_WORKSPACE",
    os.path.join(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"), "workspaces"),
)
K3S_NAMESPACE = os.environ.get("PRISMATIC_SANDBOX_NAMESPACE", "prismatic-sandbox")
DOCKER_NETWORK = os.environ.get("PRISMATIC_SANDBOX_NETWORK", "prismatic-sandbox")
POD_CLEANUP_TIMEOUT_S = int(os.environ.get("PRISMATIC_SANDBOX_CLEANUP_TIMEOUT", "30"))
POD_STARTUP_TIMEOUT_S = int(os.environ.get("PRISMATIC_SANDBOX_STARTUP_TIMEOUT", "60"))
DOCKER_IMAGE = os.environ.get("PRISMATIC_SANDBOX_IMAGE", "python:3.12-slim")

# ── Hardware Profiles ──────────────────────────────────────────────────────


class PodState(Enum):
    """Lifecycle states for a sandbox pod."""

    CREATING = "creating"
    RUNNING = "running"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    DESTROYED = "destroyed"


@dataclass
class HardwareProfile:
    """Resource constraints for a sandbox pod."""

    name: str
    cpu_limit: str          # e.g. "1", "2"
    memory_limit: str       # e.g. "512Mi", "2Gi"
    gpu_count: int = 0

    @classmethod
    def from_name(cls, name: str) -> HardwareProfile:
        profiles = {
            "standard": cls(name="standard", cpu_limit="1", memory_limit="512Mi", gpu_count=0),
            "memory":   cls(name="memory", cpu_limit="2", memory_limit="4Gi", gpu_count=0),
            "compute":  cls(name="compute", cpu_limit="4", memory_limit="8Gi", gpu_count=1),
            "gpu":      cls(name="gpu", cpu_limit="4", memory_limit="16Gi", gpu_count=2),
        }
        if name not in profiles:
            logger.warning("Unknown hardware profile %r — falling back to 'standard'", name)
            return profiles["standard"]
        return profiles[name]


# ── Pod descriptor ─────────────────────────────────────────────────────────


@dataclass
class SandboxPod:
    """Describes a single sandbox pod instance."""

    pod_name: str
    tenant_id: str
    task_id: str
    hardware: HardwareProfile
    state: PodState = PodState.CREATING
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    workspace_mount: str = ""
    k8s_uid: str = ""
    last_error: str = ""

    @property
    def label_selector(self) -> str:
        return f"prismatic-tenant={self.tenant_id},prismatic-task={self.task_id}"


# ── Driver detection ───────────────────────────────────────────────────────


def _check_k3s_available() -> bool:
    """Return True if kubectl can reach a k3s cluster."""
    try:
        result = subprocess.run(
            ["kubectl", "get", "nodes", "--request-timeout=3s"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _check_docker_available() -> bool:
    """Return True if docker CLI is available."""
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ── k3s helpers ────────────────────────────────────────────────────────────


def _kubectl(args: list[str], timeout: int = 30) -> str:
    """Run kubectl and return stdout. Raises RuntimeError on failure."""
    full_cmd = ["kubectl"] + args
    try:
        result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"kubectl timed out after {timeout}s: {' '.join(full_cmd)}")
    if result.returncode != 0:
        raise RuntimeError(
            f"kubectl error (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def _ensure_namespace() -> None:
    """Create the sandbox namespace if it doesn't exist."""
    try:
        _kubectl(["get", "namespace", K3S_NAMESPACE])
    except RuntimeError:
        _kubectl(["create", "namespace", K3S_NAMESPACE])
        logger.info("Created namespace %s", K3S_NAMESPACE)


def _make_pod_manifest(pod: SandboxPod) -> dict[str, Any]:
    """Build a Kubernetes Pod manifest for the sandbox pod."""
    resources: dict[str, Any] = {
        "requests": {
            "cpu": "100m",
            "memory": "128Mi",
        },
        "limits": {
            "cpu": pod.hardware.cpu_limit,
            "memory": pod.hardware.memory_limit,
        },
    }
    if pod.hardware.gpu_count > 0:
        resources["limits"]["nvidia.com/gpu"] = str(pod.hardware.gpu_count)

    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod.pod_name,
            "namespace": K3S_NAMESPACE,
            "labels": {
                "prismatic-tenant": pod.tenant_id,
                "prismatic-task": pod.task_id,
                "prismatic-component": "sandbox",
            },
        },
        "spec": {
            "restartPolicy": "Never",
            "containers": [
                {
                    "name": "worker",
                    "image": DOCKER_IMAGE,
                    "command": ["sleep", "infinity"],
                    "resources": resources,
                    "volumeMounts": [
                        {
                            "name": "workspace",
                            "mountPath": "/workspace",
                        },
                    ],
                    "env": [
                        {"name": "TENANT_ID", "value": pod.tenant_id},
                        {"name": "TASK_ID", "value": pod.task_id},
                    ],
                },
            ],
            "volumes": [
                {
                    "name": "workspace",
                    "hostPath": {
                        "path": pod.workspace_mount,
                        "type": "DirectoryOrCreate",
                    },
                },
            ],
        },
    }


# ── Docker fallback helpers ────────────────────────────────────────────────


def _docker(args: list[str], timeout: int = 60) -> str:
    """Run docker and return stdout. Raises RuntimeError on failure."""
    full_cmd = ["docker"] + args
    try:
        result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"docker timed out after {timeout}s: {' '.join(full_cmd)}")
    if result.returncode != 0:
        raise RuntimeError(
            f"docker error (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def _docker_run_cmd(pod: SandboxPod) -> list[str]:
    """Build docker run arguments for a dev-mode sandbox container."""
    return [
        "run",
        "--rm",
        "-d",
        "--name", pod.pod_name,
        "--network", DOCKER_NETWORK,
        "--label", f"prismatic-tenant={pod.tenant_id}",
        "--label", f"prismatic-task={pod.task_id}",
        "--label", "prismatic-component=sandbox",
        "--cpus", pod.hardware.cpu_limit,
        "--memory", pod.hardware.memory_limit,
        "-v", f"{pod.workspace_mount}:/workspace",
        "-e", f"TENANT_ID={pod.tenant_id}",
        "-e", f"TASK_ID={pod.task_id}",
        DOCKER_IMAGE,
        "sleep", "infinity",
    ]


# ── Pod Manager ────────────────────────────────────────────────────────────


class SandboxPodManager:
    """Manages ephemeral sandbox pods for tenant-isolated agent execution.

    Automatically detects available runtime::

        - k3s (via kubectl) — production mode
        - Docker              — local dev mode
        - Dry-run             — simulation when neither is available

    Usage::

        mgr = SandboxPodManager()
        pod = mgr.create_pod("tenant-42", "task-abc", HardwareProfile.from_name("standard"))
        output = mgr.exec_in_pod(pod.pod_name, ["python3", "-c", "print('hello')"])
        logs = mgr.stream_logs(pod.pod_name)
        mgr.destroy_pod(pod.pod_name)
    """

    def __init__(self, workspace_base: str = "", dry_run: bool = False):
        self.workspace_base = workspace_base or WORKSPACE_BASE
        self._pods: dict[str, SandboxPod] = {}
        self._dry_run = dry_run or not (
            _check_k3s_available() or _check_docker_available()
        )

        if self._dry_run:
            logger.info("PodManager in DRY-RUN mode — no real containers will be created")
        elif _check_k3s_available():
            self._mode = "k3s"
            _ensure_namespace()
            logger.info("PodManager in k3s mode — using kubectl against namespace %s", K3S_NAMESPACE)
        elif _check_docker_available():
            self._mode = "docker"
            logger.info("PodManager in Docker mode — using docker CLI")
        else:
            self._mode = "dry_run"
            self._dry_run = True
            logger.info("PodManager in DRY-RUN mode — no runtime available")

    @property
    def mode(self) -> str:
        """Runtime mode: 'k3s', 'docker', or 'dry_run'."""
        return getattr(self, "_mode", "dry_run")

    # ── Pod Lifecycle ──────────────────────────────────────────────────

    def create_pod(
        self,
        tenant_id: str,
        task_id: str,
        hardware: HardwareProfile | None = None,
    ) -> SandboxPod:
        """Spin up an ephemeral sandbox pod for the given tenant/task.

        Args:
            tenant_id: Tenant identifier (e.g. "tenant-42").
            task_id: Task identifier (e.g. "task-abc").
            hardware: Resource profile. Defaults to 'standard'.

        Returns:
            SandboxPod descriptor with state=CREATING or RUNNING.

        Raises:
            RuntimeError: If pod creation fails.
        """
        hw = hardware or HardwareProfile.from_name("standard")
        pod_name = f"prismatic-{tenant_id}-{task_id}".replace("_", "-").lower()
        workspace = os.path.join(self.workspace_base, tenant_id, task_id)

        os.makedirs(workspace, exist_ok=True)

        pod = SandboxPod(
            pod_name=pod_name,
            tenant_id=tenant_id,
            task_id=task_id,
            hardware=hw,
            workspace_mount=workspace,
            state=PodState.CREATING,
        )

        if self._dry_run:
            pod.state = PodState.RUNNING
            self._pods[pod_name] = pod
            logger.info("[dry-run] Would create pod %s (tenant=%s, task=%s)", pod_name, tenant_id, task_id)
            return pod

        if self._mode == "k3s":
            self._create_k3s_pod(pod)
        elif self._mode == "docker":
            self._create_docker_container(pod)
        else:
            raise RuntimeError(f"Unsupported runtime mode: {self._mode}")

        self._pods[pod_name] = pod
        logger.info("Created pod %s (mode=%s, tenant=%s, task=%s)", pod_name, self._mode, tenant_id, task_id)
        return pod

    def _create_k3s_pod(self, pod: SandboxPod) -> None:
        """Launch a k3s pod from manifest and wait for it to be ready."""
        manifest = _make_pod_manifest(pod)
        manifest_json = json.dumps(manifest)

        # Write manifest to temp file to avoid shell escaping issues
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        try:
            tmp.write(manifest_json)
            tmp.close()
            _kubectl(["apply", "-f", tmp.name], timeout=30)
        finally:
            os.unlink(tmp.name)

        # Wait for pod to reach Running state
        deadline = time.time() + POD_STARTUP_TIMEOUT_S
        while time.time() < deadline:
            try:
                status = _kubectl([
                    "get", "pod", pod.pod_name,
                    "-n", K3S_NAMESPACE,
                    "-o", "jsonpath={.status.phase}",
                ], timeout=10)
                if status.strip() == "Running":
                    pod.state = PodState.RUNNING
                    return
            except RuntimeError:
                pass
            time.sleep(2)

        # Timed out — attempt cleanup and raise
        pod.state = PodState.FAILED
        pod.last_error = f"Startup timeout after {POD_STARTUP_TIMEOUT_S}s"
        self._cleanup_failed(pod.pod_name)
        raise RuntimeError(
            f"Pod {pod.pod_name} did not reach Running state within "
            f"{POD_STARTUP_TIMEOUT_S}s"
        )

    def _create_docker_container(self, pod: SandboxPod) -> None:
        """Launch a Docker container as dev-mode sandbox."""
        cmd = _docker_run_cmd(pod)
        output = _docker(cmd, timeout=30)
        container_id = output.strip()
        pod.k8s_uid = container_id
        pod.state = PodState.RUNNING

    def exec_in_pod(
        self,
        pod_name: str,
        command: list[str],
        timeout: int = 300,
    ) -> str:
        """Execute a command inside a running sandbox pod.

        Args:
            pod_name: Name of the sandbox pod.
            command: Command to run (e.g. ["python3", "-c", "print('hi')"]).
            timeout: Max execution time in seconds.

        Returns:
            Combined stdout+stderr output.

        Raises:
            RuntimeError: If pod doesn't exist or command fails.
        """
        pod = self._pods.get(pod_name)
        if not pod:
            raise RuntimeError(f"Unknown pod: {pod_name}")

        pod.state = PodState.EXECUTING
        logger.info("Executing in pod %s: %s", pod_name, " ".join(command))

        if self._dry_run:
            output = f"[dry-run] Would execute: {' '.join(command)}"
            pod.state = PodState.COMPLETED
            return output

        if self._mode == "k3s":
            output = self._exec_k3s(pod_name, command, timeout)
        elif self._mode == "docker":
            output = self._exec_docker(pod_name, command, timeout)
        else:
            raise RuntimeError(f"Unsupported runtime mode: {self._mode}")

        pod.state = PodState.COMPLETED
        return output

    def _exec_k3s(self, pod_name: str, command: list[str], timeout: int) -> str:
        """Execute command inside k3s pod via kubectl exec."""
        args = [
            "exec", pod_name,
            "-n", K3S_NAMESPACE,
            "--"] + command
        try:
            result = subprocess.run(
                ["kubectl"] + args,
                capture_output=True, text=True, timeout=timeout,
            )
            output = result.stdout
            if result.stderr:
                output += "\n[stderr]\n" + result.stderr
            if result.returncode != 0:
                raise RuntimeError(
                    f"Command failed (exit {result.returncode}):\n{output}"
                )
            return output.strip()
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Command timed out after {timeout}s in pod {pod_name}")

    def _exec_docker(self, pod_name: str, command: list[str], timeout: int) -> str:
        """Execute command inside Docker container."""
        args = ["exec"] + command
        return _docker(args, timeout=timeout).strip()

    def stream_logs(self, pod_name: str, tail: int = 50) -> str:
        """Retrieve recent logs from a sandbox pod.

        Args:
            pod_name: Name of the sandbox pod.
            tail: Number of recent lines to fetch.

        Returns:
            Log text.
        """
        pod = self._pods.get(pod_name)
        if not pod:
            raise RuntimeError(f"Unknown pod: {pod_name}")

        if self._dry_run:
            return f"[dry-run] Logs for {pod_name}: (no real container)"

        if self._mode == "k3s":
            return _kubectl([
                "logs", pod_name,
                "-n", K3S_NAMESPACE,
                f"--tail={tail}",
            ], timeout=15)

        if self._mode == "docker":
            return _docker(["logs", pod_name, f"--tail={tail}"], timeout=15)

        return ""

    def destroy_pod(self, pod_name: str, force: bool = False) -> None:
        """Destroy a sandbox pod, including on failure.

        Args:
            pod_name: Name of the sandbox pod.
            force: If True, ignore missing-pod errors during cleanup.
        """
        pod = self._pods.get(pod_name)
        if not pod:
            if force:
                return
            raise RuntimeError(f"Unknown pod: {pod_name}")

        logger.info("Destroying pod %s", pod_name)

        if self._dry_run:
            pod.state = PodState.DESTROYED
            logger.info("[dry-run] Would destroy pod %s", pod_name)
            return

        if self._mode == "k3s":
            try:
                _kubectl([
                    "delete", "pod", pod_name,
                    "-n", K3S_NAMESPACE,
                    "--grace-period=5",
                    "--timeout=30s",
                ], timeout=35)
            except RuntimeError as exc:
                if not force:
                    raise
                logger.warning("Force-destroy: ignoring error: %s", exc)

        elif self._mode == "docker":
            try:
                _docker(["rm", "-f", pod_name], timeout=15)
            except RuntimeError as exc:
                if not force:
                    raise
                logger.warning("Force-destroy: ignoring error: %s", exc)

        pod.state = PodState.DESTROYED
        logger.info("Destroyed pod %s", pod_name)

    def _cleanup_failed(self, pod_name: str) -> None:
        """Best-effort cleanup of a pod that failed to start."""
        try:
            self.destroy_pod(pod_name, force=True)
        except Exception:
            logger.exception("Cleanup of failed pod %s also failed", pod_name)

    # ── Inventory ──────────────────────────────────────────────────────

    def list_pods(self, tenant_id: str | None = None) -> list[SandboxPod]:
        """List all pods (optionally filtered by tenant).

        In k3s/Docker mode, syncs state from the actual runtime.
        """
        if not self._dry_run and self._mode == "k3s":
            self._sync_from_k3s()

        pods = list(self._pods.values())
        if tenant_id:
            pods = [p for p in pods if p.tenant_id == tenant_id]
        return pods

    def _sync_from_k3s(self) -> None:
        """Refresh pod state from k3s."""
        try:
            output = _kubectl([
                "get", "pods",
                "-n", K3S_NAMESPACE,
                "-l", "prismatic-component=sandbox",
                "-o", "json",
            ], timeout=15)
            data = json.loads(output)
            for item in data.get("items", []):
                name = item["metadata"]["name"]
                labels = item["metadata"].get("labels", {})
                phase = item["status"].get("phase", "")
                if name in self._pods:
                    if phase == "Running":
                        self._pods[name].state = PodState.RUNNING
                    elif phase in ("Succeeded",):
                        self._pods[name].state = PodState.COMPLETED
                    elif phase in ("Failed", "Unknown"):
                        self._pods[name].state = PodState.FAILED
                    else:
                        self._pods[name].state = PodState.CREATING
                    self._pods[name].k8s_uid = item["metadata"].get("uid", "")
        except (RuntimeError, json.JSONDecodeError) as exc:
            logger.warning("k3s sync failed (will retry): %s", exc)

    def __enter__(self) -> SandboxPodManager:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Destroy all pods on context manager exit."""
        for pod_name in list(self._pods.keys()):
            self.destroy_pod(pod_name, force=True)
        self._pods.clear()
