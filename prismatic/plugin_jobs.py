from __future__ import annotations

import json
import os
import tempfile
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from prismatic.plugin_architecture import plugin_catalog

JOB_STATUSES = {
    "queued",
    "running",
    "needs_approval",
    "completed",
    "failed",
    "cancelled",
    "rejected",
}
APPROVAL_STATES = {"not_required", "pending", "approved", "rejected"}
EVENT_TYPES = {
    "job_created",
    "policy_checked",
    "approval_required",
    "approved",
    "rejected",
    "started",
    "artifact_emitted",
    "completed",
    "failed",
    "cancelled",
    "note_added",
}
PLUGIN_JOB_SCHEMA_VERSION = "1.0.0"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_state_dir() -> Path:
    return Path(os.environ.get("PRISMATIC_STATE_DIR", repo_root() / "prismatic_state")).expanduser()


def default_jobs_path() -> Path:
    return Path(os.environ.get("PRISMATIC_PLUGIN_JOBS_STATE", default_state_dir() / "plugin_jobs.json")).expanduser()


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": PLUGIN_JOB_SCHEMA_VERSION,
        "plugin_jobs": {},
        "plugin_job_events": {},
        "plugin_artifacts": {},
    }


def _safe_text(value: Any, max_len: int = 1000) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text[:max_len]


def _looks_secret(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_looks_secret(v) for v in value.values())
    if isinstance(value, list):
        return any(_looks_secret(v) for v in value)
    if not isinstance(value, str):
        return False
    upper = value.upper()
    if upper.endswith("_ENV") or (upper.isidentifier() and any(token in upper for token in ["API_KEY", "TOKEN", "SECRET", "PASSWORD"])):
        return False
    return any(token in value for token in ["sk-", "ghp_", "xoxb-", "AIza", "-----BEGIN", "Bearer "])


def _redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, val in value.items():
            key_lower = str(key).lower()
            if any(token in key_lower for token in ["secret", "token", "password", "api_key", "apikey", "authorization"]):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = _redact_secrets(val)
        return redacted
    if isinstance(value, list):
        return [_redact_secrets(v) for v in value]
    if _looks_secret(value):
        return "[REDACTED]"
    return value


def _catalog_item(plugin_name: str) -> dict[str, Any] | None:
    catalog = plugin_catalog(repo_root() / "plugins")
    return next((item for item in catalog.get("plugins", []) if item.get("name") == plugin_name), None)


def evaluate_plugin_policy(
    plugin_name: str,
    action: str,
    *,
    requested_approval_required: bool | None = None,
    input_summary: Any | None = None,
) -> dict[str, Any]:
    """Evaluate the PE-owned policy gate for a plugin job request.

    This is intentionally conservative and generic. Domain plugins can add richer
    policy later, but every plugin job receives a durable policy decision now.
    """
    item = _catalog_item(plugin_name)
    checks: list[dict[str, Any]] = []
    blockers: list[str] = []
    warnings: list[str] = []
    approval_reasons: list[str] = []

    if item is None:
        blockers.append(f"unknown plugin: {plugin_name}")
        risk_level = "unknown"
        governance: dict[str, Any] = {}
    else:
        governance = item.get("governance", {})
        risk_level = governance.get("risk_level", "unknown")
        for blocker in governance.get("production_blockers", []):
            message = str(blocker.get("message", "plugin governance blocker"))
            if blocker.get("severity") == "blocking":
                blockers.append(message)
            else:
                warnings.append(message)
        if governance.get("approval_gates"):
            approval_reasons.extend(governance.get("approval_gates", []))

    action_lower = action.lower()
    if any(token in action_lower for token in ["publish", "export", "deploy", "delete", "destroy", "costly", "batch"]):
        approval_reasons.append(f"action '{action}' requires operator approval")
    if risk_level in {"high", "critical"}:
        approval_reasons.append(f"plugin risk level is {risk_level}")
    if requested_approval_required is True:
        approval_reasons.append("request explicitly required approval")
    if _looks_secret(input_summary):
        blockers.append("job input appears to contain raw secret material")

    approval_required = bool(approval_reasons)
    allowed = not blockers
    checks.append({"name": "plugin_exists", "passed": item is not None})
    checks.append({"name": "manifest_blockers", "passed": not any("governance" in b for b in blockers)})
    checks.append({"name": "secret_redaction", "passed": not _looks_secret(input_summary)})
    checks.append({"name": "approval_gate", "passed": True, "approval_required": approval_required})

    return {
        "allowed": allowed,
        "risk_level": risk_level,
        "approval_required": approval_required,
        "approval_reasons": sorted(set(approval_reasons)),
        "blockers": blockers,
        "warnings": warnings,
        "checks": checks,
        "evaluated_at": now_iso(),
    }


class PluginJobStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or default_jobs_path()).expanduser()

    def load_state(self) -> dict[str, Any]:
        if not self.path.exists():
            return empty_state()
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return empty_state()
        state = empty_state()
        if isinstance(loaded, dict):
            state.update({k: loaded.get(k, v) for k, v in state.items()})
        for key in ["plugin_jobs", "plugin_job_events", "plugin_artifacts"]:
            if not isinstance(state.get(key), dict):
                state[key] = {}
        return state

    def save_state(self, state: dict[str, Any]) -> None:
        state = deepcopy(state)
        state["schema_version"] = PLUGIN_JOB_SCHEMA_VERSION
        _atomic_write_json(self.path, state)

    def create_job(
        self,
        plugin_name: str,
        action: str,
        *,
        actor: str = "system",
        source: str = "api",
        input_summary: Any | None = None,
        operator_notes: str | None = None,
        approval_required: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        timestamp = now_iso()
        policy = evaluate_plugin_policy(
            plugin_name,
            action,
            requested_approval_required=approval_required,
            input_summary=input_summary,
        )
        job_id = f"plugjob_{uuid.uuid4().hex[:16]}"
        approval_state = "pending" if policy["approval_required"] else "not_required"
        status = "needs_approval" if policy["approval_required"] else "queued"
        if not policy["allowed"]:
            status = "failed"
        job = {
            "job_id": job_id,
            "plugin_name": plugin_name,
            "action": action,
            "status": status,
            "risk_level": policy["risk_level"],
            "approval_required": policy["approval_required"],
            "approval_state": approval_state,
            "policy_result": policy,
            "input_summary": _redact_secrets(input_summary),
            "created_by": actor,
            "source": source,
            "operator_notes": _safe_text(operator_notes),
            "metadata": _redact_secrets(metadata or {}),
            "artifact_ids": [],
            "created_at": timestamp,
            "updated_at": timestamp,
            "completed_at": None,
            "error": None if policy["allowed"] else "; ".join(policy["blockers"]),
        }
        state = self.load_state()
        state["plugin_jobs"][job_id] = job
        state["plugin_job_events"][job_id] = []
        self._append_event_unlocked(
            state,
            job_id,
            "job_created",
            actor=actor,
            source=source,
            message=f"Created plugin job {plugin_name}:{action}",
            details={"status": status},
        )
        self._append_event_unlocked(
            state,
            job_id,
            "policy_checked",
            actor="policy",
            source="policy_gate",
            message="Evaluated generic plugin policy gate",
            details=policy,
        )
        if policy["approval_required"]:
            self._append_event_unlocked(
                state,
                job_id,
                "approval_required",
                actor="policy",
                source="policy_gate",
                message="Operator approval required before execution",
                details={"approval_reasons": policy["approval_reasons"]},
            )
        if not policy["allowed"]:
            self._append_event_unlocked(
                state,
                job_id,
                "failed",
                actor="policy",
                source="policy_gate",
                message=job["error"] or "Policy gate blocked job",
                details={"blockers": policy["blockers"]},
            )
        self.save_state(state)
        return self.get_job(job_id) or job

    def list_jobs(self, *, plugin_name: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        jobs = list(self.load_state()["plugin_jobs"].values())
        if plugin_name:
            jobs = [job for job in jobs if job.get("plugin_name") == plugin_name]
        if status:
            jobs = [job for job in jobs if job.get("status") == status]
        return sorted(jobs, key=lambda job: job.get("created_at") or "", reverse=True)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        state = self.load_state()
        job = state["plugin_jobs"].get(job_id)
        if not job:
            return None
        payload = deepcopy(job)
        payload["events"] = state["plugin_job_events"].get(job_id, [])
        payload["artifacts"] = [state["plugin_artifacts"].get(aid) for aid in payload.get("artifact_ids", []) if state["plugin_artifacts"].get(aid)]
        return payload

    def approve_job(self, job_id: str, *, actor: str = "operator", note: str | None = None) -> dict[str, Any] | None:
        return self._set_approval(job_id, "approved", "queued", actor=actor, note=note, event_type="approved")

    def reject_job(self, job_id: str, *, actor: str = "operator", note: str | None = None) -> dict[str, Any] | None:
        return self._set_approval(job_id, "rejected", "rejected", actor=actor, note=note, event_type="rejected")

    def _set_approval(self, job_id: str, approval_state: str, status: str, *, actor: str, note: str | None, event_type: str) -> dict[str, Any] | None:
        state = self.load_state()
        job = state["plugin_jobs"].get(job_id)
        if not job:
            return None
        timestamp = now_iso()
        job["approval_state"] = approval_state
        job["status"] = status
        job["updated_at"] = timestamp
        if status == "rejected":
            job["completed_at"] = timestamp
        if note:
            job["operator_notes"] = _safe_text(note)
        self._append_event_unlocked(state, job_id, event_type, actor=actor, source="operator", message=note or f"Job {event_type}", details={"approval_state": approval_state})
        self.save_state(state)
        return self.get_job(job_id)

    def update_status(self, job_id: str, status: str, *, actor: str = "system", message: str | None = None, error: str | None = None) -> dict[str, Any] | None:
        if status not in JOB_STATUSES:
            raise ValueError(f"unsupported job status: {status}")
        state = self.load_state()
        job = state["plugin_jobs"].get(job_id)
        if not job:
            return None
        job["status"] = status
        job["updated_at"] = now_iso()
        if status in {"completed", "failed", "cancelled", "rejected"}:
            job["completed_at"] = job["updated_at"]
        if error:
            job["error"] = _safe_text(error, 2000)
        event_type = "completed" if status == "completed" else "failed" if status == "failed" else "cancelled" if status == "cancelled" else "started" if status == "running" else "note_added"
        self._append_event_unlocked(state, job_id, event_type, actor=actor, source="job_status", message=message or f"Job status set to {status}", details={"status": status, "error": error})
        self.save_state(state)
        return self.get_job(job_id)

    def append_event(
        self,
        job_id: str,
        event_type: str,
        *,
        actor: str = "system",
        source: str = "api",
        message: str | None = None,
        details: dict[str, Any] | None = None,
        artifact: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        state = self.load_state()
        if job_id not in state["plugin_jobs"]:
            return None
        self._append_event_unlocked(state, job_id, event_type, actor=actor, source=source, message=message, details=details, artifact=artifact)
        state["plugin_jobs"][job_id]["updated_at"] = now_iso()
        self.save_state(state)
        return self.get_job(job_id)

    def _append_event_unlocked(
        self,
        state: dict[str, Any],
        job_id: str,
        event_type: str,
        *,
        actor: str,
        source: str,
        message: str | None = None,
        details: dict[str, Any] | None = None,
        artifact: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if event_type not in EVENT_TYPES:
            event_type = "note_added"
        event_id = f"plugevt_{uuid.uuid4().hex[:16]}"
        event = {
            "event_id": event_id,
            "job_id": job_id,
            "event_type": event_type,
            "actor": actor,
            "source": source,
            "message": _safe_text(message),
            "details": _redact_secrets(details or {}),
            "created_at": now_iso(),
        }
        if artifact:
            artifact_id = str(artifact.get("artifact_id") or f"plugart_{uuid.uuid4().hex[:16]}")
            artifact_record = {
                "artifact_id": artifact_id,
                "job_id": job_id,
                "plugin_name": state["plugin_jobs"].get(job_id, {}).get("plugin_name"),
                "artifact_type": artifact.get("artifact_type"),
                "path_or_url": artifact.get("path_or_url"),
                "metadata": _redact_secrets(artifact.get("metadata", {})),
                "approval_state": artifact.get("approval_state", "pending"),
                "created_at": now_iso(),
            }
            state["plugin_artifacts"][artifact_id] = artifact_record
            state["plugin_jobs"].setdefault(job_id, {}).setdefault("artifact_ids", []).append(artifact_id)
            event["artifact_id"] = artifact_id
        state["plugin_job_events"].setdefault(job_id, []).append(event)
        return event

    def summary(self) -> dict[str, Any]:
        state = self.load_state()
        jobs = list(state["plugin_jobs"].values())
        by_status: dict[str, int] = {}
        by_plugin: dict[str, int] = {}
        for job in jobs:
            by_status[job.get("status", "unknown")] = by_status.get(job.get("status", "unknown"), 0) + 1
            by_plugin[job.get("plugin_name", "unknown")] = by_plugin.get(job.get("plugin_name", "unknown"), 0) + 1
        return {
            "schema_version": state["schema_version"],
            "state_path": str(self.path),
            "job_count": len(jobs),
            "event_count": sum(len(events) for events in state["plugin_job_events"].values()),
            "artifact_count": len(state["plugin_artifacts"]),
            "by_status": by_status,
            "by_plugin": by_plugin,
        }


def store_from_env() -> PluginJobStore:
    return PluginJobStore()
