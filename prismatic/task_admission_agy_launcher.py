"""Thin task-admission adapter for the canonical durable AGY harness."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

from prismatic.agy_cli import (
    CANONICAL_ADMISSION_MARKER,
    CANONICAL_AGY_WORKFLOW_VERSION,
    CANONICAL_TRANSPORT,
)
from prismatic.harnesses.agy_cli import AGYCLIHarness
from prismatic.task_admission import _read_policy_bytes

MAX_REQUEST_BYTES = 32 * 1024
MAX_CONFIG_BYTES = 128 * 1024
CONFIG_ENV = "PRISMATIC_TASK_ADMISSION_AGY_CONFIG"
_EVENT_RE = re.compile(r"task-admission:[0-9a-f]{64}\Z")
_CLAIM_RE = re.compile(r"[0-9a-f]{32}\Z")
_SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
_REQUEST_KEYS = {
    "event_id",
    "idempotency_key",
    "claim_id",
    "attempt",
    "task_id",
    "producer_identity",
    "base_commit",
    "base_tree",
    "task_file_sha256",
    "writer_cap",
    "worktree",
    "task_file",
    "actor",
}
_BINDING_KEYS = (
    "producer_identity",
    "base_commit",
    "base_tree",
    "task_file_sha256",
    "worktree",
    "task_file",
)


class TaskAdmissionAgyLauncherError(RuntimeError):
    """Fail-closed adapter validation error."""


def _duplicates_fail(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TaskAdmissionAgyLauncherError("duplicate_json_key")
        result[key] = value
    return result


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()


def _digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _read_private_file(path: Path, *, max_bytes: int, label: str) -> bytes:
    try:
        raw = _read_policy_bytes(path)
    except (OSError, ValueError) as exc:
        raise TaskAdmissionAgyLauncherError(f"{label}_unavailable") from exc
    if not raw or len(raw) > max_bytes:
        raise TaskAdmissionAgyLauncherError(f"{label}_size_invalid")
    return raw


def parse_request(raw: bytes) -> dict[str, Any]:
    if not raw or len(raw) > MAX_REQUEST_BYTES:
        raise TaskAdmissionAgyLauncherError("request_size_invalid")
    try:
        request = json.loads(raw, object_pairs_hook=_duplicates_fail)
    except TaskAdmissionAgyLauncherError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TaskAdmissionAgyLauncherError("request_invalid_json") from exc
    if not isinstance(request, dict) or set(request) != _REQUEST_KEYS:
        raise TaskAdmissionAgyLauncherError("request_shape_invalid")
    if (
        not isinstance(request["event_id"], str)
        or _EVENT_RE.fullmatch(request["event_id"]) is None
        or request["idempotency_key"] != request["event_id"]
    ):
        raise TaskAdmissionAgyLauncherError("event_binding_invalid")
    if (
        not isinstance(request["claim_id"], str)
        or _CLAIM_RE.fullmatch(request["claim_id"]) is None
        or type(request["attempt"]) is not int
        or not 1 <= request["attempt"] <= 3
    ):
        raise TaskAdmissionAgyLauncherError("claim_binding_invalid")
    if type(request["writer_cap"]) is not int or request["writer_cap"] != 1:
        raise TaskAdmissionAgyLauncherError("writer_cap_invalid")
    if (
        not isinstance(request["task_file_sha256"], str)
        or _SHA_RE.fullmatch(request["task_file_sha256"]) is None
    ):
        raise TaskAdmissionAgyLauncherError("task_digest_invalid")
    if any(
        not isinstance(request[key], str) or not request[key] for key in _BINDING_KEYS
    ):
        raise TaskAdmissionAgyLauncherError("request_binding_invalid")
    task_file = Path(request["task_file"])
    if task_file.is_absolute() or ".." in task_file.parts:
        raise TaskAdmissionAgyLauncherError("task_file_invalid")
    return request


def load_config(path: Path) -> dict[str, Any]:
    if not path.is_absolute():
        raise TaskAdmissionAgyLauncherError("config_path_invalid")
    try:
        config = json.loads(
            _read_private_file(path, max_bytes=MAX_CONFIG_BYTES, label="config"),
            object_pairs_hook=_duplicates_fail,
        )
    except TaskAdmissionAgyLauncherError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TaskAdmissionAgyLauncherError("config_invalid") from exc
    if (
        not isinstance(config, dict)
        or set(config) != {"version", "harness", "tasks"}
        or config["version"] != 1
        or not isinstance(config["harness"], dict)
        or config["harness"].get("concurrent_runs") != 1
        or not isinstance(config["tasks"], dict)
    ):
        raise TaskAdmissionAgyLauncherError("config_shape_invalid")
    return config


def _bound_task(
    request: Mapping[str, Any], config: Mapping[str, Any]
) -> dict[str, Any]:
    task = config["tasks"].get(request["task_id"])
    if not isinstance(task, dict):
        raise TaskAdmissionAgyLauncherError("task_not_configured")
    if any(task.get(key) != request[key] for key in _BINDING_KEYS):
        raise TaskAdmissionAgyLauncherError("task_binding_mismatch")
    model = task.get("model")
    models = config["harness"].get("models")
    if (
        not isinstance(model, str)
        or not isinstance(models, list)
        or model not in models
    ):
        raise TaskAdmissionAgyLauncherError("task_model_invalid")
    if task.get("sandbox") is not True:
        raise TaskAdmissionAgyLauncherError("task_sandbox_invalid")
    return task


def _private_directory(path: Path, *, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise TaskAdmissionAgyLauncherError(f"{label}_unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise TaskAdmissionAgyLauncherError(f"{label}_unsafe")


def _write_receipt(path: Path, payload: Mapping[str, Any]) -> None:
    expected = _canonical(payload)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _private_directory(path.parent, label="admission_receipt_parent")
    try:
        fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
    except FileExistsError:
        if (
            _read_private_file(
                path, max_bytes=MAX_CONFIG_BYTES, label="admission_receipt"
            )
            != expected
        ):
            raise TaskAdmissionAgyLauncherError("admission_receipt_conflict")
        return
    with os.fdopen(fd, "wb") as handle:
        handle.write(expected)
        handle.flush()
        os.fsync(handle.fileno())


def _recover_durable_launch(
    runtime_dir: Path, request: Mapping[str, Any], run_id: str
) -> dict[str, Any] | None:
    launch_dir = runtime_dir / run_id
    if not launch_dir.exists():
        return None
    _private_directory(launch_dir, label="launch_directory")
    try:
        receipt = json.loads(
            _read_private_file(
                launch_dir / "launch-receipt.json",
                max_bytes=MAX_CONFIG_BYTES,
                label="launch_receipt",
            ),
            object_pairs_hook=_duplicates_fail,
        )
    except TaskAdmissionAgyLauncherError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TaskAdmissionAgyLauncherError("launch_receipt_invalid") from exc
    required = {
        "accepted": True,
        "workflow_version": CANONICAL_AGY_WORKFLOW_VERSION,
        "transport": CANONICAL_TRANSPORT,
        "identifier": run_id,
        "event_id": request["event_id"],
        "task_sha256": request["task_file_sha256"],
        "runtime_deadline": None,
    }
    if not isinstance(receipt, dict) or any(
        receipt.get(k) != v for k, v in required.items()
    ):
        raise TaskAdmissionAgyLauncherError("launch_receipt_binding_mismatch")
    return {
        "accepted": True,
        "idempotency_key": request["event_id"],
        "launch_id": run_id,
    }


def launch_request(
    request: Mapping[str, Any],
    config: Mapping[str, Any],
    *,
    harness_factory: Callable[[dict[str, Any]], Any] = AGYCLIHarness,
) -> dict[str, Any]:
    task = _bound_task(request, config)
    harness_config = dict(config["harness"])
    runtime_dir = Path(str(harness_config.get("runtime_dir", "")))
    if not runtime_dir.is_absolute() or not runtime_dir.is_dir():
        raise TaskAdmissionAgyLauncherError("runtime_dir_invalid")
    _private_directory(runtime_dir, label="runtime_dir")

    event_digest = request["event_id"].split(":", 1)[1]
    run_id = f"agy-admission-{event_digest[:24]}"
    recovered = _recover_durable_launch(runtime_dir, request, run_id)
    if recovered is not None:
        return recovered

    token = _digest(
        {
            "event_id": request["event_id"],
            "claim_id": request["claim_id"],
            "attempt": request["attempt"],
            "task_sha256": request["task_file_sha256"],
            "agy_binary_sha256": harness_config.get("agy_binary_sha256"),
        }
    )
    admission = {
        "marker": CANONICAL_ADMISSION_MARKER,
        "authorized": True,
        "event_id": request["event_id"],
        "claim_id": request["claim_id"],
        "attempt": request["attempt"],
        "attempt_token": token,
        "task_sha256": request["task_file_sha256"],
        "agy_binary_sha256": harness_config.get("agy_binary_sha256"),
    }
    admission_path = runtime_dir / "admission-receipts" / f"{token}.json"
    _write_receipt(admission_path, admission)

    launched_run_id = harness_factory(harness_config).dispatch(
        {
            "run_id": run_id,
            "model": task["model"],
            "workspace": request["worktree"],
            "task_file": str(Path(request["worktree"]) / request["task_file"]),
            "admission_receipt": str(admission_path),
            "sandbox": True,
            "task_ref": request["task_id"],
            "producer_identity": request["producer_identity"],
        }
    )
    if launched_run_id != run_id:
        raise TaskAdmissionAgyLauncherError("launch_id_invalid")
    return {
        "accepted": True,
        "idempotency_key": request["event_id"],
        "launch_id": run_id,
    }


def main() -> int:
    try:
        request = parse_request(sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1))
        config_path = os.environ.get(CONFIG_ENV)
        if not config_path:
            raise TaskAdmissionAgyLauncherError("config_environment_missing")
        receipt = launch_request(request, load_config(Path(config_path)))
    except Exception as exc:
        print(f"launcher_error:{type(exc).__name__}", file=sys.stderr)
        return 2
    print(json.dumps(receipt, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
