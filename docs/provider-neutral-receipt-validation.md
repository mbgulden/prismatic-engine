# Provider-Neutral Receipt Validation (GRO-4208 / PNV-4)

## Overview

The Provider-Neutral Receipt Validation module (`prismatic.verification.receipt_validator`) provides an independent, fail-closed verification layer sitting between clean-room execution outputs and merge decisions.

It enforces five critical dimensions of receipt integrity:
1. **Schema Validation**: Strictly validates both receipt and policy documents against their canonical JSON Schema (Draft 2020-12) contracts (`provider-neutral-verification-receipt.schema.json` and `provider-neutral-verification-policy.schema.json`).
2. **Freshness & Expiry**: Verifies receipt completion timestamps, ordering (`started_at` <= `completed_at` <= `expires_at`), clock-skew limits (<=60s), max age bounds, and non-expired status (`expires_at` > `now_utc`).
3. **Revocation Checking**: Verifies receipt IDs, commit SHAs, and evidence log/artifact digests against atomic revocation stores. Provided-but-missing revocation stores, symlinks, or non-regular files fail closed.
4. **Merge Eligibility & Policy Binding**: Validates repository ID, policy ID/version, approved backend ID/class, approved verifier identity, source requirements, command execution states/exit codes/argv/proof-classes, and producer/verifier separation.
5. **Fail-Closed Semantics**: Rejects malformed timestamps, unknown revocation states, missing required fields, non-zero command exit codes, invalid digest formats, path escapes, symlinks, missing evidence files, or unsupported overlay keys without raising unhandled exceptions.

## Public API

```python
from prismatic.verification import (
    validate_receipt_freshness,
    check_revocation,
    determine_merge_eligibility,
)
```

### `validate_receipt_freshness(receipt: dict, *, max_age_seconds: int = 3600) -> tuple[bool, str | None]`

Evaluates completion timestamp (`completed_at` or `finished_at`), start timestamp (`started_at`), and expiration timestamp (`expires_at`):
- **`max_age_seconds`**: Maximum allowable age in seconds (default: 3600s / 1 hour). Must be positive integer <= 31536000.
- **Clock Skew Tolerance**: Future timestamps are rejected unless within <=60 seconds of current UTC time.
- **Expiration**: `expires_at` must be present, parseable, and not already expired (`expires_at` > `now_utc`).
- **Timestamp Coherence**: `started_at` <= `completed_at` <= `expires_at`.
- **Returns**: `(is_fresh, reason_if_stale)`

### `check_revocation(receipt: dict, *, revocation_store: Path | str | None = None) -> tuple[bool, str | None]`

Checks whether a receipt has been revoked:
- Top-level `revocation_status` must equal `"active"`. Any other status (`"revoked"`, `"unknown"`) fails closed.
- **`revocation_store`**: Optional path to a JSON file containing revoked IDs or SHAs/digests.
  - If explicitly supplied, the path must exist, be a regular file (not a symlink), and contain valid JSON.
  - Provided-but-missing store files, symlinks, unreadable files, or malformed content return a fail-closed decision (`not_revoked=False`).
- **Returns**: `(not_revoked, reason_if_revoked)`

### `determine_merge_eligibility(receipt: dict, policy: dict, *, revocation_store: Path | str | None = None, evidence_base_path: Path | str | None = None) -> tuple[bool, str | None]`

Synthesizes policy rules and receipt evidence into an authoritative merge decision:
- Schema validation against canonical Draft 2020-12 schemas for both receipt and policy.
- Runtime binding overlay validation: overlay keys in policy `bindings` must include all three mandatory exact SHA bindings (`expected_candidate_sha`, `expected_base_sha`, and `expected_tree_sha`) and optionally `allow_empty_changed_paths`. Missing any required SHA binding or including unknown overlay keys returns a fail-closed decision. Receipt candidate, base, and tree SHAs must exactly match all three overlay bindings.
- Verifies policy status (`active` required) and matching `policy_id`, `policy_version`, and `repository_id`.
- Enforces approved source kind/provider, approved backend ID/class, approved verifier identity, and producer/verifier separation.
- Verifies receipt decision `status == "pass"` and `merge_eligible is True`.
- Command verification: unique command IDs, required command presence, exact `argv` and `proof_class` matching, `execution_state == "executed"`, `exit_state == "completed"`, and `exit_code == 0`.
- Command timeout and duration enforcement: validates coherent `started_at` and `completed_at` timestamps (`started_at` <= `completed_at`), preserves the schema's full nanosecond precision while requiring every command interval to be contained within the top-level receipt execution interval, requires and validates `duration_ms` (integer >= 0), binds `duration_ms` to timestamp-derived duration within a 1000 ms rounding tolerance, and rejects measured or timestamp-derived durations exceeding policy `timeout_seconds`.
- Receipt freshness preserves nanosecond precision for `started_at <= completed_at <= expires_at`, future-skew, maximum-age, and expiry checks.
- Enforces required and approved `proof_classes` (uses `proof_classes` list; singular `proof_class` fallback is removed).
- Enforces evidence/environment/attestation requirements, including permitted digest algorithm (`sha256`/`sha512`) and attestation key/algorithm rules.
- Toolchain proof: when canonical policy `environment.toolchain_digest_required` is `true`, requires schema-supported toolchain evidence in `logs_and_digests` or `artifacts_and_digests` using the documented `"toolchain"` reference convention and matching the policy environment digest algorithm. Missing evidence or algorithm mismatch fails closed.
- Evidence path safety: when `evidence_base_path` is supplied, every evidence reference must be a safe relative path contained under `evidence_base_path`. Absolute paths, `..` traversal, symlinks, non-regular files, missing files, or content digest mismatches return `eligible=False`.
- **Returns**: `(eligible, reason_if_blocked)`

## Explicit Boundaries & Scope

- **Revocation Store**: `revocation_store=None` remains allowed by the original GRO-4208 contract. When a store path is explicitly supplied, missing store files, symlinks, or malformed contents fail closed.
- **Evidence Base Path**: File-content digest comparison against disk is conditional on a supplied `evidence_base_path`, as specified by GRO-4208.
- **Cryptographic Attestations**: Full cryptographic signature verification and key rotation belong to GRO-4209. The GRO-4208 validator performs envelope-only structural checking and algorithm/key_id whitelist matching.
- **Unproven Dimensions**: Working-directory constraints and network-isolation proof currently lack receipt schema fields; they are documented as unproven payload dimensions rather than fabricating non-contractual validation.

## Usage Example

```python
from pathlib import Path
from prismatic.verification import (
    validate_receipt_freshness,
    check_revocation,
    determine_merge_eligibility,
)

# 1. Check Freshness
is_fresh, freshness_reason = validate_receipt_freshness(receipt_data, max_age_seconds=1800)
if not is_fresh:
    print(f"Stale receipt: {freshness_reason}")

# 2. Check Revocation
store_path = Path("/etc/prismatic/revocations.json")
not_revoked, revocation_reason = check_revocation(receipt_data, revocation_store=store_path)

# 3. Determine Merge Eligibility
eligible, block_reason = determine_merge_eligibility(
    receipt=receipt_data,
    policy=policy_data,
    revocation_store=store_path,
    evidence_base_path=Path("/var/log/clean_room"),
)
if eligible:
    print("Receipt authorized for merge")
else:
    print(f"Merge blocked: {block_reason}")
```
