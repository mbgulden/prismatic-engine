"""types — shared dataclasses for the provision_site package.

Lives in its own module to avoid the circular import between
`orchestrator.py` (which imports `steps`) and `steps/__init__.py`
(which needs `StepResult`).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class StepResult:
    """The result of one provisioning step."""
    name: str
    status: str  # "complete" | "failed" | "skipped" | "pending"
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None
    output: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProvisionRun:
    """A full provisioning run for one domain."""
    domain: str
    owner: str
    started_at: str
    finished_at: Optional[str] = None
    overall_status: str = "in_progress"  # "in_progress" | "complete" | "failed"
    steps: List[StepResult] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "owner": self.owner,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "overall_status": self.overall_status,
            "steps": [asdict(s) for s in self.steps],
        }
