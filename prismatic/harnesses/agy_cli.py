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
import signal
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
    reconcile_terminal_run,
    validate_admission_receipt,
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


def _private_lock_fd(path: Path) -> int:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        os.close(fd)
        raise AgyWorkflowError("AGY lock is not a private regular file")
    return fd


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
            metadata = path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_nlink != 1
            ):
                return True
            slot = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return True
        existing_run = slot.get("run_id")
        if not isinstance(existing_run, str) or not _RUN_ID_RE.fullmatch(existing_run):
            return True
        launch_dir = self._launch_dir(existing_run)
        record_path = launch_dir / "harness-run.json"
        receipt_path = launch_dir / "launch-receipt.json"
        identity = None
        for candidate in (record_path, receipt_path):
            try:
                metadata = candidate.lstat()
                if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                    continue
                identity = json.loads(candidate.read_text(encoding="utf-8"))
                break
            except (OSError, json.JSONDecodeError):
                continue
        if isinstance(identity, dict):
            try:
                if _proc_start_ticks(int(identity["pane_pid"])) == str(
                    identity["pane_start_ticks"]
                ):
                    return True
            except (KeyError, TypeError, ValueError):
                pass
        process_path = launch_dir / "process-result.json"
        try:
            metadata = process_path.lstat()
            if stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                process = json.loads(process_path.read_text(encoding="utf-8"))
                if (
                    process.get("workflow_version") == CANONICAL_AGY_WORKFLOW_VERSION
                    and process.get("identifier") == existing_run
                    and process.get("process_tree_cleanup_verified") is True
                    and process.get("surviving_process_identities") == []
                ):
                    return False
        except (OSError, json.JSONDecodeError):
            pass
        cancel_path = launch_dir / "cancel-receipt.json"
        try:
            metadata = cancel_path.lstat()
            if stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                cancelled = json.loads(cancel_path.read_text(encoding="utf-8"))
                if (
                    cancelled.get("run_id") == existing_run
                    and cancelled.get("exact_pane_cleanup") is True
                    and cancelled.get("exact_process_tree_cleanup") is True
                ):
                    return False
        except (OSError, json.JSONDecodeError):
            pass
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
        lock_fd = _private_lock_fd(slots / ".slot-lock")
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
        lock_fd = _private_lock_fd(path.parent / ".slot-lock")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                metadata = path.lstat()
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or stat.S_ISLNK(metadata.st_mode)
                    or metadata.st_nlink != 1
                ):
                    raise AgyWorkflowError("AGY slot is not a private regular file")
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
        producer_identity = str(task.get("producer_identity") or "agy-cli-producer")
        if not _RUN_ID_RE.fullmatch(producer_identity):
            raise AgyWorkflowError("AGY producer_identity contains unsafe characters")
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
            launch_tmux(
                spec,
                self._runtime_dir(),
                admission_receipt=admission,
                tmux=str(self._config.get("tmux", "/usr/bin/tmux")),
                run_record={
                    "task_ref": task.get("task_ref"),
                    "artifact_root": str(artifact_root),
                    "producer_identity": producer_identity,
                },
                active_slot_path=slot_path,
            )
        except Exception:
            launch_dir = self._launch_dir(run_id)
            record_path = launch_dir / "harness-run.json"
            if not record_path.is_file():
                self._release_slot(slot_path, run_id)
                try:
                    artifact_root.rmdir()
                except OSError:
                    pass
            else:
                try:
                    record = reconcile_terminal_run(launch_dir)
                    if (
                        record.get("state") == "review_pending"
                        and not slot_path.exists()
                    ):
                        return run_id
                except Exception:
                    pass
            raise
        return run_id

    def status(self, run_id: str) -> dict[str, Any]:
        record = self._record(run_id)
        launch_dir = self._launch_dir(run_id)
        cancel_receipt = launch_dir / "cancel-receipt.json"
        process_result = launch_dir / "process-result.json"
        if cancel_receipt.is_file() or process_result.is_file():
            record = reconcile_terminal_run(launch_dir)
            status = HarnessStatus(record["status"])
            error = record.get("error")
            completed_at = record.get("completed_at")
        else:
            live = (
                _proc_start_ticks(int(record["pane_pid"])) == record["pane_start_ticks"]
            )
            status = HarnessStatus.RUNNING if live else HarnessStatus.FAILED
            error = None if live else "tmux pane identity disappeared without result"
            completed_at = None
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
            "state": record.get("state", status.value),
            "started_at": record["started_at"],
            "completed_at": completed_at,
            "error": error,
            "verification_status": record.get("verification_status", "pending"),
            "review_status": record.get("review_status"),
            "reviewed_by": record.get("reviewed_by"),
            "producer_completed": bool(record.get("producer_completed")),
            "runtime_deadline": None,
            "activity": activity,
        }

    def cancel(self, run_id: str) -> bool:
        record = self._record(run_id)
        session = str(record["session"])
        pane_pid = int(record["pane_pid"])
        pane_start_ticks = str(record["pane_start_ticks"])
        if _proc_start_ticks(pane_pid) != pane_start_ticks:
            return False
        try:
            os.kill(pane_pid, signal.SIGTERM)
        except ProcessLookupError:
            return False
        process_result_path = self._launch_dir(run_id) / "process-result.json"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process_result_path.is_file():
                break
            time.sleep(0.05)
        else:
            raise AgyWorkflowError(
                "AGY supervisor did not publish exact-tree cleanup after cancellation"
            )
        process_result = json.loads(process_result_path.read_text(encoding="utf-8"))
        if process_result.get(
            "process_tree_cleanup_verified"
        ) is not True or process_result.get("surviving_process_identities"):
            raise AgyWorkflowError("AGY exact process tree survived cancellation")
        tmux = str(self._config.get("tmux", "/usr/bin/tmux"))
        subprocess.run(
            [tmux, "kill-session", "-t", session],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if _proc_start_ticks(pane_pid) != pane_start_ticks:
                break
            time.sleep(0.05)
        else:
            raise AgyWorkflowError("AGY exact pane identity survived cancellation")
        cancel_receipt_path = self._launch_dir(run_id) / "cancel-receipt.json"
        _atomic_json(
            cancel_receipt_path,
            {
                "run_id": run_id,
                "session": session,
                "cancelled_at": time.time(),
                "exact_pane_cleanup": True,
                "exact_process_tree_cleanup": True,
                "observed_process_count": process_result.get("observed_process_count"),
                "operator_or_policy_action_required": True,
            },
        )
        reconcile_terminal_run(self._launch_dir(run_id))
        return True

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
