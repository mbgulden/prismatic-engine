"""types — shared dataclasses for the provision_site package.

Lives in its own module to avoid the circular import between
`orchestrator.py` (which imports `steps`) and `steps/__init__.py`
(which needs `StepResult`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class StepResult:
    """The result of one provisioning step."""
    name: str
    status: str  # "complete" | "failed" | "skipped" | "pending"
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    output: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProvisionRun:
    """A full provisioning run for one domain."""
    domain: str
    owner: str
    started_at: str
    finished_at: str | None = None
    overall_status: str = "in_progress"  # "in_progress" | "complete" | "failed"
    steps: list[StepResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "owner": self.owner,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "overall_status": self.overall_status,
            "steps": [asdict(s) for s in self.steps],
        }
