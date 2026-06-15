"""Ephemeral k3s/Docker pod lifecycle manager for tenant-isolated tasks."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger("prismatic.sandbox.pod_manager")

WORKSPACE_BASE = os.environ.get(
    "PRISMATIC_SANDBOX_WORKSPACE",
    os.path.join(os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state"), "workspaces"),
)
K3S_NAMESPACE = os.environ.get("PRISMATIC_SANDBOX_NAMESPACE", "prismatic-sandbox")
DOCKER_NETWORK = os.environ.get("PRISMATIC_SANDBOX_NETWORK", "bridge")
POD_CLEANUP_TIMEOUT_S = int(os.environ.get("PRISMATIC_SANDBOX_CLEANUP_TIMEOUT", "30"))
POD_STARTUP_TIMEOUT_S = int(os.environ.get("PRISMATIC_SANDBOX_STARTUP_TIMEOUT", "60"))
SANDBOX_IMAGE = os.environ.get("PRISMATIC_SANDBOX_IMAGE", "python:3.12-slim")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")


class PodState(Enum):
    CREATING = "creating"
    RUNNING = "running"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    DESTROYED = "destroyed"


@dataclass(frozen=True)
class HardwareProfile:
    """Resource limits for a sandbox runtime."""

    name: str
    cpu_limit: str
    memory_limit: str
    gpu_count: int = 0

    @classmethod
    def from_name(cls, name: str) -> "HardwareProfile":
        profiles = {
            "standard": cls("standard", "1", "512Mi", 0),
            "memory": cls("memory", "2", "4Gi", 0),
            "compute": cls("compute", "4", "8Gi", 1),
            "gpu": cls("gpu", "4", "16Gi", 2),
        }
        if name not in profiles:
            logger.warning("Unknown hardware profile %r; using standard", name)
        return profiles.get(name, profiles["standard"])


@dataclass
class SandboxPod:
    pod_name: str
    tenant_id: str
    task_id: str
    hardware: HardwareProfile
    state: PodState = PodState.CREATING
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    workspace_mount: str = ""
    runtime_id: str = ""
    last_error: str = ""

    @property
    def label_selector(self) -> str:
        return f"prismatic-tenant={self.tenant_id},prismatic-task={self.task_id}"


def _validate_identifier(value: str, field_name: str) -> str:
    """Reject traversal/control chars before IDs become pod names or paths."""
    if not _SAFE_ID.match(value) or ".." in value or "/" in value or "\\" in value:
        raise ValueError(f"Invalid {field_name}: {value!r}")
    return value


def _pod_safe(value: str) -> str:
    safe = re.sub(r"[^a-z0-9-]", "-", value.lower()).strip("-")
    return re.sub(r"-+", "-", safe)[:50] or "x"


def _check_k3s_available() -> bool:
    try:
        result = subprocess.run(
            ["kubectl", "get", "nodes", "--request-timeout=3s"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _check_docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _kubectl(args: list[str], timeout: int = 30) -> str:
    full_cmd = ["kubectl", *args]
    try:
        result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"kubectl timed out after {timeout}s: {' '.join(full_cmd)}") from exc
    if result.returncode != 0:
        raise RuntimeError(f"kubectl error (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout


def _docker(args: list[str], timeout: int = 60) -> str:
    full_cmd = ["docker", *args]
    try:
        result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"docker timed out after {timeout}s: {' '.join(full_cmd)}") from exc
    if result.returncode != 0:
        raise RuntimeError(f"docker error (exit {result.returncode}): {result.stderr.strip()}")
    return result.stdout


def _ensure_namespace() -> None:
    try:
        _kubectl(["get", "namespace", K3S_NAMESPACE], timeout=10)
    except RuntimeError:
        _kubectl(["create", "namespace", K3S_NAMESPACE], timeout=15)


def _pod_manifest(pod: SandboxPod) -> dict[str, Any]:
    limits: dict[str, str] = {"cpu": pod.hardware.cpu_limit, "memory": pod.hardware.memory_limit}
    if pod.hardware.gpu_count:
        limits["nvidia.com/gpu"] = str(pod.hardware.gpu_count)
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
                    "image": SANDBOX_IMAGE,
                    "command": ["sleep", "infinity"],
                    "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": limits},
                    "volumeMounts": [{"name": "workspace", "mountPath": "/workspace"}],
                    "env": [{"name": "TENANT_ID", "value": pod.tenant_id}, {"name": "TASK_ID", "value": pod.task_id}],
                    "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": False},
                }
            ],
            "volumes": [{"name": "workspace", "hostPath": {"path": pod.workspace_mount, "type": "DirectoryOrCreate"}}],
        },
    }


def _docker_run_args(pod: SandboxPod) -> list[str]:
    args = [
        "run",
        "--rm",
        "-d",
        "--name",
        pod.pod_name,
        "--network",
        DOCKER_NETWORK,
        "--label",
        f"prismatic-tenant={pod.tenant_id}",
        "--label",
        f"prismatic-task={pod.task_id}",
        "--label",
        "prismatic-component=sandbox",
        "--cpus",
        pod.hardware.cpu_limit,
        "--memory",
        pod.hardware.memory_limit,
        "-v",
        f"{pod.workspace_mount}:/workspace",
        "-e",
        f"TENANT_ID={pod.tenant_id}",
        "-e",
        f"TASK_ID={pod.task_id}",
    ]
    if pod.hardware.gpu_count:
        args.extend(["--gpus", str(pod.hardware.gpu_count)])
    args.extend([SANDBOX_IMAGE, "sleep", "infinity"])
    return args


class SandboxPodManager:
    """Manage ephemeral runtime pods/containers with tenant-scoped workspaces."""

    def __init__(self, workspace_base: str = "", dry_run: bool = False) -> None:
        self.workspace_base = os.path.abspath(workspace_base or WORKSPACE_BASE)
        self._pods: dict[str, SandboxPod] = {}
        self._dry_run = dry_run
        if self._dry_run:
            self._mode = "dry_run"
        elif _check_k3s_available():
            self._mode = "k3s"
            _ensure_namespace()
        elif _check_docker_available():
            self._mode = "docker"
        else:
            self._mode = "dry_run"
            self._dry_run = True

    @property
    def mode(self) -> str:
        return self._mode

    def create_pod(self, tenant_id: str, task_id: str, hardware: HardwareProfile | None = None) -> SandboxPod:
        tenant_id = _validate_identifier(tenant_id, "tenant_id")
        task_id = _validate_identifier(task_id, "task_id")
        hw = hardware or HardwareProfile.from_name("standard")
        pod_name = f"prismatic-{_pod_safe(tenant_id)}-{_pod_safe(task_id)}"[:63].rstrip("-")
        workspace = os.path.realpath(os.path.join(self.workspace_base, tenant_id, task_id))
        if os.path.commonpath([self.workspace_base, workspace]) != self.workspace_base:
            raise ValueError("Workspace path escaped sandbox base")
        Path(workspace).mkdir(parents=True, exist_ok=True)

        pod = SandboxPod(pod_name, tenant_id, task_id, hw, workspace_mount=workspace)
        try:
            if self._dry_run:
                pod.state = PodState.RUNNING
            elif self._mode == "k3s":
                self._create_k3s_pod(pod)
            elif self._mode == "docker":
                self._create_docker_container(pod)
            else:
                raise RuntimeError(f"Unsupported runtime mode: {self._mode}")
        except Exception as exc:
            pod.state = PodState.FAILED
            pod.last_error = str(exc)
            self.destroy_pod(pod.pod_name, force=True, known_pod=pod)
            raise
        self._pods[pod_name] = pod
        return pod

    def _create_k3s_pod(self, pod: SandboxPod) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
            json.dump(_pod_manifest(pod), tmp)
            tmp_path = tmp.name
        try:
            _kubectl(["apply", "-f", tmp_path], timeout=30)
        finally:
            os.unlink(tmp_path)
        deadline = time.time() + POD_STARTUP_TIMEOUT_S
        while time.time() < deadline:
            phase = _kubectl(["get", "pod", pod.pod_name, "-n", K3S_NAMESPACE, "-o", "jsonpath={.status.phase}"], timeout=10).strip()
            if phase == "Running":
                pod.state = PodState.RUNNING
                return
            if phase in {"Failed", "Unknown"}:
                break
            time.sleep(2)
        pod.state = PodState.FAILED
        pod.last_error = f"Startup timeout after {POD_STARTUP_TIMEOUT_S}s"
        self.destroy_pod(pod.pod_name, force=True, known_pod=pod)
        raise RuntimeError(pod.last_error)

    def _create_docker_container(self, pod: SandboxPod) -> None:
        pod.runtime_id = _docker(_docker_run_args(pod), timeout=30).strip()
        pod.state = PodState.RUNNING

    def exec_in_pod(self, pod_name: str, command: list[str], timeout: int = 300) -> str:
        pod = self._pods.get(pod_name)
        if not pod:
            raise RuntimeError(f"Unknown pod: {pod_name}")
        pod.state = PodState.EXECUTING
        try:
            if self._dry_run:
                output = f"[dry-run] Would execute: {' '.join(command)}"
            elif self._mode == "k3s":
                output = self._exec_k3s(pod_name, command, timeout)
            elif self._mode == "docker":
                output = self._exec_docker(pod_name, command, timeout)
            else:
                raise RuntimeError(f"Unsupported runtime mode: {self._mode}")
        except Exception as exc:
            pod.state = PodState.FAILED
            pod.last_error = str(exc)
            raise
        pod.state = PodState.COMPLETED
        return output

    def run_task(self, tenant_id: str, task_id: str, command: list[str], hardware: HardwareProfile | None = None, timeout: int = 300) -> str:
        """Create → execute → destroy, guaranteeing cleanup on success/failure."""
        pod: SandboxPod | None = None
        try:
            pod = self.create_pod(tenant_id, task_id, hardware)
            return self.exec_in_pod(pod.pod_name, command, timeout=timeout)
        finally:
            if pod is not None:
                self.destroy_pod(pod.pod_name, force=True)

    def _exec_k3s(self, pod_name: str, command: list[str], timeout: int) -> str:
        result = subprocess.run(
            ["kubectl", "exec", pod_name, "-n", K3S_NAMESPACE, "--", *command],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = result.stdout + (("\n[stderr]\n" + result.stderr) if result.stderr else "")
        if result.returncode != 0:
            raise RuntimeError(f"Command failed (exit {result.returncode}):\n{output}")
        return output.strip()

    def _exec_docker(self, pod_name: str, command: list[str], timeout: int) -> str:
        return _docker(["exec", pod_name, *command], timeout=timeout).strip()

    def stream_logs(self, pod_name: str, tail: int = 50) -> str:
        pod = self._pods.get(pod_name)
        if not pod:
            raise RuntimeError(f"Unknown pod: {pod_name}")
        if self._dry_run:
            return f"[dry-run] Logs for {pod_name}: (no real container)"
        if self._mode == "k3s":
            return _kubectl(["logs", pod_name, "-n", K3S_NAMESPACE, f"--tail={tail}"], timeout=15)
        if self._mode == "docker":
            return _docker(["logs", "--tail", str(tail), pod_name], timeout=15)
        return ""

    def destroy_pod(self, pod_name: str, force: bool = False, known_pod: SandboxPod | None = None) -> None:
        pod = self._pods.get(pod_name) or known_pod
        if not pod:
            if force:
                return
            raise RuntimeError(f"Unknown pod: {pod_name}")
        try:
            if self._dry_run:
                pass
            elif self._mode == "k3s":
                _kubectl(["delete", "pod", pod_name, "-n", K3S_NAMESPACE, "--ignore-not-found=true", "--grace-period=5", f"--timeout={POD_CLEANUP_TIMEOUT_S}s"], timeout=POD_CLEANUP_TIMEOUT_S + 5)
            elif self._mode == "docker":
                _docker(["rm", "-f", pod_name], timeout=15)
        except RuntimeError:
            if not force:
                raise
            logger.exception("Ignoring sandbox cleanup failure for %s", pod_name)
        pod.state = PodState.DESTROYED

    def list_pods(self, tenant_id: str | None = None) -> list[SandboxPod]:
        pods = list(self._pods.values())
        if tenant_id:
            pods = [pod for pod in pods if pod.tenant_id == tenant_id]
        return pods

    def __enter__(self) -> "SandboxPodManager":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        for pod_name in list(self._pods):
            self.destroy_pod(pod_name, force=True)
        self._pods.clear()
