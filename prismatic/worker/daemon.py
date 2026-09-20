"""Headless distributed compute worker daemon for Prismatic Hypervisor."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import socket
import subprocess
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

from prismatic.worker.protocol import WorkerJob, WorkerReceipt

logger = logging.getLogger("prismatic.worker.daemon")


def _get_node_ip() -> str:
    """Detect Tailscale mesh IP or fallback to local IP."""
    try:
        proc = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception:
        pass

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class WorkerDaemon:
    """Autonomous headless worker daemon that polls for mesh jobs and executes in sandboxes."""

    def __init__(
        self,
        gateway_url: str | None = None,
        node_id: str | None = None,
        tags: list[str] | None = None,
        poll_interval: float = 2.0,
        heartbeat_interval: float = 15.0,
        worktree_sandbox: bool = False,
        token: str | None = None,
    ) -> None:
        self.gateway_url = (
            gateway_url
            or os.environ.get("PRISMATIC_GATEWAY_URL")
            or "http://localhost:9000"
        ).rstrip("/")
        self.hostname = socket.gethostname()
        self.ip = _get_node_ip()
        self.node_id = (
            node_id
            or os.environ.get("PRISMATIC_NODE_ID")
            or f"node-{self.hostname}-{os.getpid()}"
        )
        self.tags = tags or ["general"]
        self.poll_interval = max(0.5, poll_interval)
        self.heartbeat_interval = max(3.0, heartbeat_interval)
        self.worktree_sandbox = worktree_sandbox
        self.token = (
            token
            or os.environ.get("PRISMATIC_WORKER_TOKEN")
            or os.environ.get("PRISMATIC_CONTROL_TOKEN")
        )
        self._running = False
        self._stop_event = threading.Event()

    def _http_request(
        self,
        endpoint: str,
        method: str = "GET",
        data: dict[str, Any] | None = None,
        timeout: float = 10.0,
    ) -> dict[str, Any] | None:
        url = f"{self.gateway_url}{endpoint}"
        body_bytes = None
        headers = {"User-Agent": "Prismatic-Worker/0.2.0"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if data is not None:
            body_bytes = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"


        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_bytes = resp.read()
                return json.loads(resp_bytes.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            logger.warning("HTTP %s from %s: %s", exc.code, url, exc.reason)
            return None
        except Exception as exc:
            logger.debug("HTTP request failed %s: %s", url, exc)
            return None

    def register(self) -> bool:
        """Register node with the Gateway."""
        payload = {
            "node_id": self.node_id,
            "hostname": self.hostname,
            "ip": self.ip,
            "tags": self.tags,
            "version": "0.2.0",
        }
        res = self._http_request("/api/gateway/workers/register", method="POST", data=payload)
        if res and res.get("ok"):
            logger.info("Registered worker %s at %s (%s)", self.node_id, self.ip, self.tags)
            return True
        return False

    def heartbeat(self) -> bool:
        """Emit node heartbeat to the Gateway."""
        payload = {"node_id": self.node_id}
        res = self._http_request("/api/gateway/workers/heartbeat", method="POST", data=payload)
        return bool(res and res.get("ok"))

    def emit_signal(self, action: str, details: str, target: str = "") -> None:
        """Emit telemetry event signal to Gateway hub."""
        payload = {
            "agent": self.node_id,
            "action": action,
            "target": target or self.node_id,
            "details": details,
        }
        self._http_request("/api/gateway/signals/emit", method="POST", data=payload)

    def lease_job(self) -> WorkerJob | None:
        """Request next queued job matching node tags."""
        payload = {"node_id": self.node_id, "tags": self.tags, "ttl_seconds": 60}
        res = self._http_request("/api/gateway/jobs/lease", method="POST", data=payload)
        if res and res.get("ok") and res.get("job"):
            return WorkerJob.from_dict(res["job"])
        return None

    def heartbeat_job(self, job_id: str, fence_token: int) -> bool:
        """Extend active job lease."""
        payload = {
            "job_id": job_id,
            "node_id": self.node_id,
            "fence_token": fence_token,
            "ttl_seconds": 60,
        }
        res = self._http_request("/api/gateway/jobs/heartbeat", method="POST", data=payload)
        return bool(res and res.get("ok") and res.get("extended"))

    def complete_job(self, job_id: str, fence_token: int, receipt: WorkerReceipt) -> bool:
        """Submit completed job receipt."""
        payload = {
            "job_id": job_id,
            "node_id": self.node_id,
            "fence_token": fence_token,
            "receipt": receipt.to_dict(),
        }
        res = self._http_request("/api/gateway/jobs/complete", method="POST", data=payload)
        return bool(res and res.get("ok") and res.get("completed"))

    def _execute_job(self, job: WorkerJob) -> WorkerReceipt:
        """Execute task inside sandboxed environment with deterministic telemetry."""
        t_start = time.time()
        logger.info("Executing job %s (task: %s): %s", job.id, job.task_id, job.command)
        self.emit_signal("JOB_START", f"Running {job.command}", target=job.task_id)

        # Background thread to heartbeat the job while running
        job_done = threading.Event()

        def _heartbeat_worker_thread() -> None:
            while not job_done.wait(10.0):
                self.heartbeat_job(job.id, job.fence_token)

        hb_t = threading.Thread(target=_heartbeat_worker_thread, daemon=True)
        hb_t.start()

        stdout = ""
        stderr = ""
        exit_code = 0
        artifacts: dict[str, Any] = {}

        job_meta = getattr(job, "metadata", {}) or {}
        is_hermes_task = (
            job_meta.get("harness") == "hermes"
            or any(t == "hermes" or t.startswith("hermes:") for t in job.tags)
            or any(t in {"george", "kai", "ned", "autobot", "fred", "orchestrator", "next-step"} for t in job.tags)
            or job.command.strip().startswith(("hermes ", "/home/ubuntu/.local/bin/hermes"))
        )
        is_agy_task = (
            job_meta.get("harness") == "agy"
            or any(t in {"agy", "antigravity"} for t in job.tags)
            or job.command.strip().startswith(("agy ", "agy-bin ", "/home/ubuntu/.local/bin/agy"))
        )

        try:
            if is_hermes_task:
                from .harness import HermesProfileRunner
                runner = HermesProfileRunner()
                res = runner.execute(job)
                stdout = res.stdout
                stderr = res.stderr
                exit_code = res.exit_code
                artifacts.update(res.artifacts)
            elif is_agy_task:
                from .harness import AgyHarnessRunner
                runner = AgyHarnessRunner()
                res = runner.execute(job)
                stdout = res.stdout
                stderr = res.stderr
                exit_code = res.exit_code
                artifacts.update(res.artifacts)
            else:
                # Execute command in shell
                proc = subprocess.run(
                    job.command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=job.timeout_seconds,
                )
                stdout = proc.stdout
                stderr = proc.stderr
                exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            stderr = f"Job timed out after {job.timeout_seconds}s"
            exit_code = 124
        except Exception as exc:
            stderr = f"Execution exception: {exc}"
            exit_code = 1
        finally:
            job_done.set()
            hb_t.join(timeout=1.0)

        # AST Validation check if python files are involved
        try:
            from prismatic.client.exec import validate_python_ast
            ast_errors = validate_python_ast()
            if ast_errors:
                artifacts["ast_errors"] = ast_errors
                if exit_code == 0:
                    exit_code = 2
                    stderr += f"\nAST Syntax Errors: {ast_errors}"
        except Exception:
            pass

        duration = round(time.time() - t_start, 3)

        # Compute Deterministic Log Digest (DLD)
        dld_raw = json.dumps(
            {
                "job_id": job.id,
                "task_id": job.task_id,
                "command": job.command,
                "exit_code": exit_code,
                "duration": duration,
                "stdout_hash": hashlib.sha256(stdout.encode()).hexdigest(),
                "stderr_hash": hashlib.sha256(stderr.encode()).hexdigest(),
            },
            sort_keys=True,
        )
        dld_digest = hashlib.sha256(dld_raw.encode()).hexdigest()

        return WorkerReceipt(
            job_id=job.id,
            node_id=self.node_id,
            exit_code=exit_code,
            stdout=stdout[:8000],
            stderr=stderr[:4000],
            duration_seconds=duration,
            artifacts=artifacts,
            dld_digest=dld_digest,
        )

    def run(self, once: bool = False, max_jobs: int | None = None) -> int:
        """Run worker polling loop."""
        self._running = True
        self._stop_event.clear()

        # Register with Gateway
        self.register()

        # Background thread for periodic node heartbeat
        def _node_heartbeat_loop() -> None:
            while not self._stop_event.wait(self.heartbeat_interval):
                self.heartbeat()

        node_hb_t = threading.Thread(target=_node_heartbeat_loop, daemon=True)
        node_hb_t.start()

        jobs_completed = 0
        logger.info(
            "Worker %s started polling gateway %s for jobs (tags: %s, once=%s)",
            self.node_id,
            self.gateway_url,
            self.tags,
            once,
        )

        try:
            while not self._stop_event.is_set():
                job = self.lease_job()
                if job:
                    receipt = self._execute_job(job)
                    self.complete_job(job.id, job.fence_token, receipt)
                    self.emit_signal(
                        "JOB_COMPLETE",
                        f"Job {job.id} completed (exit: {receipt.exit_code}) in {receipt.duration_seconds}s",
                        target=job.task_id,
                    )
                    jobs_completed += 1
                    if once or (max_jobs is not None and jobs_completed >= max_jobs):
                        break
                else:
                    if once:
                        logger.info("No jobs queued, worker exiting (--once)")
                        break
                    self._stop_event.wait(self.poll_interval)
        except KeyboardInterrupt:
            logger.info("Worker interrupted by user")
        finally:
            self._stop_event.set()
            node_hb_t.join(timeout=2.0)
            self._running = False
            logger.info("Worker %s shut down gracefully (%d jobs handled)", self.node_id, jobs_completed)

        return 0

    def stop(self) -> None:
        self._stop_event.set()


def run_worker_daemon(
    gateway_url: str | None = None,
    node_id: str | None = None,
    tags: list[str] | None = None,
    poll_interval: float = 2.0,
    once: bool = False,
    max_jobs: int | None = None,
    token: str | None = None,
) -> int:
    """CLI entry point for running the headless worker daemon."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    daemon = WorkerDaemon(
        gateway_url=gateway_url,
        node_id=node_id,
        tags=tags,
        poll_interval=poll_interval,
        token=token,
    )


    def _sig_handler(sig: int, frame: Any) -> None:
        daemon.stop()

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    return daemon.run(once=once, max_jobs=max_jobs)
