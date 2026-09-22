"""DeployRecord schema and persistence for the Deploy Hook (WB-6).

Corresponds to §5.2 of okf-docs-workspace-deploy-v1.md.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def default_deploy_db_path() -> Path:
    """Resolve deploy records json path.

    Priority:
    1. PRISMATIC_DEPLOY_DB env var
    2. ~/.prismatic/db/deploy_records.json
    3. ./prismatic_state/deploy_records.json (fallback)
    """
    env_path = os.environ.get("PRISMATIC_DEPLOY_DB")
    if env_path:
        return Path(env_path).expanduser()

    db_dir = Path("~/.prismatic/db").expanduser()
    if db_dir.exists() or db_dir.parent.exists():
        db_dir.mkdir(parents=True, exist_ok=True)
        return db_dir / "deploy_records.json"

    fallback = Path("./prismatic_state")
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback / "deploy_records.json"


@dataclass
class DeployRecord:
    """Canonical record of a production deployment."""

    deploy_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    pr_sha: str = ""
    pr_number: int = 0
    pr_title: str = ""
    merged_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    deployed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    deployer: str = "github-action"
    version_dir: str = ""
    release_symlink: str = ""
    health_check: dict[str, Any] = field(default_factory=dict)
    linear_transitions: list[dict[str, Any]] = field(default_factory=list)
    gateway_deploy: dict[str, Any] = field(default_factory=dict)
    mirror_refresh: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0
    success: bool = True
    failure_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DeployRecord:
        return cls(
            deploy_id=d.get("deploy_id", str(uuid.uuid4())),
            pr_sha=d.get("pr_sha", ""),
            pr_number=d.get("pr_number", 0),
            pr_title=d.get("pr_title", ""),
            merged_at=d.get("merged_at", datetime.now(timezone.utc).isoformat()),
            deployed_at=d.get("deployed_at", datetime.now(timezone.utc).isoformat()),
            deployer=d.get("deployer", "github-action"),
            version_dir=d.get("version_dir", ""),
            release_symlink=d.get("release_symlink", ""),
            health_check=d.get("health_check", {}),
            linear_transitions=d.get("linear_transitions", []),
            gateway_deploy=d.get("gateway_deploy", {}),
            mirror_refresh=d.get("mirror_refresh", {}),
            duration_ms=d.get("duration_ms", 0),
            success=d.get("success", True),
            failure_reason=d.get("failure_reason"),
        )


class DeployManifestStore:
    """Manages reading and writing DeployRecord items to JSON storage."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or default_deploy_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def record_deploy(self, record: DeployRecord) -> None:
        """Append a DeployRecord to storage."""
        records = self.list_deploys(limit=1000)
        records.append(record)
        
        data = [r.to_dict() for r in records]
        self.db_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def list_deploys(self, limit: int = 50) -> list[DeployRecord]:
        """List recent deploy records."""
        if not self.db_path.exists():
            return []

        try:
            raw = json.loads(self.db_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                records = [DeployRecord.from_dict(d) for d in raw]
                return sorted(records, key=lambda r: r.deployed_at, reverse=True)[:limit]
        except Exception:
            pass

        return []

    def get_latest(self) -> DeployRecord | None:
        """Get the most recent deploy record."""
        deploys = self.list_deploys(limit=1)
        return deploys[0] if deploys else None
