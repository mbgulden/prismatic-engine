"""W4: On-disk append-only audit log for deploy attempts.

Writes JSON Lines to ``~/.prismatic/db/deploy_audit.log`` recording timestamp,
actor, client IP, HMAC signature hash, rate-limit status, and action.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def default_audit_log_path() -> Path:
    state_dir = os.environ.get("PRISMATIC_STATE_DIR", "./prismatic_state")
    p = Path(state_dir).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p / "deploy_audit.log"


class DeployAuditLog:
    """Append-only audit logger for deploy events."""

    def __init__(self, log_path: Optional[Path] = None):
        self.log_path = log_path or default_audit_log_path()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def record_entry(
        self,
        action: str,
        actor: str = "unknown",
        client_ip: str = "",
        hmac_sig: str = "",
        status: str = "success",
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Record an immutable audit log entry."""
        now_iso = datetime.now(timezone.utc).isoformat()
        sig_hash = (
            hashlib.sha256(hmac_sig.encode("utf-8")).hexdigest() if hmac_sig else ""
        )
        entry = {
            "timestamp": now_iso,
            "epoch": time.time(),
            "action": action,
            "actor": actor,
            "client_ip": client_ip,
            "hmac_sig_hash": sig_hash,
            "status": status,
            "details": details or {},
        }
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as exc:
            logger.error(
                "Failed to write to deploy audit log %s: %s", self.log_path, exc
            )
        return entry

    def read_entries(self, limit: int = 100) -> list[Dict[str, Any]]:
        """Read recent audit log entries."""
        if not self.log_path.exists():
            return []
        entries = []
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
        except Exception as exc:
            logger.error("Failed to read deploy audit log %s: %s", self.log_path, exc)
        return entries[-limit:]
