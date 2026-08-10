"""AGY completed-work integration gate.

This module is intentionally pure: it classifies a completed AGY handoff packet
before any branch is merged or rebuilt. Gateway/dashboard callers can use the
same contract without dispatching AGY or touching git remotes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence


class GateClassification(str, Enum):
    """Stable machine-readable classifications for completed work."""

    MERGE_READY = "merge_ready"
    CLEAN_REBUILD_REQUIRED = "clean_rebuild_required"
    BLOCKED_MISSING_PROOF = "blocked_missing_proof"
    BLOCKED_FAILED_VERIFICATION = "blocked_failed_verification"
    MANUAL_REVIEW_SCOPE = "manual_review_scope"
    MANUAL_REVIEW_CONFLICT = "manual_review_conflict"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


REQUIRED_PACKET_FIELDS = {
    "agent",
    "source_branch",
    "source_path",
    "base_branch",
    "changed_files",
    "result_summary",
    "proof",
    "lane_scope",
}
REQUIRED_PROOF_FIELDS = {
    "command",
    "result",
    "log",
    "scope",
    "ad_hoc_or_canonical",
    "marker",
}
REQUIRED_LANE_FIELDS = {"allowed_paths", "touched_paths"}
ALLOWED_PROOF_RESULTS = {"PASS", "FAIL", "BLOCKED"}
ALLOWED_BASE_BRANCHES = {"origin/main", "main"}
AGY_COMPLETED_WORK_MARKER = "AGY_COMPLETED_WORK_INTEGRATION_GATE_OK"


@dataclass(frozen=True)
class CompletedWorkGateInput:
    """Normalized input for classifying a completed work packet."""

    packet: Mapping[str, Any]
    dirty_source: bool = False
    source_is_stale: bool = False
    conflicts: Sequence[str] = field(default_factory=tuple)
    trusted_agents: Sequence[str] = ("agy",)


@dataclass(frozen=True)
class CompletedWorkGateState:
    """Stable output consumed by API/dashboard/Linear writeback candidates."""

    classification: GateClassification
    eligible_for_merge: bool
    requires_clean_rebuild: bool
    reasons: tuple[str, ...]
    agent: str | None
    source_branch: str | None
    source_path: str | None
    base_branch: str | None
    changed_files: tuple[str, ...]
    proof_result: str | None
    proof_marker: str | None
    dashboard_label: str
    linear_status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification.value,
            "eligible_for_merge": self.eligible_for_merge,
            "requires_clean_rebuild": self.requires_clean_rebuild,
            "reasons": list(self.reasons),
            "agent": self.agent,
            "source_branch": self.source_branch,
            "source_path": self.source_path,
            "base_branch": self.base_branch,
            "changed_files": list(self.changed_files),
            "proof_result": self.proof_result,
            "proof_marker": self.proof_marker,
            "dashboard_label": self.dashboard_label,
            "linear_status": self.linear_status,
        }


def completed_work_gate_schema() -> dict[str, Any]:
    """Return a small JSON-schema-like contract for callers and dashboard docs."""

    return {
        "marker": AGY_COMPLETED_WORK_MARKER,
        "required_packet_fields": sorted(REQUIRED_PACKET_FIELDS),
        "required_proof_fields": sorted(REQUIRED_PROOF_FIELDS),
        "required_lane_scope_fields": sorted(REQUIRED_LANE_FIELDS),
        "classifications": [item.value for item in GateClassification],
        "minimum_packet": {
            "agent": "agy",
            "source_branch": "feature/...",
            "source_path": str(Path.home() / "..."),
            "base_branch": "origin/main",
            "changed_files": [],
            "result_summary": "...",
            "proof": {
                "command": "...",
                "result": "PASS|FAIL|BLOCKED",
                "log": "/tmp/...",
                "scope": "...",
                "ad_hoc_or_canonical": "ad-hoc targeted|canonical suite",
                "non_claims": ["production_deployed", "auto_merge"],
                "marker": "...",
            },
            "lane_scope": {"allowed_paths": [], "touched_paths": []},
        },
        "non_claims": ["no_auto_merge", "no_bulk_agy_dispatch", "contract_gate_only"],
    }


def classify_completed_work(
    packet: Mapping[str, Any],
    *,
    dirty_source: bool = False,
    source_is_stale: bool = False,
    conflicts: Sequence[str] | None = None,
    trusted_agents: Sequence[str] = ("agy",),
) -> CompletedWorkGateState:
    """Classify a completed AGY result packet before any merge is considered."""

    gate_input = CompletedWorkGateInput(
        packet=packet,
        dirty_source=dirty_source,
        source_is_stale=source_is_stale,
        conflicts=tuple(conflicts or ()),
        trusted_agents=trusted_agents,
    )
    return _classify(gate_input)


def demo_completed_work_packet() -> dict[str, Any]:
    """Return a safe fixture packet for the demo classification endpoint."""

    return {
        "agent": "agy",
        "source_branch": "feature/agy-demo-completed-work",
        "source_path": str(Path.home() / "work" / "agy-demo-completed-work"),
        "base_branch": "origin/main",
        "changed_files": ["prismatic/demo.py", "tests/test_demo.py"],
        "result_summary": "Demo completed-work packet for gate contract proof.",
        "proof": {
            "command": "python3 -m pytest -q tests/test_demo.py",
            "result": "PASS",
            "log": "/tmp/agy-demo-proof.log",
            "scope": "demo fixture only",
            "ad_hoc_or_canonical": "ad-hoc targeted",
            "non_claims": ["production_deployed", "auto_merge"],
            "marker": AGY_COMPLETED_WORK_MARKER,
        },
        "lane_scope": {
            "allowed_paths": ["prismatic/", "tests/", "docs/", "schemas/"],
            "touched_paths": ["prismatic/demo.py", "tests/test_demo.py"],
        },
    }


def demo_completed_work_gate_state() -> dict[str, Any]:
    """Return demo endpoint payload without reading external AGY state."""

    state = classify_completed_work(demo_completed_work_packet())
    return {"status": "ok", "mode": "fixture", "gate": state.as_dict()}


def _classify(gate_input: CompletedWorkGateInput) -> CompletedWorkGateState:
    packet = gate_input.packet
    reasons: list[str] = []

    missing = sorted(REQUIRED_PACKET_FIELDS - set(packet.keys()))
    if missing:
        reasons.append("missing packet fields: " + ", ".join(missing))
        if "proof" in missing:
            return _state(GateClassification.BLOCKED_MISSING_PROOF, packet, reasons)
        if "lane_scope" in missing:
            return _state(GateClassification.MANUAL_REVIEW_SCOPE, packet, reasons)
        return _state(GateClassification.REJECTED, packet, reasons)

    agent = _string(packet.get("agent"))
    if agent not in set(gate_input.trusted_agents):
        reasons.append(f"untrusted agent: {agent or 'missing'}")
        return _state(GateClassification.REJECTED, packet, reasons)

    source_branch = _string(packet.get("source_branch"))
    if not source_branch or not source_branch.startswith("feature/"):
        reasons.append("source_branch must be a feature/* branch")
        return _state(GateClassification.MANUAL_REVIEW_SCOPE, packet, reasons)

    source_path = _string(packet.get("source_path"))
    try:
        is_under_home = source_path and Path(source_path).resolve().is_relative_to(Path.home().resolve())
    except Exception:
        is_under_home = False
    if not source_path or not is_under_home:
        reasons.append(
            "source_path must be an absolute path under the operator home directory"
        )
        return _state(GateClassification.MANUAL_REVIEW_SCOPE, packet, reasons)

    base_branch = _string(packet.get("base_branch"))
    if base_branch not in ALLOWED_BASE_BRANCHES:
        reasons.append(f"unsupported base_branch: {base_branch or 'missing'}")
        return _state(GateClassification.SUPERSEDED, packet, reasons)

    changed_files = _string_list(packet.get("changed_files"))
    if not changed_files:
        reasons.append("changed_files must not be empty")
        return _state(GateClassification.REJECTED, packet, reasons)

    lane_scope = packet.get("lane_scope")
    if not isinstance(lane_scope, Mapping):
        reasons.append("lane_scope must be an object")
        return _state(GateClassification.MANUAL_REVIEW_SCOPE, packet, reasons)
    missing_lane = sorted(REQUIRED_LANE_FIELDS - set(lane_scope.keys()))
    if missing_lane:
        reasons.append("missing lane_scope fields: " + ", ".join(missing_lane))
        return _state(GateClassification.MANUAL_REVIEW_SCOPE, packet, reasons)

    allowed_paths = _string_list(lane_scope.get("allowed_paths"))
    touched_paths = _string_list(lane_scope.get("touched_paths"))
    if not touched_paths:
        touched_paths = changed_files
    if packet.get("ACCEPTANCE_DECISION") == "PENDING":
        reasons.append("producer acceptance pending independent review")
        return _state(GateClassification.MANUAL_REVIEW_SCOPE, packet, reasons)
    out_of_scope = _out_of_scope_paths(touched_paths, allowed_paths)
    if out_of_scope:
        reason = "touched paths outside lane scope: " + ", ".join(out_of_scope)
        if lane_scope.get("manual_review_reason"):
            reason += f" ({lane_scope['manual_review_reason']})"
        reasons.append(reason)
        return _state(GateClassification.MANUAL_REVIEW_SCOPE, packet, reasons)

    conflicts = tuple(gate_input.conflicts)
    if conflicts:
        reasons.append("conflicts require manual review: " + ", ".join(conflicts))
        return _state(GateClassification.MANUAL_REVIEW_CONFLICT, packet, reasons)

    if gate_input.source_is_stale:
        reasons.append("source is stale relative to base branch")
        return _state(GateClassification.SUPERSEDED, packet, reasons)

    if gate_input.dirty_source:
        reasons.append("source worktree is dirty or untrusted; clean rebuild required")
        return _state(GateClassification.CLEAN_REBUILD_REQUIRED, packet, reasons)

    proof = packet.get("proof")
    if not isinstance(proof, Mapping):
        reasons.append("proof must be an object")
        return _state(GateClassification.BLOCKED_MISSING_PROOF, packet, reasons)
    missing_proof = sorted(REQUIRED_PROOF_FIELDS - set(proof.keys()))
    if missing_proof:
        reasons.append("missing proof fields: " + ", ".join(missing_proof))
        return _state(GateClassification.BLOCKED_MISSING_PROOF, packet, reasons)

    proof_result = _string(proof.get("result"))
    if proof_result not in ALLOWED_PROOF_RESULTS:
        reasons.append(f"invalid proof result: {proof_result or 'missing'}")
        return _state(GateClassification.BLOCKED_MISSING_PROOF, packet, reasons)
    if proof_result == "BLOCKED":
        reasons.append("proof result is BLOCKED")
        return _state(GateClassification.BLOCKED_MISSING_PROOF, packet, reasons)
    if proof_result == "FAIL":
        reasons.append("proof result is FAIL")
        return _state(GateClassification.BLOCKED_FAILED_VERIFICATION, packet, reasons)

    proof_command = _string(proof.get("command"))
    proof_log = _string(proof.get("log"))
    proof_scope = _string(proof.get("scope"))
    proof_marker = _string(proof.get("marker"))
    proof_non_claims = normalize_non_claims(proof)
    if not proof_command or not proof_log or not proof_scope or not proof_marker:
        reasons.append("proof command/log/scope/marker must be non-empty")
        return _state(GateClassification.BLOCKED_MISSING_PROOF, packet, reasons)
    if not proof_non_claims:
        reasons.append("proof must include non_claims or legacy not_claiming")
        return _state(GateClassification.BLOCKED_MISSING_PROOF, packet, reasons)
    if not proof_log.startswith("/tmp/"):
        reasons.append("proof log must point to /tmp evidence")
        return _state(GateClassification.BLOCKED_MISSING_PROOF, packet, reasons)

    reasons.append(
        "packet passed contract, lane, and proof checks; manual merge still required"
    )
    return _state(GateClassification.MERGE_READY, packet, reasons)


def _state(
    classification: GateClassification,
    packet: Mapping[str, Any],
    reasons: Sequence[str],
) -> CompletedWorkGateState:
    raw_proof = packet.get("proof")
    proof: Mapping[str, Any] = raw_proof if isinstance(raw_proof, Mapping) else {}
    changed_files = tuple(_string_list(packet.get("changed_files")))
    eligible = classification is GateClassification.MERGE_READY

    reasons_list = list(reasons)
    lane_scope = packet.get("lane_scope")
    if isinstance(lane_scope, Mapping) and lane_scope.get("manual_review_reason"):
        m_reason = str(lane_scope.get("manual_review_reason"))
        if not any(m_reason in r for r in reasons_list):
            reasons_list.append(m_reason)

    return CompletedWorkGateState(
        classification=classification,
        eligible_for_merge=eligible,
        requires_clean_rebuild=classification
        is GateClassification.CLEAN_REBUILD_REQUIRED,
        reasons=tuple(reasons_list),
        agent=_string(packet.get("agent")),
        source_branch=_string(packet.get("source_branch")),
        source_path=_string(packet.get("source_path")),
        base_branch=_string(packet.get("base_branch")),
        changed_files=changed_files,
        proof_result=_string(proof.get("result")),
        proof_marker=_string(proof.get("marker")),
        dashboard_label=_dashboard_label(classification),
        linear_status=_linear_status(classification),
    )


def _dashboard_label(classification: GateClassification) -> str:
    labels = {
        GateClassification.MERGE_READY: "Merge-ready candidate · manual review required",
        GateClassification.CLEAN_REBUILD_REQUIRED: "Clean rebuild required",
        GateClassification.BLOCKED_MISSING_PROOF: "Blocked · missing proof",
        GateClassification.BLOCKED_FAILED_VERIFICATION: "Blocked · failed verification",
        GateClassification.MANUAL_REVIEW_SCOPE: "Manual review · scope/lane mismatch",
        GateClassification.MANUAL_REVIEW_CONFLICT: "Manual review · conflict",
        GateClassification.SUPERSEDED: "Superseded by current base",
        GateClassification.REJECTED: "Rejected by contract",
    }
    return labels[classification]


def _linear_status(classification: GateClassification) -> str:
    statuses = {
        GateClassification.MERGE_READY: "Awaiting Fred merge review",
        GateClassification.CLEAN_REBUILD_REQUIRED: "Clean rebuild required before review",
        GateClassification.BLOCKED_MISSING_PROOF: "Blocked: proof packet missing/incomplete",
        GateClassification.BLOCKED_FAILED_VERIFICATION: "Blocked: verification failed",
        GateClassification.MANUAL_REVIEW_SCOPE: "Manual review: lane/scope mismatch",
        GateClassification.MANUAL_REVIEW_CONFLICT: "Manual review: conflict",
        GateClassification.SUPERSEDED: "Superseded: source stale",
        GateClassification.REJECTED: "Rejected: invalid handoff contract",
    }
    return statuses[classification]


def normalize_non_claims(proof: Mapping[str, Any]) -> tuple[str, ...]:
    """Normalize proof non-claims without treating them as positive claims.

    Older packets used `not_claiming: "a,b"`; newer packets should use
    `non_claims: ["a", "b"]`. Returning a separate tuple prevents validators
    from scanning a negated claim string and falsely flagging words like
    `auto_merge` as an asserted capability.
    """

    raw = proof.get("non_claims")
    values: list[str] = []
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        values.extend(
            item.strip() for item in raw if isinstance(item, str) and item.strip()
        )
    legacy = proof.get("not_claiming")
    if isinstance(legacy, str):
        values.extend(
            part.strip() for part in legacy.replace(";", ",").split(",") if part.strip()
        )
    elif isinstance(legacy, Sequence) and not isinstance(legacy, (str, bytes)):
        values.extend(
            item.strip() for item in legacy if isinstance(item, str) and item.strip()
        )
    # Stable de-dupe preserving order.
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(value)
    return tuple(deduped)


def _out_of_scope_paths(
    touched_paths: Sequence[str], allowed_paths: Sequence[str]
) -> list[str]:
    if not allowed_paths:
        return list(touched_paths)
    clean_allowed = tuple(
        path.rstrip("/") + "/" if not path.endswith("/") else path
        for path in allowed_paths
    )
    out: list[str] = []
    for touched in touched_paths:
        normalized = touched.lstrip("/")
        if ".." in Path(normalized).parts:
            out.append(touched)
            continue
        if not any(
            normalized.startswith(prefix) or normalized == prefix.rstrip("/")
            for prefix in clean_allowed
        ):
            out.append(touched)
    return out


def _string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
    return result
