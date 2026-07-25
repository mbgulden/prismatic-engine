"""Read-only dashboard projection for canonical AGY activity receipts."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _process_identity_alive(pid: object, start_ticks: object) -> bool:
    if type(pid) is not int or not isinstance(start_ticks, str):
        return False
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = raw[raw.rfind(")") + 2 :].split()
    except (OSError, IndexError):
        return False
    return fields[19] == start_ticks and fields[0] != "Z"


def resolve_activity_runtime_dir(path: str | Path | None = None) -> Path | None:
    raw = str(path) if path is not None else os.environ.get("PRISMATIC_AGY_RUNTIME_DIR")
    if not raw:
        return None
    resolved = Path(raw).expanduser().resolve()
    return resolved if resolved.is_dir() else None


def list_agy_activity_runs(
    runtime_dir: str | Path | None = None, *, limit: int = 50
) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    root = resolve_activity_runtime_dir(runtime_dir)
    if root is None:
        return {
            "status": "unavailable",
            "source": "canonical-agy-activity-receipts",
            "runtime_deadline": None,
            "automatic_kill": False,
            "runs": [],
            "reason": "PRISMATIC_AGY_RUNTIME_DIR is not configured",
        }
    now = time.time()
    runs: list[dict[str, Any]] = []
    for run_dir in root.iterdir():
        if not run_dir.is_dir() or not _RUN_ID_RE.fullmatch(run_dir.name):
            continue
        harness_record = _read_json(run_dir / "harness-run.json")
        launch_receipt = _read_json(run_dir / "launch-receipt.json")
        manifest = _read_json(run_dir / "manifest.json")
        record = harness_record or manifest
        if record is None or launch_receipt is None:
            continue
        raw_admission = record.get("admission")
        admission: dict[str, Any] = (
            raw_admission if isinstance(raw_admission, dict) else {}
        )
        started_at = record.get("started_at") or launch_receipt.get("started_at_unix")
        event_id = record.get("event_id") or admission.get("event_id")
        attempt = record.get("attempt") or admission.get("attempt")
        task_ref = record.get("task_ref") or event_id or record.get("identifier")
        activity = _read_json(run_dir / "activity.json") or {
            "classification": "starting",
            "process_alive": True,
            "observed_at_unix": started_at,
            "runtime_deadline": None,
            "automatic_kill": False,
            "metrics": {},
        }
        process = _read_json(run_dir / "process-result.json")
        cancelled = _read_json(run_dir / "cancel-receipt.json")
        pane_identity_alive = _process_identity_alive(
            launch_receipt.get("pane_pid"), launch_receipt.get("pane_start_ticks")
        )
        if cancelled is not None:
            state = "cancelled"
        elif process is not None:
            state = "completed" if process.get("exit_code") == 0 else "failed"
        elif activity.get("process_alive") and pane_identity_alive:
            state = "running"
        else:
            state = "orphaned"
            activity = {**activity, "classification": "stale_unverified"}
        observed = activity.get("observed_at_unix")
        age = round(max(0.0, now - float(observed)), 3) if observed else None
        runs.append(
            {
                "run_id": run_dir.name,
                "task_ref": task_ref,
                "state": state,
                "started_at_unix": started_at,
                "completed_at_unix": (
                    cancelled.get("cancelled_at")
                    if cancelled is not None
                    else process.get("finished_at_unix")
                    if process is not None
                    else None
                ),
                "activity": {
                    "classification": activity.get("classification", "unknown"),
                    "observed_at_unix": observed,
                    "last_progress_at_unix": activity.get("last_progress_at_unix"),
                    "receipt_age_seconds": age,
                    "quiet_seconds": activity.get("quiet_seconds"),
                    "process_alive": bool(activity.get("process_alive")),
                    "pane_identity_verified_alive": pane_identity_alive,
                    "metrics": activity.get("metrics", {}),
                },
                "runtime_deadline": None,
                "automatic_kill": False,
                "verification_status": record.get("verification_status", "pending"),
                "result_exists": Path(str(record.get("result_path") or "")).is_file(),
                "plan_exists": Path(str(record.get("plan_path") or "")).is_file(),
                "attempt": attempt,
                "event_id": event_id,
            }
        )
    runs.sort(key=lambda item: float(item.get("started_at_unix") or 0), reverse=True)
    selected = runs[:limit]
    counts: dict[str, int] = {}
    for run in selected:
        label = str(run["activity"]["classification"])
        counts[label] = counts.get(label, 0) + 1
    return {
        "status": "ok",
        "source": "canonical-agy-activity-receipts",
        "runtime_deadline": None,
        "automatic_kill": False,
        "activity_counts": counts,
        "runs": selected,
    }
