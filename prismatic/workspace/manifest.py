"""WorkspaceManifestEntry schema for the Curated Workspace Plugin (WA-4).

Corresponds to §5.1 of okf-docs-workspace-deploy-v1.md.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class WorkspaceManifestEntry:
    """Canonical schema for a single curated documentation entry."""

    schema_version: str = "1.0"
    id: str = ""  # slug from path, e.g., "okf-review-factory-v1"
    title: str = ""  # from H1 or frontmatter
    path: str = ""  # relative to release docs/, e.g., "okf-review-factory-v1.md"
    category: str = "other"  # architecture, decisions, prompts, runbooks, okfs, plugins, governance, research, other
    tags: list[str] = field(default_factory=list)  # from frontmatter or extracted
    last_modified: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )  # ISO8601
    size_bytes: int = 0
    status: str = "accepted"  # proposed, accepted, deprecated, superseded
    superseded_by: Optional[str] = None  # id of replacement doc if status=superseded
    acceptance: dict[str, Any] = field(default_factory=dict)  # from AcceptanceProtocol
    linear_issue: Optional[str] = None  # e.g., "GRO-4188"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspaceManifestEntry:
        """Deserialize from dictionary."""
        return cls(
            schema_version=data.get("schema_version", "1.0"),
            id=data.get("id", ""),
            title=data.get("title", ""),
            path=data.get("path", ""),
            category=data.get("category", "other"),
            tags=data.get("tags", []),
            last_modified=data.get(
                "last_modified", datetime.now(timezone.utc).isoformat()
            ),
            size_bytes=data.get("size_bytes", 0),
            status=data.get("status", "accepted"),
            superseded_by=data.get("superseded_by"),
            acceptance=data.get("acceptance", {}),
            linear_issue=data.get("linear_issue"),
        )
