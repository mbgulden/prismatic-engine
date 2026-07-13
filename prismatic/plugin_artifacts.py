from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ARTIFACT_SCHEMA_VERSION = "1.0.0"
APPROVAL_STATES = {"pending", "approved", "rejected", "not_required"}
PUBLISH_STATES = {"draft", "publish_ready", "published", "rejected", "archived"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_state_dir() -> Path:
    return Path(os.environ.get("PRISMATIC_STATE_DIR", repo_root() / "prismatic_state")).expanduser()


def default_artifacts_path() -> Path:
    return Path(os.environ.get("PRISMATIC_PLUGIN_ARTIFACTS_STATE", default_state_dir() / "plugin_artifacts.json")).expanduser()


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
    return {"schema_version": ARTIFACT_SCHEMA_VERSION, "plugin_artifacts": {}}


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


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, val in value.items():
            key_lower = str(key).lower()
            if any(token in key_lower for token in ["secret", "token", "password", "api_key", "apikey", "authorization"]):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_secrets(val)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(v) for v in value]
    if _looks_secret(value):
        return "[REDACTED]"
    return value


def is_external_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def safe_local_artifact_path(path_or_url: str | None) -> Path | None:
    """Return a resolved local path only if it is safe to inspect for hashing.

    External URLs are intentionally not fetched. Local files are only read when
    they live under the repo, the configured state dir, or /tmp. That keeps the
    provenance registry from becoming an arbitrary filesystem read primitive.
    """
    if not path_or_url or is_external_url(path_or_url):
        return None
    candidate = Path(path_or_url).expanduser()
    if not candidate.is_absolute():
        candidate = repo_root() / candidate
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        return None
    allowed_roots = [repo_root().resolve(), default_state_dir().resolve(), Path("/tmp").resolve()]
    if not any(resolved == root or root in resolved.parents for root in allowed_roots):
        return None
    return resolved


def file_fingerprint(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {"sha256": None, "size_bytes": None, "exists": False}
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return {"sha256": h.hexdigest(), "size_bytes": path.stat().st_size, "exists": True}


class PluginArtifactStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or default_artifacts_path()).expanduser()

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
        if not isinstance(state.get("plugin_artifacts"), dict):
            state["plugin_artifacts"] = {}
        return state

    def save_state(self, state: dict[str, Any]) -> None:
        state = deepcopy(state)
        state["schema_version"] = ARTIFACT_SCHEMA_VERSION
        _atomic_write_json(self.path, state)

    def create_artifact(
        self,
        *,
        plugin_name: str,
        job_id: str | None = None,
        artifact_type: str | None = None,
        mime_type: str | None = None,
        path_or_url: str | None = None,
        asset_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
        input_summary: Any | None = None,
        provider_or_service: str | None = None,
        approval_state: str = "pending",
        publish_state: str = "draft",
        artifact_id: str | None = None,
    ) -> dict[str, Any]:
        timestamp = now_iso()
        if approval_state not in APPROVAL_STATES:
            approval_state = "pending"
        if publish_state not in PUBLISH_STATES:
            publish_state = "draft"
        local_path = safe_local_artifact_path(path_or_url)
        fingerprint = file_fingerprint(local_path) if local_path else {"sha256": None, "size_bytes": None, "exists": False}
        artifact_id = artifact_id or f"plugart_{uuid.uuid4().hex[:16]}"
        record = {
            "artifact_id": artifact_id,
            "asset_id": asset_id or artifact_id,
            "plugin_name": plugin_name,
            "job_id": job_id,
            "artifact_type": artifact_type,
            "mime_type": mime_type,
            "path_or_url": path_or_url,
            "sha256": fingerprint["sha256"],
            "size_bytes": fingerprint["size_bytes"],
            "metadata": redact_secrets(metadata or {}),
            "provenance": redact_secrets({
                "source_plugin": plugin_name,
                "source_job": job_id,
                **(provenance or {}),
            }),
            "input_summary": redact_secrets(input_summary),
            "provider_or_service": provider_or_service,
            "approval_state": approval_state,
            "publish_state": publish_state,
            "export_history": [],
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        state = self.load_state()
        state["plugin_artifacts"][artifact_id] = record
        self.save_state(state)
        return deepcopy(record)

    def list_artifacts(self, *, plugin_name: str | None = None, job_id: str | None = None, approval_state: str | None = None, publish_state: str | None = None) -> list[dict[str, Any]]:
        artifacts = list(self.load_state()["plugin_artifacts"].values())
        if plugin_name:
            artifacts = [a for a in artifacts if a.get("plugin_name") == plugin_name]
        if job_id:
            artifacts = [a for a in artifacts if a.get("job_id") == job_id]
        if approval_state:
            artifacts = [a for a in artifacts if a.get("approval_state") == approval_state]
        if publish_state:
            artifacts = [a for a in artifacts if a.get("publish_state") == publish_state]
        return sorted(artifacts, key=lambda a: a.get("created_at") or "", reverse=True)

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        artifact = self.load_state()["plugin_artifacts"].get(artifact_id)
        return deepcopy(artifact) if artifact else None

    def set_approval(self, artifact_id: str, approval_state: str, *, actor: str = "operator", note: str | None = None) -> dict[str, Any] | None:
        if approval_state not in {"approved", "rejected"}:
            raise ValueError(f"unsupported approval state: {approval_state}")
        state = self.load_state()
        artifact = state["plugin_artifacts"].get(artifact_id)
        if not artifact:
            return None
        artifact["approval_state"] = approval_state
        if approval_state == "rejected":
            artifact["publish_state"] = "rejected"
        artifact["updated_at"] = now_iso()
        artifact.setdefault("metadata", {}).setdefault("operator_notes", []).append(redact_secrets({"actor": actor, "note": note, "at": artifact["updated_at"]}))
        self.save_state(state)
        return self.get_artifact(artifact_id)

    def mark_publish_ready(self, artifact_id: str, *, actor: str = "operator", note: str | None = None) -> dict[str, Any] | None:
        state = self.load_state()
        artifact = state["plugin_artifacts"].get(artifact_id)
        if not artifact:
            return None
        artifact["publish_state"] = "publish_ready"
        artifact["updated_at"] = now_iso()
        artifact.setdefault("export_history", []).append(redact_secrets({"event": "publish_ready", "actor": actor, "note": note, "at": artifact["updated_at"]}))
        self.save_state(state)
        return self.get_artifact(artifact_id)

    def add_export(self, artifact_id: str, *, target: str, actor: str = "operator", note: str | None = None) -> dict[str, Any] | None:
        state = self.load_state()
        artifact = state["plugin_artifacts"].get(artifact_id)
        if not artifact:
            return None
        artifact["updated_at"] = now_iso()
        artifact.setdefault("export_history", []).append(redact_secrets({"event": "export", "target": target, "actor": actor, "note": note, "at": artifact["updated_at"]}))
        self.save_state(state)
        return self.get_artifact(artifact_id)

    def summary(self) -> dict[str, Any]:
        artifacts = list(self.load_state()["plugin_artifacts"].values())
        by_plugin: dict[str, int] = {}
        by_approval: dict[str, int] = {}
        by_publish: dict[str, int] = {}
        for artifact in artifacts:
            by_plugin[artifact.get("plugin_name") or "unknown"] = by_plugin.get(artifact.get("plugin_name") or "unknown", 0) + 1
            by_approval[artifact.get("approval_state") or "unknown"] = by_approval.get(artifact.get("approval_state") or "unknown", 0) + 1
            by_publish[artifact.get("publish_state") or "unknown"] = by_publish.get(artifact.get("publish_state") or "unknown", 0) + 1
        return {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "state_path": str(self.path),
            "artifact_count": len(artifacts),
            "by_plugin": by_plugin,
            "by_approval_state": by_approval,
            "by_publish_state": by_publish,
        }


def store_from_env() -> PluginArtifactStore:
    return PluginArtifactStore()
