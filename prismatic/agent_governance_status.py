"""Agent governance status read model for dashboard operator visibility.

This module turns existing local evidence into a no-side-effect governance status
surface. It intentionally labels local filesystem/packet sources as interim so
operators do not mistake dry-run or pending artifacts for deployed truth.
"""

from __future__ import annotations

import re
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

MARKER = "AGENT_GOVERNANCE_CORE_STATE_MODEL_OK"
UI_API_CONTRACT_MARKER = "GOVERNANCE_DASHBOARD_UI_API_CONTRACT_OK"
LEGACY_DASHBOARD_MARKER = "AGENT_GOVERNANCE_DASHBOARD_STATUS_OK"
SOURCE = (
    "interim-source: run_records+agent_registry+completed_work_packets+local_policy"
)
CONTRACT_VERSION = "governance-dashboard-ui-api-contract-v1"
DEFAULT_AGENTS = ("kai", "fred")
DEFAULT_SIDE_EFFECT_POLICY = {
    "merge": False,
    "deploy": False,
    "linear_writeback": False,
    "github_pr_create": False,
    "auto_merge": False,
    "bulk_dispatch": False,
    "production_restart": False,
}
_SAFE_ISSUE_ID_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_SENSITIVE_LOCATOR_RE = re.compile(
    r"(?is)("
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\bauthorization\s*[:=]"
    r"|\bbearer\s+[a-z0-9._~+/=-]{6,}"
    r"|\b(?:password|passwd|pwd|token|secret|api[_-]?key|access[_-]?key|client[_-]?secret)\b\s*[:=]"
    r"|(?:^|[/_.?&#=-])(?:password|passwd|pwd|token|secret|api[_-]?key|apikey|access[_-]?key|client[_-]?secret)(?:$|[/_.?&#=-])"
    r"|\b(?:ghp|gho|ghu|ghs|github_pat)_[a-z0-9_]{12,}"
    r"|\b(?:sk-ant|sk-proj|sk-live|rk_live|pk_live|AIza)[a-z0-9._~+/=-]{8,}"
    r"|\bsk-[a-z0-9_-]{12,}"
    r"|\bxox(?:b|p|a|r)-[a-z0-9-]{12,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r")"
)
_SENSITIVE_DISPLAY_RE = re.compile(
    r"(?is)("
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\bauthorization\s*[:=]"
    r"|\bbearer\s+[a-z0-9._~+/=-]{6,}"
    r"|\b(?:password|passwd|pwd|token|secret|api[_-]?key|access[_-]?key|client[_-]?secret)\b\s*[:=]"
    r"|\b(?:ghp|gho|ghu|ghs|github_pat)_[a-z0-9_]{12,}"
    r"|\b(?:sk-ant|sk-proj|sk-live|rk_live|pk_live|AIza)[a-z0-9._~+/=-]{8,}"
    r"|\bsk-[a-z0-9_-]{12,}"
    r"|\bxox(?:b|p|a|r)-[a-z0-9-]{12,}"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r")"
)
DISPLAY_REDACTION_PLACEHOLDER = "[redacted unsafe display text]"
_SAFE_DISPLAY_TOKEN_RE = re.compile(r"^[A-Za-z0-9 .:_/+@#=-]{1,160}$")
_SAFE_STATUS_TOKEN_RE = re.compile(r"^[a-z0-9_+.-]{1,80}$", re.IGNORECASE)


def _to_dict(value: Any, fields: Sequence[str]) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "as_dict"):
        try:
            return dict(value.as_dict())
        except Exception:
            pass
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)  # type: ignore[arg-type]
    return {name: getattr(value, name) for name in fields if hasattr(value, name)}


def _row_to_dict(row: Any) -> dict[str, Any]:
    return _to_dict(
        row,
        (
            "id",
            "created_at",
            "updated_at",
            "agent",
            "source_branch",
            "source_path",
            "classification",
            "integration_classification",
            "proof_result",
            "proof_marker",
            "proof_log",
            "packet",
            "gate",
            "non_claims",
        ),
    )


def _record_to_dict(record: Any) -> dict[str, Any]:
    return _to_dict(
        record,
        (
            "run_id",
            "issue_id",
            "agent_name",
            "status",
            "started_at",
            "completed_at",
            "output_path",
            "error_message",
            "verification_status",
            "verification_scope",
        ),
    )


def _agent_key(value: Any) -> str:
    key = str(value or "").strip().lower().replace("agent:", "")
    aliases = {
        "kai-js": "kai",
        "kai-css": "kai",
        "kai-content": "kai",
        "fredthebotfredthebot": "fred",
    }
    return aliases.get(key, key)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _latest_stamp(item: Mapping[str, Any]) -> datetime | None:
    return (
        _parse_dt(item.get("updated_at"))
        or _parse_dt(item.get("completed_at"))
        or _parse_dt(item.get("started_at"))
        or _parse_dt(item.get("created_at"))
    )


def _issue_url(issue_id: Any) -> str | None:
    text = str(issue_id or "").strip().upper()
    if not text or text == "UNKNOWN" or not _SAFE_ISSUE_ID_RE.fullmatch(text):
        return None
    return f"https://prismatic.growthwebdev.com/tab/tasks?issue={text}"


def _has_unsafe_locator_material(text: str) -> bool:
    return bool(_CONTROL_CHARS_RE.search(text) or _SENSITIVE_LOCATOR_RE.search(text))


def _has_unsafe_display_material(text: str) -> bool:
    return bool(_CONTROL_CHARS_RE.search(text) or _SENSITIVE_DISPLAY_RE.search(text))


def _sanitize_display_text(
    value: Any, *, placeholder: str = DISPLAY_REDACTION_PLACEHOLDER
) -> str:
    """Fail-closed sanitizer for externally serialized untrusted display text.

    Registry, run-record, completed-work, and packet strings can be controlled by
    agents or upstream integrations. If the text carries secret-shaped material,
    control characters, or an unexpected display shape, return one fixed
    non-sensitive placeholder and never echo the original value.
    """
    text = str(value or "").strip()
    if not text:
        return placeholder
    if _has_unsafe_display_material(text):
        return placeholder
    if not _SAFE_DISPLAY_TOKEN_RE.fullmatch(text):
        return placeholder
    return text


def _sanitize_status_token(value: Any, *, fallback: str = "unknown") -> str:
    text = str(value or "").strip().lower()
    if not text or _has_unsafe_display_material(text):
        return fallback
    if not _SAFE_STATUS_TOKEN_RE.fullmatch(text):
        return fallback
    return text


def _is_safe_local_absolute_path(text: str) -> bool:
    if not text.startswith("/") or text.startswith("//"):
        return False
    parts = [part for part in text.split("/") if part]
    return ".." not in parts


def _sanitize_proof_locator(value: Any, *, generated: bool = False) -> str | None:
    """Return a safe proof href or drop it fail-closed.

    Rejected values are intentionally not surfaced in responses or exceptions.
    """
    text = str(value or "").strip()
    if not text or _has_unsafe_locator_material(text):
        return None

    parsed = urlsplit(text)
    if parsed.scheme:
        if parsed.scheme not in {"http", "https"}:
            return None
        if parsed.username or parsed.password:
            return None
        if _has_unsafe_locator_material(parsed.netloc) or _has_unsafe_locator_material(
            parsed.path
        ):
            return None
        if not parsed.netloc:
            return None
        query = parsed.query if generated else ""
        fragment = parsed.fragment if generated else ""
        if (query and _has_unsafe_locator_material(query)) or (
            fragment and _has_unsafe_locator_material(fragment)
        ):
            return None
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path or "/", query, fragment)
        )

    if _is_safe_local_absolute_path(text):
        return text
    return None


def _packet_field(packet: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if packet.get(name) not in (None, ""):
            return packet.get(name)
        upper = name.upper()
        if packet.get(upper) not in (None, ""):
            return packet.get(upper)
    raw = packet.get("_raw_fields")
    if isinstance(raw, Mapping):
        for name in names:
            upper = name.upper()
            if raw.get(upper) not in (None, ""):
                return raw.get(upper)
    proof = packet.get("proof")
    if isinstance(proof, Mapping):
        for name in names:
            if proof.get(name) not in (None, ""):
                return proof.get(name)
            upper = name.upper()
            if proof.get(upper) not in (None, ""):
                return proof.get(upper)
    return None


def _proof_links(
    row: Mapping[str, Any], record: Mapping[str, Any] | None
) -> list[dict[str, str]]:
    packet_value = row.get("packet")
    packet: Mapping[str, Any] = (
        packet_value if isinstance(packet_value, Mapping) else {}
    )
    proof_value = packet.get("proof")
    proof: Mapping[str, Any] = proof_value if isinstance(proof_value, Mapping) else {}
    candidates = [
        (
            "proof_log",
            row.get("proof_log") or proof.get("log") or _packet_field(packet, "LOG"),
        ),
        ("source_path", row.get("source_path") or packet.get("source_path")),
        ("output_path", (record or {}).get("output_path")),
        (
            "issue",
            _issue_url(
                packet.get("issue_identifier")
                or packet.get("issue_id")
                or (record or {}).get("issue_id")
            ),
        ),
    ]
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for label, value in candidates:
        if not value:
            continue
        href = _sanitize_proof_locator(value, generated=(label == "issue"))
        if not href or href in seen:
            continue
        seen.add(href)
        links.append({"label": label, "href": href, "source_label": SOURCE})
    return links


def _load_completed_work(limit: int = 100) -> list[dict[str, Any]]:
    try:
        from prismatic.agy_completed_work import list_completed_work

        return [_row_to_dict(row) for row in list_completed_work(limit=limit)]
    except Exception:
        return []


def _policy_from_context(policy: Mapping[str, Any] | None) -> dict[str, bool]:
    merged = dict(DEFAULT_SIDE_EFFECT_POLICY)
    if isinstance(policy, Mapping):
        for key in merged:
            if key in policy:
                merged[key] = bool(policy[key])
    return merged


def _audit_result(
    row: Mapping[str, Any] | None, record: Mapping[str, Any] | None
) -> str:
    if row:
        return _sanitize_display_text(
            row.get("integration_classification")
            or row.get("classification")
            or row.get("proof_result")
            or "packet_seen"
        )
    if record:
        status = _sanitize_status_token(record.get("status"))
        verification = record.get("verification_status")
        if verification:
            safe_verification = _sanitize_status_token(verification)
            return f"run_{status}+verification_{safe_verification}"
        return f"run_{status}"
    return "no_packet_seen"


def _audit_events(
    row: Mapping[str, Any] | None, record: Mapping[str, Any] | None
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if record:
        status = _sanitize_status_token(record.get("status"))
        events.append(
            {
                "event_type": f"agent_run_{status}",
                "source_label": "source:agent_run_record_store",
                "timestamp": record.get("completed_at") or record.get("started_at"),
                "summary": f"Agent run record is {status}",
            }
        )
    if row:
        events.append(
            {
                "event_type": "completed_work_packet_seen",
                "source_label": "source:completed_work_store",
                "timestamp": row.get("updated_at") or row.get("created_at"),
                "summary": _sanitize_display_text(
                    row.get("integration_classification")
                    or row.get("classification")
                    or "packet_seen"
                ),
            }
        )
    if not events:
        events.append(
            {
                "event_type": "no_completed_packet_seen",
                "source_label": SOURCE,
                "timestamp": None,
                "summary": "No completed-work packet or run record found for this agent.",
            }
        )
    return events


def _approval_gates(policy: Mapping[str, bool]) -> list[dict[str, Any]]:
    return [
        {
            "gate": "operator_approval_required_before_real_side_effects",
            "source_label": "source:local_side_effect_policy",
            "state": "blocked_by_default" if not allowed else "requires_policy_review",
            "side_effect": name,
        }
        for name, allowed in policy.items()
    ]


def _durability_status(
    row: Mapping[str, Any] | None, record: Mapping[str, Any] | None
) -> dict[str, Any]:
    return {
        "state": "durable_packet_seen"
        if row
        else "run_record_only"
        if record
        else "no_durable_packet_seen",
        "source_label": "source:completed_work_store"
        if row
        else "source:agent_run_record_store"
        if record
        else SOURCE,
        "proof_persisted": bool(row),
        "run_record_persisted": bool(record),
    }


def _portability_readiness(row: Mapping[str, Any] | None) -> dict[str, Any]:
    source_path = str((row or {}).get("source_path") or "")
    host_prefix = f"{Path.home()}/"
    has_host_path = source_path.startswith(host_prefix)
    return {
        "state": "review_host_path" if has_host_path else "portable_or_not_applicable",
        "source_label": SOURCE,
        "host_path_seen": has_host_path,
        "notes": "Host-local proof paths are links only; dashboard labels them as local/interim evidence.",
    }


def build_agent_governance_status(
    *,
    agents: Sequence[str] = DEFAULT_AGENTS,
    run_records: Iterable[Any] | None = None,
    registry: Mapping[str, Any] | None = None,
    completed_work_rows: Iterable[Any] | None = None,
    side_effect_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build dashboard-visible governance status for target agents only.

    The result is read-only: it performs no merge, deploy, writeback, PR creation,
    dispatch, restart, or approval mutation.
    """

    policy = _policy_from_context(side_effect_policy)
    records = [_record_to_dict(record) for record in (run_records or [])]
    rows = [
        _row_to_dict(row)
        for row in (
            completed_work_rows
            if completed_work_rows is not None
            else _load_completed_work()
        )
    ]
    records.sort(
        key=lambda item: (
            _latest_stamp(item) or datetime.min.replace(tzinfo=timezone.utc)
        ),
        reverse=True,
    )
    rows.sort(
        key=lambda item: (
            _latest_stamp(item) or datetime.min.replace(tzinfo=timezone.utc)
        ),
        reverse=True,
    )
    registry = registry if isinstance(registry, Mapping) else {}

    statuses: list[dict[str, Any]] = []
    for raw_agent in agents:
        agent = _sanitize_status_token(_agent_key(raw_agent), fallback="unknown_agent")
        reg = next(
            (
                dict(value)
                for key, value in registry.items()
                if _agent_key(key) == agent and isinstance(value, Mapping)
            ),
            {},
        )
        matching_records = [
            record
            for record in records
            if _agent_key(record.get("agent_name")) == agent
        ]
        matching_rows = []
        for row in rows:
            packet_value = row.get("packet")
            packet = packet_value if isinstance(packet_value, Mapping) else {}
            if _agent_key(row.get("agent") or packet.get("agent")) == agent:
                matching_rows.append(row)
        latest_record = matching_records[0] if matching_records else None
        latest_row = matching_rows[0] if matching_rows else None
        packet_value = latest_row.get("packet") if latest_row else None
        packet: Mapping[str, Any] = (
            packet_value if isinstance(packet_value, Mapping) else {}
        )
        current_task = (
            reg.get("task_id")
            or reg.get("issue")
            or (latest_record or {}).get("issue_id")
            or _packet_field(packet, "issue_identifier", "issue_id")
        )
        last_task = _packet_field(packet, "issue_identifier", "issue_id") or (
            latest_record or {}
        ).get("issue_id")
        proof_result = (latest_row or {}).get("proof_result") or _packet_field(
            packet, "RESULT"
        )
        proof_marker = (
            (latest_row or {}).get("proof_marker")
            or _packet_field(packet, "MARKER")
            or _packet_field(packet, "marker")
        )
        safe_current_task = _sanitize_display_text(
            current_task or "No current task recorded"
        )
        safe_last_task = _sanitize_display_text(last_task or "No last task recorded")
        safe_registry_status = _sanitize_display_text(
            reg.get("status") or "not_registered"
        )
        safe_proof_result = _sanitize_display_text(proof_result or "not_reported")
        safe_proof_marker = _sanitize_display_text(proof_marker or "not_reported")
        statuses.append(
            {
                "agent": agent,
                "name": _sanitize_display_text(reg.get("name") or agent.title()),
                "lane_status": "policy_guarded"
                if not any(policy.values())
                else "policy_review_required",
                "current_task": safe_current_task,
                "last_task": safe_last_task,
                "task_detail": {
                    "current_task": safe_current_task,
                    "last_task": safe_last_task,
                    "registry_status": safe_registry_status,
                    "source_label": "source:agent_registry+run_records+completed_work_packets",
                },
                "audit_result": _audit_result(latest_row, latest_record),
                "audit_events": _audit_events(latest_row, latest_record),
                "proof_result": safe_proof_result,
                "proof_marker": safe_proof_marker,
                "proof_links": _proof_links(latest_row or {}, latest_record),
                "approval_gates": _approval_gates(policy),
                "side_effect_policy": policy,
                "side_effect_policy_label": "all real side effects disabled unless operator-approved",
                "durability_status": _durability_status(latest_row, latest_record),
                "portability_readiness": _portability_readiness(latest_row),
                "readiness_fields": {
                    "packet_seen": bool(latest_row),
                    "run_seen": bool(latest_record),
                    "approval_required_for_real_effects": True,
                    "source_label": SOURCE,
                },
                "source_labels": [
                    SOURCE,
                    "source:local_side_effect_policy",
                    "source:dashboard_read_model_contract",
                ],
                "interim_source_label": SOURCE,
                "last_activity_at": (
                    _latest_stamp(latest_row or {})
                    or _latest_stamp(latest_record or {})
                    or datetime.now(timezone.utc)
                ).isoformat(),
            }
        )

    return {
        "marker": MARKER,
        "ui_api_contract_marker": UI_API_CONTRACT_MARKER,
        "legacy_dashboard_marker": LEGACY_DASHBOARD_MARKER,
        "contract_version": CONTRACT_VERSION,
        "source": SOURCE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "agents": statuses,
        "ui_api_contract": {
            "route": "/api/gateway/agents/governance-status",
            "dashboard_panel_marker": "agent-governance-dashboard-status",
            "fields": [
                "lane_status",
                "task_detail",
                "proof_links",
                "audit_events",
                "approval_gates",
                "side_effect_policy",
                "durability_status",
                "portability_readiness",
                "readiness_fields",
                "source_labels",
            ],
            "source_label_required": True,
            "mock_data_allowed": False,
            "secret_values_allowed": False,
        },
        "approval_gates": _approval_gates(policy),
        "side_effect_policy": policy,
        "durability_status": {
            "state": "read_model_available",
            "source_label": SOURCE,
            "agents_with_durable_packets": sum(
                1 for item in statuses if item["durability_status"]["proof_persisted"]
            ),
        },
        "portability_readiness": {
            "state": "local_paths_labelled"
            if any(item["portability_readiness"]["host_path_seen"] for item in statuses)
            else "portable_or_not_applicable",
            "source_label": SOURCE,
            "host_local_links_are_evidence_only": True,
        },
        "no_mock_assertions": [
            "No synthetic fallback rows are generated by the read model.",
            "Missing rows are represented as no_packet_seen/no_durable_packet_seen with source labels.",
        ],
        "no_secret_assertions": [
            "Proof links expose paths/URLs only; no credential values are produced by this contract.",
            "Side-effect policy is boolean and contains no token-like fields.",
        ],
        "non_claims": [
            "no merge",
            "no deploy",
            "no Linear writeback",
            "no GitHub PR creation",
            "no auto-merge",
            "no bulk dispatch",
            "no production restart",
        ],
    }
