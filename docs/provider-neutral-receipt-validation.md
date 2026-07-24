# Provider-Neutral Receipt Validation & Attestation (GRO-4208 / GRO-4209 / PNV-4 / PNV-5)

## Overview

The Provider-Neutral Receipt Validation & Attestation module (`prismatic.verification.receipt_validator` and `prismatic.verification.attestation`) provides an independent, fail-closed verification layer sitting between clean-room execution outputs and merge decisions.

It enforces six critical dimensions of receipt integrity:
1. **Schema Validation**: Strictly validates both receipt and policy documents against their canonical JSON Schema (Draft 2020-12) contracts (`provider-neutral-verification-receipt.schema.json` and `provider-neutral-verification-policy.schema.json`).
2. **Freshness & Expiry**: Verifies receipt completion timestamps, ordering (`started_at` <= `completed_at` <= `expires_at`), clock-skew limits (<=60s), max age bounds, and non-expired status (`expires_at` > `now_utc`).
3. **Revocation Checking**: Verifies receipt IDs, commit SHAs, and evidence log/artifact digests against atomic revocation stores. Provided-but-missing revocation stores, symlinks, or non-regular files fail closed.
4. **Merge Eligibility & Policy Binding**: Validates repository ID, policy ID/version, approved backend ID/class, approved verifier identity, source requirements, command execution states/exit codes/argv/proof-classes, and producer/verifier separation.
5. **Ed25519 Receipt Attestation & Verifier Identity**: Cryptographically verifies receipt signatures against policy-carried verifier key records, enforcing active/expired/revoked key status, deterministic receipt canonicalization, and trust rotation.
6. **Fail-Closed Semantics**: Rejects malformed timestamps, unknown revocation states, missing required fields, non-zero command exit codes, invalid digest formats, path escapes, symlinks, missing evidence files, malformed signatures, or unsupported overlay keys without raising unhandled exceptions.

## Public API

```python
from prismatic.verification import (
    validate_receipt_freshness,
    check_revocation,
    determine_merge_eligibility,
    canonicalize_receipt,
    verify_receipt_attestation,
)
```

### `canonicalize_receipt(receipt: dict) -> bytes`

Exposes the exact, deterministic canonicalization function for receipt signing and signature verification.

- **Contract**:
  1. **Non-Mutating**: Deep-copies caller input dictionary before modification, guaranteeing original inputs are never mutated.
  2. **Field Exclusion**: Excludes ONLY `signature_or_attestation.value`. Retains attestation `type`, `algorithm`, `key_id`, `issuer`, and all other metadata intact inside the signed payload.
  3. **JSON Encoding**: Serializes UTF-8 JSON with sorted keys (`sort_keys=True`), compact separators (`','`, `':'`), `ensure_ascii=False` (preserving UTF-8 characters), and `allow_nan=False`.

### `verify_receipt_attestation(receipt: dict, policy: dict) -> tuple[bool, str | None]`

Verifies receipt Ed25519 attestation signatures against policy-carried verifier key records:

- **Attestation Envelope**: Requires `signature_or_attestation.type == "attestation"` and `algorithm == "ed25519"`.
- **Policy Restrictions**: Enforces policy `attestation.allowed_algorithms` and `attestation.allowed_key_ids` before cryptographic evaluation.
- **Strict Base64**: Decodes `signature_or_attestation.value` expecting raw 64-byte Ed25519 signature in standard strict Base64.
- **Verifier Key Lookup**: Matches key record by receipt `verifier_id` and attestation `key_id`. Rejects duplicate key IDs across policy verifiers.
- **Key Lifecycle & Rotation**: Evaluates key timestamps against receipt `completed_at`:
  - **Active**: `key.created_at <= receipt.completed_at`. Rejects keys created after receipt completion.
  - **Expiry**: `receipt.completed_at < key.expires_at`. Rejects receipts signed at or after key expiration.
  - **Revocation**: Any non-null `revoked_at` in the key record fails closed regardless of receipt signing time.
  - **Fresh Key Policy**: If `attestation.require_fresh_key` is true, rejects receipts where `receipt.completed_at - key.created_at > max_key_age_seconds`.
- **Cryptographic Verification**: Loads Ed25519 public key from PEM string and verifies the signature over `canonicalize_receipt(receipt)`.
- **Returns**: `(is_valid, reason_if_invalid)`

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

Synthesizes policy rules, receipt evidence, and cryptographic attestations into an authoritative merge decision:
- Schema validation against canonical Draft 2020-12 schemas for both receipt and policy.
- Runtime binding overlay validation: overlay keys in policy `bindings` must include all three mandatory exact SHA bindings (`expected_candidate_sha`, `expected_base_sha`, and `expected_tree_sha`) and optionally `allow_empty_changed_paths`. Missing any required SHA binding or including unknown overlay keys returns a fail-closed decision. Receipt candidate, base, and tree SHAs must exactly match all three overlay bindings.
- Verifies policy status (`active` required) and matching `policy_id`, `policy_version`, and `repository_id`.
- Enforces approved source kind/provider, approved backend ID/class, approved verifier identity, and producer/verifier separation.
- Verifies receipt decision `status == "pass"` and `merge_eligible is True`.
- Command verification: unique command IDs, required command presence, exact `argv` and `proof_class` matching, `execution_state == "executed"`, `exit_state == "completed"`, and `exit_code == 0`.
- Command timeout and duration enforcement: validates coherent `started_at` and `completed_at` timestamps (`started_at` <= `completed_at`), preserves full nanosecond precision while requiring every command interval to be contained within the top-level receipt execution interval, requires and validates `duration_ms` (integer >= 0), binds `duration_ms` to timestamp-derived duration within a 1000 ms rounding tolerance, and rejects measured or timestamp-derived durations exceeding policy `timeout_seconds`.
- Receipt freshness preserves nanosecond precision for `started_at <= completed_at <= expires_at`, future-skew, maximum-age, and expiry checks.
- Enforces required and approved `proof_classes`.
- Enforces evidence/environment/attestation requirements, including permitted digest algorithm (`sha256`/`sha512`).
- Toolchain proof: when canonical policy `environment.toolchain_digest_required` is `true`, requires schema-supported toolchain evidence in `logs_and_digests` or `artifacts_and_digests` using the documented `"toolchain"` reference convention and matching the policy environment digest algorithm. Missing evidence or algorithm mismatch fails closed.
- Evidence path safety: when `evidence_base_path` is supplied, every evidence reference must be a safe relative path contained under `evidence_base_path`. Absolute paths, `..` traversal, symlinks, non-regular files, missing files, or content digest mismatches return `eligible=False`.
- Cryptographic attestation verification: executes `verify_receipt_attestation(receipt, policy)` when `attestation.required` is True or an attestation envelope is present.
- **Returns**: `(eligible, reason_if_blocked)`

## Policy Verifier Key Records

Under GRO-4209, `approved_verifiers.identities` is migrated from string identifiers to strict `verifier_key_record` objects:

```json
{
  "id": "verifier-1",
  "key_id": "verification-key-1",
  "algorithm": "ed25519",
  "public_key_pem": "-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEA...\n-----END PUBLIC KEY-----",
  "created_at": "2026-01-01T00:00:00Z",
  "expires_at": "2026-12-31T23:59:59Z",
  "revoked_at": null,
  "supersedes_key_id": null
}
```

- Multiple key records may share the same verifier `id` to enable zero-downtime key rotation.
- Pre-rotation receipts signed by older active keys remain valid for receipts completed before the old key's expiration.

## Explicit Boundaries & Scope

- **Revocation Store**: `revocation_store=None` remains allowed by contract. When a store path is explicitly supplied, missing store files, symlinks, or malformed contents fail closed.
- **Evidence Base Path**: File-content digest comparison against disk is conditional on a supplied `evidence_base_path`.
- **Cryptographic Attestations**: Ed25519 receipt-attestation verification, canonicalization, verifier key records, and rotation enforcement are fully implemented in GRO-4209.
- **Unproven Dimensions**: Working-directory constraints and network-isolation proof currently lack receipt schema fields; they are documented as unproven payload dimensions rather than fabricating non-contractual validation.

## Usage Example

```python
from pathlib import Path
from prismatic.verification import (
    validate_receipt_freshness,
    check_revocation,
    determine_merge_eligibility,
    canonicalize_receipt,
    verify_receipt_attestation,
)

# 1. Check Freshness
is_fresh, freshness_reason = validate_receipt_freshness(receipt_data, max_age_seconds=1800)
if not is_fresh:
    print(f"Stale receipt: {freshness_reason}")

# 2. Check Ed25519 Attestation
att_valid, att_reason = verify_receipt_attestation(receipt_data, policy_data)
if not att_valid:
    print(f"Attestation invalid: {att_reason}")

# 3. Determine Merge Eligibility
store_path = Path("/etc/prismatic/revocations.json")
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
