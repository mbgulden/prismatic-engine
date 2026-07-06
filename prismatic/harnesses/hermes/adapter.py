"""
Hermes systemd harness adapter.

This adapter exposes local Hermes bot agents (Fred, Kai, Ned, etc.) as a
Prismatic Engine ``AgentHarness``.  Dispatch writes a task envelope for the
Hermes side to consume and nudges the corresponding systemd service; status and
logs are read from ``systemctl`` and ``journalctl`` respectively.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from prismatic.harnesses.base import AgentHarness, HarnessCapabilities, HarnessStatus

Runner = Callable[..., subprocess.CompletedProcess[str]]


class HermesHarness(AgentHarness):
    """Harness adapter for systemd-managed Hermes agents."""

    name = "hermes"
    models = ["gemini-3.5-flash-high"]

    def __init__(
        self, config: dict[str, Any] | None = None, runner: Runner | None = None
    ) -> None:
        super().__init__(config)
        self._runner = runner or subprocess.run
        self._target = str(self._config.get("target", "hermes"))
        self._service_template = str(
            self._config.get("service_template", "hermes-{target}.service")
        )
        self._service_name = self._config.get("service_name")
        self._systemctl = str(self._config.get("systemctl", "systemctl"))
        self._journalctl = str(self._config.get("journalctl", "journalctl"))
        self._spool_dir = Path(
            self._config.get(
                "spool_dir",
                os.environ.get("PRISMATIC_HERMES_SPOOL", "/tmp/prismatic/hermes-runs"),
            )
        )
        self._start_on_dispatch = bool(self._config.get("start_on_dispatch", True))
        self._runs: dict[str, dict[str, Any]] = {}

    # ── AgentHarness contract ───────────────────────────────────

    def dispatch(self, task: dict[str, Any]) -> str:
        """Persist a task envelope and nudge the target Hermes systemd service.

        ``task`` may set ``target``/``agent`` and ``service_name`` to override the
        adapter defaults.  The returned run id can later be passed to ``status``
        and ``logs``.
        """
        target = str(task.get("target") or task.get("agent") or self._target)
        service = str(task.get("service_name") or self._service_for_target(target))
        run_id = f"hermes-{target}-{uuid.uuid4().hex[:12]}"
        created_at = time.time()
        envelope = {
            "run_id": run_id,
            "target": target,
            "service": service,
            "created_at": created_at,
            "task": task,
        }

        self._spool_dir.mkdir(parents=True, exist_ok=True)
        task_file = self._spool_dir / f"{run_id}.json"
        task_file.write_text(
            json.dumps(envelope, indent=2, sort_keys=True), encoding="utf-8"
        )

        start_result: dict[str, Any] | None = None
        if self._start_on_dispatch:
            proc = self._run([self._systemctl, "start", service], check=False)
            start_result = {
                "returncode": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
            if proc.returncode != 0:
                # Keep the run id and task file for forensics, but report failure
                # immediately through status().
                status = HarnessStatus.FAILED.value
            else:
                status = HarnessStatus.PENDING.value
        else:
            status = HarnessStatus.PENDING.value

        self._runs[run_id] = {
            "run_id": run_id,
            "target": target,
            "service": service,
            "task_file": str(task_file),
            "created_at": created_at,
            "status": status,
            "start_result": start_result,
        }
        return run_id

    def status(self, run_id: str) -> dict[str, Any]:
        """Read the backing service state with ``systemctl show``."""
        run = self._runs.get(run_id)
        if run is None:
            return {"run_id": run_id, "status": HarnessStatus.UNKNOWN.value}

        service = run["service"]
        proc = self._run(
            [
                self._systemctl,
                "show",
                service,
                "--no-page",
                "--property=ActiveState,SubState,Result,MainPID,ExecMainStatus",
            ],
            check=False,
        )
        if proc.returncode != 0:
            return {
                "run_id": run_id,
                "status": HarnessStatus.UNKNOWN.value,
                "service": service,
                "error": proc.stderr.strip() or proc.stdout.strip(),
            }

        fields = self._parse_systemctl_show(proc.stdout)
        normalized = self._normalize_status(fields, fallback=run.get("status"))
        run["status"] = normalized
        return {
            "run_id": run_id,
            "status": normalized,
            "service": service,
            "target": run.get("target"),
            "task_file": run.get("task_file"),
            "systemd": fields,
        }

    def cancel(self, run_id: str) -> bool:
        """Stop the backing Hermes systemd service."""
        run = self._runs.get(run_id)
        if run is None:
            return False
        proc = self._run([self._systemctl, "stop", run["service"]], check=False)
        if proc.returncode == 0:
            run["status"] = HarnessStatus.CANCELLED.value
            return True
        return False

    def logs(self, run_id: str, tail: int = 100) -> list[str]:
        """Read recent logs for the backing service with ``journalctl``."""
        run = self._runs.get(run_id)
        if run is None:
            return []
        safe_tail = max(1, int(tail))
        proc = self._run(
            [
                self._journalctl,
                "-u",
                run["service"],
                "-n",
                str(safe_tail),
                "--no-pager",
                "--output=short-iso",
            ],
            check=False,
        )
        if proc.returncode != 0:
            return []
        lines = [line for line in proc.stdout.splitlines() if line]
        return lines[-safe_tail:]

    def cost(self, run_id: str) -> dict[str, Any]:
        """Hermes systemd services have no engine-visible token cost."""
        return {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "run_id": run_id}

    def capabilities(self) -> HarnessCapabilities:
        return HarnessCapabilities(
            streaming_logs=False,
            cost_tracking=False,
            concurrent_runs=1,
            supports_cancel=True,
            supports_timeout=True,
            extra={
                "runtime": "hermes-systemd",
                "status_source": "systemctl",
                "log_source": "journalctl",
            },
        )

    # ── Helpers ─────────────────────────────────────────────────

    def _service_for_target(self, target: str) -> str:
        if self._service_name:
            return str(self._service_name)
        return self._service_template.format(target=target)

    def _run(
        self, args: Sequence[str], check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        return self._runner(
            list(args),
            check=check,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    @staticmethod
    def _parse_systemctl_show(output: str) -> dict[str, str]:
        fields: dict[str, str] = {}
        for line in output.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            fields[key] = value
        return fields

    @staticmethod
    def _normalize_status(
        fields: Mapping[str, str], fallback: str | None = None
    ) -> str:
        active = fields.get("ActiveState", "").lower()
        sub = fields.get("SubState", "").lower()
        result = fields.get("Result", "").lower()
        exec_status = fields.get("ExecMainStatus", "")

        if active in {"activating", "reloading"}:
            return HarnessStatus.PENDING.value
        if active == "active" or sub == "running":
            return HarnessStatus.RUNNING.value
        if active == "failed" or result not in {"", "success"}:
            return HarnessStatus.FAILED.value
        if active == "inactive" and exec_status in {"", "0"}:
            return HarnessStatus.COMPLETED.value
        if fallback in {s.value for s in HarnessStatus}:
            return str(fallback)
        return HarnessStatus.UNKNOWN.value
