"""Independent receipt validator (freshness, revocation, merge eligibility).

GRO-4208 / PNV-4 Provider-neutral receipt validation layer.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import jsonschema
    from jsonschema import Draft202012Validator
except ImportError:
    jsonschema = None
    Draft202012Validator = None

SHA1_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
DIGEST_PATTERN = re.compile(r"^(sha256:[0-9a-fA-F]{64}|sha512:[0-9a-fA-F]{128})$")
ALL_ZERO_SHA256 = "sha256:" + "0" * 64
ALL_ZERO_SHA512 = "sha512:" + "0" * 128
ALL_ZERO_GIT_SHA = "0" * 40

RECEIPT_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "schemas"
    / "provider-neutral-verification-receipt.schema.json"
)

POLICY_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "schemas"
    / "provider-neutral-verification-policy.schema.json"
)

CANONICAL_POLICY_BINDINGS_KEYS = {
    "require_base_sha",
    "require_candidate_sha",
    "require_tree_sha",
    "require_changed_paths",
    "allowed_paths",
}

ALLOWED_POLICY_BINDINGS_OVERLAY_KEYS = {
    "expected_candidate_sha",
    "expected_base_sha",
    "expected_tree_sha",
    "allow_empty_changed_paths",
}

_RECEIPT_SCHEMA_CACHE: dict[str, Any] | None = None
_POLICY_SCHEMA_CACHE: dict[str, Any] | None = None


def _get_receipt_schema() -> dict[str, Any] | None:
    global _RECEIPT_SCHEMA_CACHE
    if _RECEIPT_SCHEMA_CACHE is None and RECEIPT_SCHEMA_PATH.exists():
        try:
            _RECEIPT_SCHEMA_CACHE = json.loads(
                RECEIPT_SCHEMA_PATH.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            _RECEIPT_SCHEMA_CACHE = None
    return _RECEIPT_SCHEMA_CACHE


def _get_policy_schema() -> dict[str, Any] | None:
    global _POLICY_SCHEMA_CACHE
    if _POLICY_SCHEMA_CACHE is None and POLICY_SCHEMA_PATH.exists():
        try:
            _POLICY_SCHEMA_CACHE = json.loads(
                POLICY_SCHEMA_PATH.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            _POLICY_SCHEMA_CACHE = None
    return _POLICY_SCHEMA_CACHE


def _parse_timestamp(ts_val: Any) -> datetime | None:
    if not isinstance(ts_val, str) or not ts_val.strip():
        return None
    ts_str = ts_val.strip()
    try:
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def validate_receipt_freshness(
    receipt: dict[str, Any], *, max_age_seconds: int = 3600
) -> tuple[bool, str | None]:
    """Return (is_fresh, reason_if_stale). Fail-closed, non-raising."""
    try:
        if (
            not isinstance(max_age_seconds, int)
            or isinstance(max_age_seconds, bool)
            or max_age_seconds <= 0
            or max_age_seconds > 31536000
        ):
            return False, "invalid_max_age_seconds"

        if not isinstance(receipt, dict):
            return False, "invalid_receipt_schema"

        ts_str = receipt.get("completed_at") or receipt.get("finished_at")
        if not ts_str or not isinstance(ts_str, str):
            return False, "missing_timestamp"

        completed_dt = _parse_timestamp(ts_str)
        if completed_dt is None:
            return False, "malformed_timestamp"

        started_str = receipt.get("started_at")
        if started_str is not None:
            if not isinstance(started_str, str):
                return False, "malformed_timestamp"
            started_dt = _parse_timestamp(started_str)
            if started_dt is None:
                return False, "malformed_timestamp"
            if completed_dt < started_dt:
                return False, "timestamp_ordering_invalid"

        expires_str = receipt.get("expires_at")
        if not expires_str or not isinstance(expires_str, str):
            return False, "missing_expires_at"
        expires_dt = _parse_timestamp(expires_str)
        if expires_dt is None:
            return False, "malformed_expires_at"
        if expires_dt < completed_dt:
            return False, "expires_at_before_completed_at"

        now_utc = datetime.now(timezone.utc)

        age_seconds = (now_utc - completed_dt).total_seconds()

        # Reject future timestamps unless within clock-skew tolerance (<= 60s)
        if age_seconds < -60:
            return False, "timestamp_future"

        # Reject stale timestamps (> max_age_seconds)
        if age_seconds > max_age_seconds:
            return False, "receipt_stale"

        if expires_dt <= now_utc:
            return False, "receipt_expired"

        return True, None
    except Exception as exc:
        return False, f"freshness_validation_error: {exc}"


def check_revocation(
    receipt: dict[str, Any], *, revocation_store: Path | str | None = None
) -> tuple[bool, str | None]:
    """Return (not_revoked, reason_if_revoked). Fail-closed, non-raising."""
    try:
        if not isinstance(receipt, dict):
            return False, "invalid_receipt_schema"

        status = receipt.get("revocation_status")
        if status != "active":
            return False, f"revocation_status_{status}"

        if revocation_store is None:
            return True, None

        try:
            store_path = Path(revocation_store)
        except Exception:
            return False, "unsafe_revocation_store"

        try:
            if store_path.is_symlink():
                return False, "revocation_store_is_symlink"
        except OSError:
            return False, "revocation_store_error"

        if not store_path.exists():
            return False, "revocation_store_missing"

        if not store_path.is_file():
            return False, "unsafe_revocation_store"

        try:
            content = store_path.read_text(encoding="utf-8")
            data = json.loads(content)
        except (OSError, ValueError):
            return False, "malformed_revocation_store"

        if not isinstance(data, (list, dict)):
            return False, "malformed_revocation_store"

        revoked_items: set[str] = set()

        def _add_item(item: Any) -> None:
            if isinstance(item, str) and item:
                revoked_items.add(item)
            elif isinstance(item, dict):
                for k in ("id", "sha", "digest", "receipt_id", "task_id"):
                    v = item.get(k)
                    if isinstance(v, str) and v:
                        revoked_items.add(v)

        if isinstance(data, list):
            for item in data:
                _add_item(item)
        elif isinstance(data, dict):
            for val in data.values():
                if isinstance(val, list):
                    for item in val:
                        _add_item(item)
                else:
                    _add_item(val)

        # Check receipt IDs
        for key in ("task_id", "clean_checkout_id", "policy_id", "receipt_id", "id"):
            val = receipt.get(key)
            if isinstance(val, str) and val in revoked_items:
                return False, f"receipt_revoked_by_id_{val}"

        # Check receipt SHAs / digests
        shas_to_check: list[str] = []
        for key in (
            "candidate_sha",
            "base_sha",
            "tree_sha",
            "source_acquisition_digest",
            "environment_digest",
        ):
            val = receipt.get(key)
            if isinstance(val, str) and val:
                shas_to_check.append(val)

        for log_item in receipt.get("logs_and_digests") or []:
            if isinstance(log_item, dict) and isinstance(log_item.get("digest"), str):
                shas_to_check.append(log_item["digest"])

        for art_item in receipt.get("artifacts_and_digests") or []:
            if isinstance(art_item, dict) and isinstance(art_item.get("digest"), str):
                shas_to_check.append(art_item["digest"])

        for sha in shas_to_check:
            if sha in revoked_items:
                return False, f"receipt_revoked_by_sha_{sha}"

        return True, None
    except Exception as exc:
        return False, f"revocation_check_error: {exc}"


def determine_merge_eligibility(
    receipt: dict[str, Any],
    policy: dict[str, Any],
    *,
    revocation_store: Path | str | None = None,
    evidence_base_path: Path | str | None = None,
) -> tuple[bool, str | None]:
    """Return (eligible, reason_if_blocked).

    Combines freshness, revocation, provenance, and required evidence checks.
    Fail-closed semantics on missing, malformed, or invalid inputs.
    """
    try:
        if not isinstance(receipt, dict):
            return False, "schema_validation_failed: non-dict receipt"

        if not isinstance(policy, dict):
            return False, "schema_validation_failed: non-dict policy"

        # 1. Schema Validation (Draft 2020-12)
        if Draft202012Validator is None:
            return False, "missing_jsonschema"

        receipt_schema = _get_receipt_schema()
        policy_schema = _get_policy_schema()

        if receipt_schema is None or policy_schema is None:
            return False, "schema_load_failed: missing or unreadable schema file"

        # Inspect policy bindings for unknown overlay keys or invalid types
        policy_bindings = policy.get("bindings")
        if not isinstance(policy_bindings, dict):
            return False, "schema_validation_failed: policy bindings must be a dict"

        overlay_values: dict[str, Any] = {}
        for key, val in policy_bindings.items():
            if (
                key not in CANONICAL_POLICY_BINDINGS_KEYS
                and key not in ALLOWED_POLICY_BINDINGS_OVERLAY_KEYS
            ):
                return (
                    False,
                    f"schema_validation_failed: unknown policy binding overlay key '{key}'",
                )
            if key in ALLOWED_POLICY_BINDINGS_OVERLAY_KEYS:
                overlay_values[key] = val

        # Check overlay value types/formats
        if "expected_candidate_sha" in overlay_values:
            val = overlay_values["expected_candidate_sha"]
            if not isinstance(val, str) or not SHA1_PATTERN.match(val):
                return (
                    False,
                    "schema_validation_failed: invalid expected_candidate_sha",
                )
        if "expected_base_sha" in overlay_values:
            val = overlay_values["expected_base_sha"]
            if not isinstance(val, str) or not SHA1_PATTERN.match(val):
                return False, "schema_validation_failed: invalid expected_base_sha"
        if "expected_tree_sha" in overlay_values:
            val = overlay_values["expected_tree_sha"]
            if not isinstance(val, str) or not SHA1_PATTERN.match(val):
                return False, "schema_validation_failed: invalid expected_tree_sha"
        if "allow_empty_changed_paths" in overlay_values:
            val = overlay_values["allow_empty_changed_paths"]
            if not isinstance(val, bool):
                return (
                    False,
                    "schema_validation_failed: allow_empty_changed_paths must be boolean",
                )

        # Validate policy schema after removing overlay keys from bindings
        policy_copy = copy.deepcopy(policy)
        if isinstance(policy_copy.get("bindings"), dict):
            for k in ALLOWED_POLICY_BINDINGS_OVERLAY_KEYS:
                policy_copy["bindings"].pop(k, None)

        p_validator = Draft202012Validator(policy_schema)
        p_errors = list(p_validator.iter_errors(policy_copy))
        if p_errors:
            return False, f"schema_validation_failed: policy {p_errors[0].message}"

        # Validate receipt schema
        allow_empty_changed_paths = overlay_values.get(
            "allow_empty_changed_paths", False
        )
        r_validator = Draft202012Validator(receipt_schema)
        r_errors = list(r_validator.iter_errors(receipt))
        if r_errors:
            filtered_r_errors = []
            for err in r_errors:
                if (
                    list(err.path) == ["changed_paths"]
                    and err.validator == "minItems"
                    and allow_empty_changed_paths
                ):
                    continue
                filtered_r_errors.append(err)
            if filtered_r_errors:
                return (
                    False,
                    f"schema_validation_failed: receipt {filtered_r_errors[0].message}",
                )

        # 2. Policy status & Identity bindings
        if policy.get("status") != "active":
            return False, f"policy_status_{policy.get('status')}"

        if receipt.get("policy_id") != policy.get("policy_id"):
            return False, "policy_id_mismatch"

        if receipt.get("policy_version") != policy.get("policy_version"):
            return False, "policy_version_mismatch"

        repo_id = policy.get("repository", {}).get("repository_id")
        if receipt.get("repository_id") != repo_id:
            return False, "repository_id_mismatch"

        # 3. Source requirements
        src_reqs = policy.get("repository", {}).get("source_requirements", {})
        allowed_kinds = src_reqs.get("allowed_source_kinds", [])
        if receipt.get("source_kind") not in allowed_kinds:
            return False, "unapproved_source_kind"

        allowed_providers = src_reqs.get("allowed_source_providers", [])
        if receipt.get("source_provider") not in allowed_providers:
            return False, "unapproved_source_provider"

        # 4. Approved Backends & Verifiers
        approved_backends = policy.get("approved_backends", [])
        r_backend_id = receipt.get("backend_id")
        r_backend_class = receipt.get("backend_class")
        backend_approved = any(
            isinstance(b, dict)
            and b.get("id") == r_backend_id
            and b.get("class") == r_backend_class
            for b in approved_backends
        )
        if not backend_approved:
            return False, "unapproved_backend"

        approved_verifiers = policy.get("approved_verifiers", {})
        identities = approved_verifiers.get("identities", [])
        r_verifier_id = receipt.get("verifier_id")
        if r_verifier_id not in identities:
            return False, "unapproved_verifier_identity"

        # 5. Producer / Verifier separation
        if approved_verifiers.get("require_producer_verifier_separation", False):
            prod_id = receipt.get("producer_id")
            ver_id = receipt.get("verifier_id")
            if not prod_id or not ver_id or prod_id == ver_id:
                return False, "producer_verifier_separation_failed"

        # 6. Receipt decision status & merge_eligible
        decision = receipt.get("decision")
        if not isinstance(decision, dict):
            return False, "invalid_receipt_decision"
        if decision.get("status") != "pass":
            return False, f"decision_status_{decision.get('status')}"
        if decision.get("merge_eligible") is not True:
            return False, "decision_not_merge_eligible"

        # 7. Freshness check
        max_age = policy.get("freshness", {}).get("max_age_seconds", 3600)
        is_fresh, freshness_reason = validate_receipt_freshness(
            receipt, max_age_seconds=max_age
        )
        if not is_fresh:
            return False, f"freshness_failed: {freshness_reason}"

        # 8. Revocation check
        not_revoked, revocation_reason = check_revocation(
            receipt, revocation_store=revocation_store
        )
        if not not_revoked:
            return False, revocation_reason

        # 9. Git SHAs and overlay bindings
        for field in ("candidate_sha", "base_sha", "tree_sha"):
            val = receipt.get(field)
            if not val or not isinstance(val, str) or not SHA1_PATTERN.match(val):
                return False, f"malformed_{field}"
            if val == ALL_ZERO_GIT_SHA:
                return False, f"all_zero_{field}"

        exp_candidate = overlay_values.get("expected_candidate_sha")
        if exp_candidate and receipt.get("candidate_sha") != exp_candidate:
            return False, "candidate_sha_mismatch"

        exp_base = overlay_values.get("expected_base_sha")
        if exp_base and receipt.get("base_sha") != exp_base:
            return False, "base_sha_mismatch"

        exp_tree = overlay_values.get("expected_tree_sha")
        if exp_tree and receipt.get("tree_sha") != exp_tree:
            return False, "tree_sha_mismatch"

        # 10. Changed paths check
        changed_paths = receipt.get("changed_paths")
        if not isinstance(changed_paths, list):
            return False, "malformed_changed_paths"

        require_changed_paths = policy.get("bindings", {}).get(
            "require_changed_paths", True
        )
        if (
            len(changed_paths) == 0
            and require_changed_paths
            and not allow_empty_changed_paths
        ):
            return False, "empty_changed_paths"

        allowed_paths = policy.get("bindings", {}).get("allowed_paths")
        if allowed_paths is not None:
            allowed_set = set(allowed_paths)
            for p in changed_paths:
                if p not in allowed_set:
                    return False, f"disallowed_changed_path: {p}"

        # 11. Command validation (unique IDs, required presence, argv & proof_class binding, zero exit)
        policy_cmds = policy.get("commands", [])
        policy_cmd_ids: set[str] = set()
        for p_cmd in policy_cmds:
            if isinstance(p_cmd, dict):
                cid = p_cmd.get("id")
                if cid in policy_cmd_ids:
                    return False, f"duplicate_policy_command_id: {cid}"
                if cid:
                    policy_cmd_ids.add(cid)

        r_cmds = receipt.get("commands_and_exit_states")
        if not isinstance(r_cmds, list) or len(r_cmds) == 0:
            return False, "missing_commands_and_exit_states"

        r_cmd_map: dict[str, dict[str, Any]] = {}
        for r_cmd in r_cmds:
            if not isinstance(r_cmd, dict):
                return False, "malformed_command_record"
            cid = r_cmd.get("command_id")
            if not cid or cid in r_cmd_map:
                return False, f"duplicate_receipt_command_id: {cid}"
            if cid not in policy_cmd_ids:
                return False, f"unapproved_receipt_command: {cid}"
            r_cmd_map[cid] = r_cmd

            if r_cmd.get("execution_state") != "executed":
                return False, f"command_not_executed: {cid}"
            if r_cmd.get("exit_state") != "completed":
                return False, f"command_not_completed: {cid}"
            if r_cmd.get("exit_code") != 0:
                return False, "non_zero_exit_code"

        for p_cmd in policy_cmds:
            if isinstance(p_cmd, dict):
                cid = p_cmd.get("id")
                is_req = p_cmd.get("required", True)
                if is_req and cid not in r_cmd_map:
                    return False, f"missing_required_command: {cid}"
                if cid in r_cmd_map:
                    r_cmd = r_cmd_map[cid]
                    if r_cmd.get("argv") != p_cmd.get("argv"):
                        return False, f"command_argv_mismatch: {cid}"
                    if r_cmd.get("proof_class") != p_cmd.get("proof_class"):
                        return False, f"command_proof_class_mismatch: {cid}"

        # 12. Proof classes coverage
        proof_classes = receipt.get("proof_classes")
        if not isinstance(proof_classes, list) or len(proof_classes) == 0:
            return False, "missing_proof_classes"

        r_proof_set = set(proof_classes)
        required_proof_classes = policy.get("required_proof_classes") or []
        for req_pc in required_proof_classes:
            if req_pc not in r_proof_set:
                return False, f"missing_required_proof_class: {req_pc}"

        approved_proof_classes = policy.get("approved_proof_classes")
        if approved_proof_classes is not None:
            approved_set = set(approved_proof_classes)
            for pc in proof_classes:
                if pc not in approved_set:
                    return False, f"unapproved_proof_class: {pc}"

        # 13. Evidence / Environment / Attestation requirements
        evidence_policy = policy.get("evidence", {})
        if evidence_policy.get("logs_required", False):
            r_logs = receipt.get("logs_and_digests")
            if not isinstance(r_logs, list) or len(r_logs) == 0:
                return False, "missing_required_log_evidence"

        if evidence_policy.get("artifacts_required", False):
            r_arts = receipt.get("artifacts_and_digests")
            if not isinstance(r_arts, list) or len(r_arts) == 0:
                return False, "missing_required_artifact_evidence"

        env_policy = policy.get("environment", {})
        permitted_algo = env_policy.get("digest_algorithm", "sha256")

        if policy.get("clean_room", {}).get("source_acquisition_required", False):
            s_dig = receipt.get("source_acquisition_digest")
            if (
                not s_dig
                or not isinstance(s_dig, str)
                or not DIGEST_PATTERN.match(s_dig)
                or s_dig in (ALL_ZERO_SHA256, ALL_ZERO_SHA512)
            ):
                return False, "invalid_source_acquisition_digest"
            if not s_dig.startswith(f"{permitted_algo}:"):
                return False, "unpermitted_digest_algorithm"

        if env_policy.get("environment_digest_required", False):
            e_dig = receipt.get("environment_digest")
            if (
                not e_dig
                or not isinstance(e_dig, str)
                or not DIGEST_PATTERN.match(e_dig)
                or e_dig in (ALL_ZERO_SHA256, ALL_ZERO_SHA512)
            ):
                return False, "invalid_environment_digest"
            if not e_dig.startswith(f"{permitted_algo}:"):
                return False, "unpermitted_digest_algorithm"

        # Check evidence.digest_requirements entries
        digest_reqs = evidence_policy.get("digest_requirements")
        if digest_reqs is not None:
            if not isinstance(digest_reqs, list):
                return (
                    False,
                    "schema_validation_failed: digest_requirements must be a list",
                )

            # Check for contradictory policy requirements first
            seen_reqs: dict[tuple[str, str | None], str] = {}
            for req in digest_reqs:
                if not isinstance(req, dict):
                    return (
                        False,
                        "schema_validation_failed: malformed digest_requirement",
                    )
                kind = req.get("kind")
                algo = req.get("algorithm")
                name = req.get("name")

                if kind not in (
                    "log",
                    "artifact",
                    "source_acquisition",
                    "environment",
                    "toolchain",
                ):
                    return False, f"unsupported_digest_requirement_kind: {kind}"
                if algo not in ("sha256", "sha512"):
                    return False, f"unsupported_digest_requirement_algorithm: {algo}"

                key = (kind, name)
                if key in seen_reqs and seen_reqs[key] != algo:
                    return False, "contradictory_digest_requirements"
                seen_reqs[key] = algo

                if (
                    kind in ("environment", "source_acquisition")
                    and permitted_algo
                    and algo != permitted_algo
                ):
                    return False, "contradictory_digest_requirements"

            r_logs_list = receipt.get("logs_and_digests") or []
            if not isinstance(r_logs_list, list):
                r_logs_list = []
            r_arts_list = receipt.get("artifacts_and_digests") or []
            if not isinstance(r_arts_list, list):
                r_arts_list = []

            for req in digest_reqs:
                kind = req.get("kind")
                algo = req.get("algorithm")
                is_req = req.get("required", False)
                req_name = req.get("name")

                if kind == "log":
                    if req_name:
                        matching_items = [
                            item
                            for item in r_logs_list
                            if isinstance(item, dict)
                            and (
                                item.get("reference") == req_name
                                or Path(str(item.get("reference"))).name == req_name
                            )
                        ]
                    else:
                        matching_items = [
                            item for item in r_logs_list if isinstance(item, dict)
                        ]

                    if is_req and len(matching_items) == 0:
                        return False, "missing_required_digest_evidence: log"

                    for item in matching_items:
                        d_val = item.get("digest")
                        if isinstance(d_val, str) and not d_val.startswith(f"{algo}:"):
                            return False, "digest_algorithm_mismatch: log"

                elif kind == "artifact":
                    if req_name:
                        matching_items = [
                            item
                            for item in r_arts_list
                            if isinstance(item, dict)
                            and (
                                item.get("reference") == req_name
                                or Path(str(item.get("reference"))).name == req_name
                            )
                        ]
                    else:
                        matching_items = [
                            item for item in r_arts_list if isinstance(item, dict)
                        ]

                    if is_req and len(matching_items) == 0:
                        return False, "missing_required_digest_evidence: artifact"

                    for item in matching_items:
                        d_val = item.get("digest")
                        if isinstance(d_val, str) and not d_val.startswith(f"{algo}:"):
                            return False, "digest_algorithm_mismatch: artifact"

                elif kind == "source_acquisition":
                    s_dig = receipt.get("source_acquisition_digest")
                    if is_req:
                        if (
                            not s_dig
                            or not isinstance(s_dig, str)
                            or not DIGEST_PATTERN.match(s_dig)
                            or s_dig in (ALL_ZERO_SHA256, ALL_ZERO_SHA512)
                        ):
                            return False, "invalid_source_acquisition_digest"
                    if s_dig and isinstance(s_dig, str):
                        if not s_dig.startswith(f"{algo}:"):
                            return (
                                False,
                                "digest_algorithm_mismatch: source_acquisition",
                            )

                elif kind == "environment":
                    e_dig = receipt.get("environment_digest")
                    if is_req:
                        if (
                            not e_dig
                            or not isinstance(e_dig, str)
                            or not DIGEST_PATTERN.match(e_dig)
                            or e_dig in (ALL_ZERO_SHA256, ALL_ZERO_SHA512)
                        ):
                            return False, "invalid_environment_digest"
                    if e_dig and isinstance(e_dig, str):
                        if not e_dig.startswith(f"{algo}:"):
                            return False, "digest_algorithm_mismatch: environment"

                elif kind == "toolchain":
                    toolchain_items = []
                    for items in (r_arts_list, r_logs_list):
                        for item in items:
                            if isinstance(item, dict):
                                ref = item.get("reference")
                                if isinstance(ref, str):
                                    if req_name and (
                                        ref == req_name or Path(ref).name == req_name
                                    ):
                                        toolchain_items.append(item)
                                    elif not req_name and ("toolchain" in ref.lower()):
                                        toolchain_items.append(item)

                    if is_req and len(toolchain_items) == 0:
                        return False, "unprovable_toolchain_digest_requirement"

                    for item in toolchain_items:
                        d_val = item.get("digest")
                        if isinstance(d_val, str) and not d_val.startswith(f"{algo}:"):
                            return False, "digest_algorithm_mismatch: toolchain"

        # Collect evidence items
        all_evidence_items: list[dict[str, Any]] = []
        for k in ("logs_and_digests", "artifacts_and_digests"):
            items = receipt.get(k)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        all_evidence_items.append(item)

        for item in all_evidence_items:
            d_val = item.get("digest")
            if (
                not d_val
                or not isinstance(d_val, str)
                or not DIGEST_PATTERN.match(d_val)
                or d_val in (ALL_ZERO_SHA256, ALL_ZERO_SHA512)
            ):
                return False, "malformed_digest"

        # 14. File evidence verification if evidence_base_path is supplied
        if evidence_base_path is not None:
            try:
                base_root = Path(evidence_base_path).resolve()
            except Exception:
                return False, "evidence_base_path_invalid"

            if not base_root.exists() or not base_root.is_dir():
                return False, "evidence_base_path_invalid"

            for item in all_evidence_items:
                ref = item.get("reference")
                if not ref or not isinstance(ref, str):
                    return False, "missing_evidence_reference"

                if ref.startswith("/") or Path(ref).is_absolute():
                    return False, "unsafe_evidence_reference_absolute"

                if ".." in Path(ref).parts:
                    return False, "unsafe_evidence_reference_traversal"

                target_path = base_root / ref

                try:
                    if target_path.is_symlink():
                        return False, "unsafe_evidence_reference_symlink"
                except OSError:
                    return False, "unsafe_evidence_reference_error"

                if not target_path.exists():
                    return False, "missing_evidence_file"

                if not target_path.is_file():
                    return False, "unsafe_evidence_reference_not_regular"

                try:
                    resolved_target = target_path.resolve()
                    resolved_target.relative_to(base_root)
                except ValueError:
                    return False, "unsafe_evidence_path_escape"

                try:
                    file_bytes = target_path.read_bytes()
                except OSError:
                    return False, "evidence_read_error"

                d_val = item["digest"]
                algo, expected_hex = d_val.split(":", 1)
                if algo == "sha256":
                    calc_hex = hashlib.sha256(file_bytes).hexdigest()
                elif algo == "sha512":
                    calc_hex = hashlib.sha512(file_bytes).hexdigest()
                else:
                    return False, "unsupported_digest_algorithm"

                if calc_hex.lower() != expected_hex.lower():
                    return False, "evidence_digest_mismatch"

        # 15. Attestation policy check
        att_policy = policy.get("attestation", {})
        if att_policy.get("required", False):
            sig = receipt.get("signature_or_attestation")
            if not isinstance(sig, dict):
                return False, "missing_attestation"
            val = sig.get("value")
            if not val or not isinstance(val, str) or not val.strip():
                return False, "empty_attestation_value"
            algo = sig.get("algorithm")
            allowed_algos = att_policy.get("allowed_algorithms", [])
            if algo not in allowed_algos:
                return False, "unapproved_attestation_algorithm"
            key_id = sig.get("key_id")
            allowed_key_ids = att_policy.get("allowed_key_ids", [])
            if key_id not in allowed_key_ids:
                return False, "unapproved_attestation_key_id"

        return True, None

    except Exception as exc:
        return False, f"eligibility_check_error: {exc}"
