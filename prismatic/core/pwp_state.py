"""
prismatic/core/pwp_state.py - PWP run-state JSON store and rollback handler.

Manages PWP deployment history and execution of rollback operations.
"""

from __future__ import annotations

import json
import os
import sys
import fcntl
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("prismatic.core.pwp_state")


@dataclass
class PWPRunState:
    """Run-state of a single PWP deployment."""

    run_id: str
    client_id: str
    target: str
    artifact_sha: str
    deployed_at: str
    deployed_by: str
    previous_artifact_sha: Optional[str] = None
    reversible: bool = False
    commit_hash: Optional[str] = None
    theme_hash: Optional[str] = None
    content_hash: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PWPRunState:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class PWPRunStateStore:
    """Thread-safe JSON-backed store for PWP run states."""

    def __init__(self, store_path: Optional[str] = None):
        state_dir = os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state/")
        self._store_path = store_path or os.path.join(state_dir, "pwp_run_state.json")
        Path(self._store_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock_file_path = self._store_path + ".lock"
        self._records: Dict[str, PWPRunState] = {}
        self._load_from_disk()

    def _acquire_lock(self) -> int:
        fd = os.open(self._lock_file_path, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd

    def _release_lock(self, fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    def _load_from_disk(self) -> None:
        if not os.path.exists(self._store_path):
            self._records = {}
            return
        fd = self._acquire_lock()
        try:
            try:
                with open(self._store_path) as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                data = {}

            if isinstance(data, dict):
                self._records = {k: PWPRunState.from_dict(v) for k, v in data.items()}
            else:
                self._records = {}
        except Exception:
            self._records = {}
        finally:
            self._release_lock(fd)

    def _flush_to_disk(self) -> None:
        fd = self._acquire_lock()
        try:
            serialised = {k: asdict(v) for k, v in self._records.items()}
            with open(self._store_path, "w") as f:
                json.dump(serialised, f, indent=2, default=str)
        finally:
            self._release_lock(fd)

    def get_run(self, run_id: str) -> Optional[PWPRunState]:
        self._load_from_disk()
        return self._records.get(run_id)

    def all_records(self) -> List[PWPRunState]:
        self._load_from_disk()
        return list(self._records.values())

    def latest_deploy(self, client_id: str, target: str) -> Optional[PWPRunState]:
        """Return the newest deployment record for a client/target pair."""
        self._load_from_disk()
        matches = [
            r
            for r in self._records.values()
            if r.client_id == client_id and r.target == target
        ]
        if not matches:
            return None
        return max(matches, key=lambda r: r.deployed_at)

    def should_skip_deploy(
        self,
        client_id: str,
        target: str,
        commit_hash: Optional[str] = None,
        theme_hash: Optional[str] = None,
        content_hash: Optional[str] = None,
    ) -> bool:
        """
        Return True when the latest successful deploy has matching hashes.

        Hash-free deploy contexts are deliberately not idempotent: without at
        least one meaningful hash, the runner cannot prove the output is the
        same artifact, so it deploys rather than guessing.
        """
        if commit_hash is None and theme_hash is None and content_hash is None:
            return False

        latest = self.latest_deploy(client_id=client_id, target=target)
        if latest is None:
            return False

        return (
            latest.commit_hash == commit_hash
            and latest.theme_hash == theme_hash
            and latest.content_hash == content_hash
        )

    def record_deploy(
        self,
        run_id: str,
        client_id: str,
        target: str,
        artifact_sha: str,
        deployed_by: str,
        reversible: bool = False,
        commit_hash: Optional[str] = None,
        theme_hash: Optional[str] = None,
        content_hash: Optional[str] = None,
    ) -> PWPRunState:
        self._load_from_disk()

        # Find previous artifact sha for this client and target
        existing = [
            r
            for r in self._records.values()
            if r.client_id == client_id and r.target == target
        ]
        previous_artifact_sha = None
        if existing:
            existing.sort(key=lambda r: r.deployed_at, reverse=True)
            previous_artifact_sha = existing[0].artifact_sha

        deployed_at = datetime.now(timezone.utc).isoformat()

        record = PWPRunState(
            run_id=run_id,
            client_id=client_id,
            target=target,
            artifact_sha=artifact_sha,
            deployed_at=deployed_at,
            deployed_by=deployed_by,
            previous_artifact_sha=previous_artifact_sha,
            reversible=reversible,
            commit_hash=commit_hash,
            theme_hash=theme_hash,
            content_hash=content_hash,
        )
        self._records[run_id] = record

        # FIFO eviction: max 10 historical versions per client
        client_records = [r for r in self._records.values() if r.client_id == client_id]
        if len(client_records) > 10:
            client_records.sort(key=lambda r: r.deployed_at)
            to_evict = client_records[:-10]
            for r in to_evict:
                if r.run_id in self._records:
                    del self._records[r.run_id]

        self._flush_to_disk()
        return record


def handle_rollback(run_id: str, reason: str = "Rollback requested via CLI") -> None:
    """CLI handler for rolling back a specific run ID."""
    from prismatic.core.deploy_adapters import cloudflare, http, file

    store = PWPRunStateStore()
    record = store.get_run(run_id)
    if not record:
        print(f"Error: Run ID '{run_id}' not found in PWP run states.")
        sys.exit(1)

    if not record.reversible:
        print(
            f"Error: Run '{run_id}' is not reversible (reversible field in manifest must be true)."
        )
        sys.exit(1)

    previous_sha = record.previous_artifact_sha
    if not previous_sha:
        print(
            f"Error: Run '{run_id}' does not have a previous_artifact_sha to roll back to."
        )
        sys.exit(1)

    target = record.target
    context = {
        "run_id": run_id,
        "client_id": record.client_id,
        "deployed_by": record.deployed_by,
        "reason": reason,
    }

    success = False
    if target in ("cloudflare-pages", "cloudflare"):
        success = cloudflare.undo(previous_sha, context)
    elif target == "http":
        success = http.undo(previous_sha, context)
    elif target == "file":
        success = file.undo(previous_sha, context)
    else:
        print(f"Error: Unsupported deployment target '{target}' for rollback.")
        sys.exit(1)

    if not success:
        print(f"Error: Rollback for run '{run_id}' via adapter '{target}' failed.")
        sys.exit(1)

    # Record the rollback in the audit log
    state_dir = os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state/")
    audit_log_path = os.path.join(state_dir, "pwp_audit.log")
    Path(audit_log_path).parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).isoformat()
    audit_entry = {
        "timestamp": timestamp,
        "event": "rollback",
        "run_id": run_id,
        "target": target,
        "previous_artifact_sha": previous_sha,
        "reason": reason,
        "status": "success",
    }

    with open(audit_log_path, "a") as f:
        f.write(json.dumps(audit_entry) + "\n")

    # Log to global rollback log if writable
    home_dir = os.path.expanduser("~")
    cli_rollback_log = os.path.join(home_dir, ".prismatic/logs/rollback.log")
    try:
        Path(cli_rollback_log).parent.mkdir(parents=True, exist_ok=True)
        with open(cli_rollback_log, "a") as f:
            f.write(
                f"[{timestamp}] Rollback run {run_id} to {previous_sha} (reason: {reason}) - SUCCESS\n"
            )
    except Exception:
        pass

    print(
        f"Successfully rolled back run '{run_id}' to previous SHA '{previous_sha}' via '{target}' adapter."
    )
