"""Canonical durable Google Antigravity CLI harness.

The harness is the PE Core lifecycle adapter. Admission remains upstream; producer
completion remains downstream of independent verification.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from prismatic.agy_cli import (
    CANONICAL_AGY_WORKFLOW_VERSION,
    CANONICAL_RESULT_MARKER,
    AgyLaunchSpec,
    AgyWorkflowError,
    launch_tmux,
    validate_admission_receipt,
    wait_tmux,
)
from prismatic.harnesses.base import AgentHarness, HarnessCapabilities, HarnessStatus

_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _create_json_exclusive(path: Path, payload: dict[str, Any]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return True


def _proc_start_ticks(pid: int) -> str | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
        return raw[raw.rfind(")") + 2 :].split()[19]
    except (OSError, IndexError):
        return None


def _tail(path: Path, count: int) -> list[str]:
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-count:]


class AGYCLIHarness(AgentHarness):
    """Hash-bound AGY runtime behind the stable ``AgentHarness`` contract."""

    @property
    def name(self) -> str:
        return "agy-cli"

    @property
    def models(self) -> list[str]:
        configured = self._config.get("models", ["gemini-3.6-flash-high"])
        return [str(model) for model in configured]

    def _path(self, key: str, *, directory: bool = False) -> Path:
        raw = self._config.get(key)
        if not isinstance(raw, str) or not raw:
            raise AgyWorkflowError(f"AGY harness config requires {key}")
        path = Path(raw)
        if not path.is_absolute():
            raise AgyWorkflowError(f"AGY harness {key} must be absolute")
        if directory and not path.is_dir():
            raise AgyWorkflowError(f"AGY harness {key} is not a directory")
        return path

    def _binary(self) -> tuple[Path, str]:
        binary = self._path("agy_binary")
        if binary.is_symlink() or not binary.is_file():
            raise AgyWorkflowError("AGY harness executable is not a regular file")
        mode = stat.S_IMODE(binary.stat().st_mode)
        if mode & 0o022:
            raise AgyWorkflowError("AGY harness executable is group/world writable")
        expected = self._config.get("agy_binary_sha256")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise AgyWorkflowError("AGY harness requires reviewed executable SHA-256")
        if _sha256(binary) != expected:
            raise AgyWorkflowError("AGY harness executable SHA-256 mismatch")
        return binary, expected

    def _runtime_dir(self) -> Path:
        return self._path("runtime_dir", directory=True)

    def _spool_dir(self) -> Path:
        return self._path("spool_dir", directory=True)

    def _launch_dir(self, run_id: str) -> Path:
        return self._runtime_dir() / run_id

    def _record(self, run_id: str) -> dict[str, Any]:
        path = self._launch_dir(run_id) / "harness-run.json"
        if not path.is_file():
            raise KeyError(f"unknown AGY run: {run_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _reserve_admission(
        self, spec: AgyLaunchSpec, admission_path: Path, run_id: str
    ) -> str | None:
        admission = validate_admission_receipt(admission_path, spec)
        ledger_path = (
            self._runtime_dir()
            / "admission-ledger"
            / f"{admission['attempt_token']}.json"
        )
        payload = {
            "run_id": run_id,
            "event_id": admission["event_id"],
            "attempt": admission["attempt"],
            "attempt_token": admission["attempt_token"],
            "task_sha256": _sha256(Path(spec.task_file)),
            "agy_binary_sha256": spec.agy_binary_sha256,
            "reserved_at": time.time(),
        }
        if _create_json_exclusive(ledger_path, payload):
            return None
        existing = json.loads(ledger_path.read_text(encoding="utf-8"))
        for key in (
            "event_id",
            "attempt",
            "attempt_token",
            "task_sha256",
            "agy_binary_sha256",
        ):
            if existing.get(key) != payload[key]:
                raise AgyWorkflowError("admission token replay binding mismatch")
        existing_run = existing.get("run_id")
        if not isinstance(existing_run, str) or not _RUN_ID_RE.fullmatch(existing_run):
            raise AgyWorkflowError("admission ledger is malformed")
        launch_dir = self._launch_dir(existing_run)
        if not (
            (launch_dir / "launch-receipt.json").is_file()
            or (launch_dir / "harness-run.json").is_file()
        ):
            raise AgyWorkflowError(
                "admission token was consumed without a durable launch; issue a new attempt"
            )
        return existing_run

    def _slot_is_active(self, path: Path) -> bool:
        try:
            slot = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return True
        existing_run = slot.get("run_id")
        if not isinstance(existing_run, str) or not _RUN_ID_RE.fullmatch(existing_run):
            return True
        launch_dir = self._launch_dir(existing_run)
        if (launch_dir / "process-result.json").is_file() or (
            launch_dir / "cancel-receipt.json"
        ).is_file():
            return False
        record_path = launch_dir / "harness-run.json"
        receipt_path = launch_dir / "launch-receipt.json"
        identity = None
        for candidate in (record_path, receipt_path):
            try:
                identity = json.loads(candidate.read_text(encoding="utf-8"))
                break
            except (OSError, json.JSONDecodeError):
                continue
        if isinstance(identity, dict):
            try:
                return _proc_start_ticks(int(identity["pane_pid"])) == str(
                    identity["pane_start_ticks"]
                )
            except (KeyError, TypeError, ValueError):
                return True
        try:
            owner_pid = int(slot["owner_pid"])
            owner_start_ticks = str(slot["owner_start_ticks"])
        except (KeyError, TypeError, ValueError):
            return True
        return _proc_start_ticks(owner_pid) == owner_start_ticks

    def _claim_slot(self, run_id: str) -> Path:
        cap = int(self._config.get("concurrent_runs", 1))
        if not 1 <= cap <= 32:
            raise AgyWorkflowError("AGY harness concurrent_runs must be 1..32")
        slots = self._runtime_dir() / "active-slots"
        slots.mkdir(mode=0o700, exist_ok=True)
        lock_fd = os.open(slots / ".slot-lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            payload = {
                "run_id": run_id,
                "claimed_at": time.time(),
                "owner_pid": os.getpid(),
                "owner_start_ticks": _proc_start_ticks(os.getpid()),
            }
            for index in range(cap):
                path = slots / f"slot-{index}.json"
                if _create_json_exclusive(path, payload):
                    return path
                if not self._slot_is_active(path):
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
                    if _create_json_exclusive(path, payload):
                        return path
            raise AgyWorkflowError("AGY harness concurrency cap is full")
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    @staticmethod
    def _release_slot(path: Path, run_id: str) -> None:
        lock_fd = os.open(path.parent / ".slot-lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("run_id") == run_id:
                    path.unlink()
            except FileNotFoundError:
                return
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    def dispatch(self, task: dict[str, Any]) -> str:
        run_id = str(task.get("run_id") or f"agy-{uuid.uuid4().hex}")
        if not _RUN_ID_RE.fullmatch(run_id):
            raise AgyWorkflowError("AGY run_id contains unsafe characters")
        model = str(task.get("model") or self.models[0])
        if model not in self.models:
            raise AgyWorkflowError(f"AGY model is not configured: {model}")
        binary, binary_sha256 = self._binary()
        workspace = Path(str(task.get("workspace") or ""))
        task_file = Path(str(task.get("task_file") or ""))
        admission = Path(str(task.get("admission_receipt") or ""))
        if not workspace.is_absolute() or not workspace.is_dir():
            raise AgyWorkflowError("AGY task requires an absolute workspace directory")
        if not task_file.is_absolute() or not task_file.is_file():
            raise AgyWorkflowError("AGY task requires an absolute frozen task file")
        if not admission.is_absolute():
            raise AgyWorkflowError("AGY task requires an absolute admission receipt")

        artifact_root = self._spool_dir() / run_id
        artifact_root.mkdir(mode=0o700)
        spec = AgyLaunchSpec(
            identifier=run_id,
            task_file=str(task_file),
            workspace=str(workspace),
            artifact_root=str(artifact_root),
            result_path=str(artifact_root / "RESULT.md"),
            plan_path=str(artifact_root / "PLAN.md"),
            stdout_path=str(artifact_root / "stdout.log"),
            stderr_path=str(artifact_root / "stderr.log"),
            diagnostics_path=str(artifact_root / "agy-diagnostics.log"),
            agy_binary=str(binary),
            agy_binary_sha256=binary_sha256,
            agy_home=str(self._path("agy_home", directory=True)),
            model=model,
            sandbox=bool(task.get("sandbox", True)),
        )
        existing_run = self._reserve_admission(spec, admission, run_id)
        if existing_run is not None:
            artifact_root.rmdir()
            return existing_run
        slot_path = self._claim_slot(run_id)
        try:
            receipt = launch_tmux(
                spec,
                self._runtime_dir(),
                admission_receipt=admission,
                tmux=str(self._config.get("tmux", "/usr/bin/tmux")),
            )
        except Exception:
            self._release_slot(slot_path, run_id)
            try:
                artifact_root.rmdir()
            except OSError:
                pass
            raise
        record = {
            "schema_version": "1.0",
            "workflow_version": CANONICAL_AGY_WORKFLOW_VERSION,
            "run_id": run_id,
            "task_ref": task.get("task_ref"),
            "status": HarnessStatus.RUNNING.value,
            "started_at": receipt["started_at_unix"],
            "completed_at": None,
            "artifact_root": str(artifact_root),
            "result_path": spec.result_path,
            "plan_path": spec.plan_path,
            "stdout_path": spec.stdout_path,
            "stderr_path": spec.stderr_path,
            "diagnostics_path": spec.diagnostics_path,
            "activity_path": receipt["activity_path"],
            "runtime_deadline": None,
            "active_slot_path": str(slot_path),
            "launch_receipt_path": str(
                self._launch_dir(run_id) / "launch-receipt.json"
            ),
            "session": receipt["session"],
            "pane_pid": receipt["pane_pid"],
            "pane_start_ticks": receipt["pane_start_ticks"],
            "task_sha256": receipt["task_sha256"],
            "event_id": receipt["event_id"],
            "attempt": receipt["attempt"],
            "attempt_token": receipt["attempt_token"],
            "verification_status": "pending",
            "error": None,
        }
        _atomic_json(self._launch_dir(run_id) / "harness-run.json", record)
        return run_id

    def status(self, run_id: str) -> dict[str, Any]:
        record = self._record(run_id)
        launch_dir = self._launch_dir(run_id)
        cancel_receipt = launch_dir / "cancel-receipt.json"
        process_result = launch_dir / "process-result.json"
        if cancel_receipt.is_file():
            status = HarnessStatus.CANCELLED
            error = None
            completed_at = json.loads(cancel_receipt.read_text())["cancelled_at"]
        elif process_result.is_file():
            wait_tmux(
                Path(record["launch_receipt_path"]),
                tmux=str(self._config.get("tmux", "/usr/bin/tmux")),
            )
            process = json.loads(process_result.read_text())
            result = Path(record["result_path"])
            plan = Path(record["plan_path"])
            complete = (
                process.get("exit_code") == 0
                and result.is_file()
                and CANONICAL_RESULT_MARKER in result.read_text(errors="replace")
                and plan.is_file()
            )
            status = HarnessStatus.COMPLETED if complete else HarnessStatus.FAILED
            error = None if complete else "producer exit/result contract failed"
            completed_at = process.get("finished_at_unix")
        else:
            live = (
                _proc_start_ticks(int(record["pane_pid"])) == record["pane_start_ticks"]
            )
            status = HarnessStatus.RUNNING if live else HarnessStatus.FAILED
            error = None if live else "tmux pane identity disappeared without result"
            completed_at = None
        if status in {
            HarnessStatus.COMPLETED,
            HarnessStatus.FAILED,
            HarnessStatus.CANCELLED,
        }:
            self._release_slot(Path(record["active_slot_path"]), run_id)
        activity_path = Path(record["activity_path"])
        activity = (
            json.loads(activity_path.read_text(encoding="utf-8"))
            if activity_path.is_file()
            else {
                "classification": "starting",
                "process_alive": status is HarnessStatus.RUNNING,
                "runtime_deadline": None,
                "automatic_kill": False,
            }
        )
        return {
            "status": status.value,
            "started_at": record["started_at"],
            "completed_at": completed_at,
            "error": error,
            "verification_status": "pending",
            "producer_completed": status is HarnessStatus.COMPLETED,
            "runtime_deadline": None,
            "activity": activity,
        }

    def cancel(self, run_id: str) -> bool:
        record = self._record(run_id)
        session = str(record["session"])
        if _proc_start_ticks(int(record["pane_pid"])) != record["pane_start_ticks"]:
            return False
        tmux = str(self._config.get("tmux", "/usr/bin/tmux"))
        result = subprocess.run(
            [tmux, "kill-session", "-t", session],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if _proc_start_ticks(int(record["pane_pid"])) != record["pane_start_ticks"]:
                break
            time.sleep(0.05)
        else:
            raise AgyWorkflowError("AGY exact pane identity survived cancellation")
        _atomic_json(
            self._launch_dir(run_id) / "cancel-receipt.json",
            {
                "run_id": run_id,
                "session": session,
                "cancelled_at": time.time(),
                "exact_pane_cleanup": True,
                "operator_or_policy_action_required": True,
            },
        )
        self._release_slot(Path(record["active_slot_path"]), run_id)
        return result.returncode == 0

    def logs(self, run_id: str, tail: int = 100) -> list[str]:
        if type(tail) is not int or not 1 <= tail <= 10_000:
            raise ValueError("tail must be between 1 and 10000")
        record = self._record(run_id)
        lines: list[str] = []
        for label, key in (
            ("stdout", "stdout_path"),
            ("stderr", "stderr_path"),
            ("diagnostics", "diagnostics_path"),
        ):
            lines.extend(f"[{label}] {line}" for line in _tail(Path(record[key]), tail))
        return lines[-tail:]

    def cost(self, run_id: str) -> dict[str, Any]:
        self._record(run_id)
        return {
            "tokens_in": None,
            "tokens_out": None,
            "dollars": None,
            "available": False,
        }

    def health(self) -> dict[str, str]:
        try:
            self._binary()
            self._runtime_dir()
            self._spool_dir()
            self._path("agy_home", directory=True)
            tmux = Path(str(self._config.get("tmux", "/usr/bin/tmux")))
            if not tmux.is_file():
                raise AgyWorkflowError("tmux unavailable")
        except (AgyWorkflowError, OSError) as exc:
            return {"status": "unavailable", "harness": self.name, "error": str(exc)}
        return {"status": "ok", "harness": self.name}

    def capabilities(self) -> HarnessCapabilities:
        return HarnessCapabilities(
            streaming_logs=True,
            cost_tracking=False,
            concurrent_runs=int(self._config.get("concurrent_runs", 1)),
            supports_cancel=True,
            supports_timeout=False,
            extra={
                "transport": "tmux-durable-anchor",
                "runtime_policy": "no-wall-clock-cap-progress-supervised",
                "activity_receipts": True,
                "result_contract": CANONICAL_RESULT_MARKER,
                "completion_authority": "producer-only; independent verification pending",
            },
        )
