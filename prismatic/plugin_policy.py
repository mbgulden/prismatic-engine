from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prismatic.plugin_architecture import plugin_catalog

POLICY_DECISIONS = {"allow", "needs_approval", "block"}
RISKY_ACTION_TOKENS = {
    "publish",
    "export",
    "deploy",
    "delete",
    "destroy",
    "write",
    "overwrite",
    "batch",
    "costly",
    "external-service",
    "credentialed",
    "production",
    "public",
}
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled", "rejected"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def looks_secret(value: Any) -> bool:
    if isinstance(value, dict):
        return any(looks_secret(v) for v in value.values())
    if isinstance(value, list):
        return any(looks_secret(v) for v in value)
    if not isinstance(value, str):
        return False
    upper = value.upper()
    if upper.endswith("_ENV") or (
        upper.isidentifier()
        and any(token in upper for token in ["API_KEY", "TOKEN", "SECRET", "PASSWORD"])
    ):
        return False
    return any(
        token in value
        for token in ["sk-", "ghp_", "xoxb-", "AIza", "-----BEGIN", "Bearer "]
    )


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, val in value.items():
            key_lower = str(key).lower()
            if any(
                token in key_lower
                for token in [
                    "secret",
                    "token",
                    "password",
                    "api_key",
                    "apikey",
                    "authorization",
                ]
            ):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_secrets(val)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(v) for v in value]
    if looks_secret(value):
        return "[REDACTED]"
    return value


def decision_payload(
    *,
    decision: str,
    reason: str,
    blockers: list[str] | None = None,
    warnings: list[str] | None = None,
    approval_reasons: list[str] | None = None,
    checks: list[dict[str, Any]] | None = None,
    risk_level: str = "unknown",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if decision not in POLICY_DECISIONS:
        decision = "block"
    approval_reasons = sorted(set(approval_reasons or []))
    blockers = blockers or []
    warnings = warnings or []
    return {
        "allowed": decision == "allow",
        "requires_approval": bool(approval_reasons),
        "decision": decision,
        "reason": reason,
        "risk_level": risk_level,
        "blockers": blockers,
        "warnings": warnings,
        "approval_reasons": approval_reasons,
        "checks": checks or [],
        "context": redact_secrets(context or {}),
        "evaluated_at": now_iso(),
    }


def _check(
    name: str, status: str, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {"name": name, "status": status, "details": redact_secrets(details or {})}


def _catalog_item(plugin_name: str) -> dict[str, Any] | None:
    catalog = plugin_catalog(repo_root() / "plugins")
    return next(
        (
            item
            for item in catalog.get("plugins", [])
            if item.get("name") == plugin_name
        ),
        None,
    )


def _plugin_context(
    plugin_name: str,
) -> tuple[
    dict[str, Any] | None,
    dict[str, Any],
    str,
    list[str],
    list[str],
    list[dict[str, Any]],
]:
    item = _catalog_item(plugin_name)
    blockers: list[str] = []
    warnings: list[str] = []
    checks: list[dict[str, Any]] = []
    if item is None:
        blockers.append(f"unknown plugin: {plugin_name}")
        checks.append(_check("plugin_known", "failed", {"plugin_name": plugin_name}))
        return None, {}, "unknown", blockers, warnings, checks

    governance = item.get("governance", {}) or {}
    risk_level = str(governance.get("risk_level") or "unknown")
    checks.append(_check("plugin_known", "passed", {"plugin_name": plugin_name}))
    readiness = governance.get("readiness_state") or "unknown"
    if readiness == "blocked":
        blockers.append("plugin governance readiness is blocked")
        checks.append(
            _check("plugin_readiness", "failed", {"readiness_state": readiness})
        )
    else:
        checks.append(
            _check(
                "plugin_readiness",
                "passed" if readiness in {"ready", "warning"} else "warning",
                {"readiness_state": readiness},
            )
        )

    for blocker in governance.get("production_blockers", []) or []:
        message = str(blocker.get("message", "plugin governance blocker"))
        if blocker.get("severity") == "blocking":
            blockers.append(message)
        else:
            warnings.append(message)

    if governance.get("credential_redaction") == "blocked":
        blockers.append("plugin manifest contains raw secret material")
        checks.append(
            _check(
                "credential_redaction", "failed", {"credential_redaction": "blocked"}
            )
        )
    else:
        checks.append(
            _check(
                "credential_redaction",
                "passed",
                {"credential_redaction": governance.get("credential_redaction")},
            )
        )
    return item, governance, risk_level, blockers, warnings, checks


def _approval_reasons_for_action(
    action: str,
    governance: dict[str, Any],
    risk_level: str,
    requested_approval_required: bool | None = None,
) -> list[str]:
    reasons: list[str] = []
    for gate in governance.get("approval_gates", []) or []:
        reasons.append(str(gate))
    action_lower = action.lower()
    if any(token in action_lower for token in RISKY_ACTION_TOKENS):
        reasons.append(f"action '{action}' requires operator approval")
    if risk_level in {"high", "critical"}:
        reasons.append(f"plugin risk level is {risk_level}")
    if requested_approval_required is True:
        reasons.append("request explicitly required approval")
    return reasons


def evaluate_job_request_policy(
    plugin_name: str,
    action: str,
    *,
    requested_approval_required: bool | None = None,
    input_summary: Any | None = None,
) -> dict[str, Any]:
    item, governance, risk_level, blockers, warnings, checks = _plugin_context(
        plugin_name
    )
    approval_reasons = _approval_reasons_for_action(
        action, governance, risk_level, requested_approval_required
    )
    if looks_secret(input_summary):
        blockers.append("job input appears to contain raw secret material")
        checks.append(_check("raw_secret_input", "failed"))
    else:
        checks.append(_check("raw_secret_input", "passed"))
    checks.append(
        _check(
            "approval_gate",
            "warning" if approval_reasons else "passed",
            {"approval_required": bool(approval_reasons)},
        )
    )
    if blockers:
        return decision_payload(
            decision="block",
            reason="job request blocked by plugin policy",
            blockers=blockers,
            warnings=warnings,
            approval_reasons=approval_reasons,
            checks=checks,
            risk_level=risk_level,
            context={
                "plugin_name": plugin_name,
                "action": action,
                "input_summary": input_summary,
            },
        )
    if approval_reasons:
        return decision_payload(
            decision="needs_approval",
            reason="job request requires operator approval",
            warnings=warnings,
            approval_reasons=approval_reasons,
            checks=checks,
            risk_level=risk_level,
            context={"plugin_name": plugin_name, "action": action},
        )
    return decision_payload(
        decision="allow",
        reason="safe low-risk queued job",
        warnings=warnings,
        checks=checks,
        risk_level=risk_level,
        context={"plugin_name": plugin_name, "action": action},
    )


def evaluate_job_start_policy(job: dict[str, Any] | None) -> dict[str, Any]:
    if not job:
        return decision_payload(
            decision="block",
            reason="plugin job not found",
            blockers=["plugin job not found"],
            checks=[_check("job_exists", "failed")],
        )
    plugin_name = str(job.get("plugin_name") or "")
    action = str(job.get("action") or "")
    item, governance, risk_level, blockers, warnings, checks = _plugin_context(
        plugin_name
    )
    checks.insert(0, _check("job_exists", "passed", {"job_id": job.get("job_id")}))
    status = str(job.get("status") or "unknown")
    approval_state = str(job.get("approval_state") or "unknown")
    if status in TERMINAL_JOB_STATUSES:
        blockers.append(
            f"terminal job status cannot start without an explicit retry path: {status}"
        )
        checks.append(_check("terminal_status", "failed", {"status": status}))
    else:
        checks.append(_check("terminal_status", "passed", {"status": status}))
    if approval_state == "rejected" or status == "rejected":
        blockers.append("rejected job cannot start")
        checks.append(_check("not_rejected", "failed"))
    else:
        checks.append(
            _check("not_rejected", "passed", {"approval_state": approval_state})
        )
    if looks_secret(job.get("input_summary")):
        blockers.append("job input appears to contain raw secret material")
        checks.append(_check("raw_secret_input", "failed"))
    else:
        checks.append(_check("raw_secret_input", "passed"))
    approval_reasons = _approval_reasons_for_action(
        action, governance, risk_level, bool(job.get("approval_required"))
    )
    if approval_reasons and approval_state != "approved":
        checks.append(
            _check("approval_state", "failed", {"approval_state": approval_state})
        )
        if blockers:
            return decision_payload(
                decision="block",
                reason="job start blocked by plugin policy",
                blockers=blockers,
                warnings=warnings,
                approval_reasons=approval_reasons,
                checks=checks,
                risk_level=risk_level,
                context={
                    "job_id": job.get("job_id"),
                    "plugin_name": plugin_name,
                    "action": action,
                },
            )
        return decision_payload(
            decision="needs_approval",
            reason="job start requires approval before running",
            blockers=blockers,
            warnings=warnings,
            approval_reasons=approval_reasons,
            checks=checks,
            risk_level=risk_level,
            context={
                "job_id": job.get("job_id"),
                "plugin_name": plugin_name,
                "action": action,
            },
        )
    checks.append(
        _check("approval_state", "passed", {"approval_state": approval_state})
    )
    if blockers:
        return decision_payload(
            decision="block",
            reason="job start blocked by plugin policy",
            blockers=blockers,
            warnings=warnings,
            approval_reasons=approval_reasons,
            checks=checks,
            risk_level=risk_level,
            context={
                "job_id": job.get("job_id"),
                "plugin_name": plugin_name,
                "action": action,
            },
        )
    return decision_payload(
        decision="allow",
        reason="job start allowed by plugin policy",
        warnings=warnings,
        approval_reasons=approval_reasons,
        checks=checks,
        risk_level=risk_level,
        context={
            "job_id": job.get("job_id"),
            "plugin_name": plugin_name,
            "action": action,
        },
    )


def _artifact_has_provenance(artifact: dict[str, Any]) -> bool:
    provenance = artifact.get("provenance")
    if not isinstance(provenance, dict) or not provenance:
        return False
    return bool(
        provenance.get("source_job")
        or provenance.get("provider")
        or provenance.get("provider_or_service")
        or artifact.get("provider_or_service")
    )


def evaluate_artifact_action_policy(
    artifact: dict[str, Any] | None, action: str, *, target: str | None = None
) -> dict[str, Any]:
    if not artifact:
        return decision_payload(
            decision="block",
            reason="plugin artifact not found",
            blockers=["plugin artifact not found"],
            checks=[_check("artifact_exists", "failed")],
        )
    plugin_name = str(artifact.get("plugin_name") or "")
    item, governance, risk_level, blockers, warnings, checks = _plugin_context(
        plugin_name
    )
    checks.insert(
        0,
        _check(
            "artifact_exists", "passed", {"artifact_id": artifact.get("artifact_id")}
        ),
    )
    approval_state = str(artifact.get("approval_state") or "pending")
    publish_state = str(artifact.get("publish_state") or "draft")
    if approval_state == "rejected" or publish_state == "rejected":
        blockers.append("rejected artifact cannot publish or export")
        checks.append(
            _check(
                "not_rejected",
                "failed",
                {"approval_state": approval_state, "publish_state": publish_state},
            )
        )
    else:
        checks.append(
            _check(
                "not_rejected",
                "passed",
                {"approval_state": approval_state, "publish_state": publish_state},
            )
        )
    if not _artifact_has_provenance(artifact):
        blockers.append("artifact is missing provenance")
        checks.append(_check("artifact_provenance", "failed"))
    else:
        checks.append(_check("artifact_provenance", "passed"))
    approval_reasons = _approval_reasons_for_action(
        action, governance, risk_level, requested_approval_required=True
    )
    if approval_state != "approved":
        checks.append(
            _check("artifact_approval", "failed", {"approval_state": approval_state})
        )
        if blockers:
            return decision_payload(
                decision="block",
                reason=f"artifact {action} blocked by plugin policy",
                blockers=blockers,
                warnings=warnings,
                approval_reasons=approval_reasons,
                checks=checks,
                risk_level=risk_level,
                context={
                    "artifact_id": artifact.get("artifact_id"),
                    "plugin_name": plugin_name,
                    "action": action,
                    "target": target,
                },
            )
        return decision_payload(
            decision="needs_approval",
            reason=f"artifact {action} requires approval",
            warnings=warnings,
            approval_reasons=approval_reasons,
            checks=checks,
            risk_level=risk_level,
            context={
                "artifact_id": artifact.get("artifact_id"),
                "plugin_name": plugin_name,
                "action": action,
                "target": target,
            },
        )
    else:
        checks.append(
            _check("artifact_approval", "passed", {"approval_state": approval_state})
        )
    if blockers:
        return decision_payload(
            decision="block",
            reason=f"artifact {action} blocked by plugin policy",
            blockers=blockers,
            warnings=warnings,
            approval_reasons=approval_reasons,
            checks=checks,
            risk_level=risk_level,
            context={
                "artifact_id": artifact.get("artifact_id"),
                "plugin_name": plugin_name,
                "action": action,
                "target": target,
            },
        )
    return decision_payload(
        decision="allow",
        reason=f"artifact {action} allowed by plugin policy",
        warnings=warnings,
        approval_reasons=approval_reasons,
        checks=checks,
        risk_level=risk_level,
        context={
            "artifact_id": artifact.get("artifact_id"),
            "plugin_name": plugin_name,
            "action": action,
            "target": target,
        },
    )


def preview_policy(
    kind: str,
    *,
    job: dict[str, Any] | None = None,
    artifact: dict[str, Any] | None = None,
    plugin_name: str | None = None,
    action: str | None = None,
    input_summary: Any | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    if kind == "job_start":
        return evaluate_job_start_policy(job)
    if kind == "artifact_export":
        return evaluate_artifact_action_policy(artifact, "export", target=target)
    if kind == "artifact_publish_ready":
        return evaluate_artifact_action_policy(artifact, "publish-ready", target=target)
    if kind == "job_request":
        return evaluate_job_request_policy(
            str(plugin_name or ""), str(action or ""), input_summary=input_summary
        )
    return decision_payload(
        decision="block",
        reason=f"unsupported policy preview kind: {kind}",
        blockers=[f"unsupported policy preview kind: {kind}"],
        checks=[_check("preview_kind", "failed", {"kind": kind})],
    )
