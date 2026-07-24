# Provider-Neutral Receipt Validation (GRO-4208 / PNV-4)

## Overview

The Provider-Neutral Receipt Validation module (`prismatic.verification.receipt_validator`) provides an independent, fail-closed verification layer sitting between clean-room execution outputs and merge decisions.

It enforces five critical dimensions of receipt integrity:
1. **Freshness Validation**: Ensures receipts were generated within configurable time bounds and rejects future-dated timestamps outside clock-skew tolerance.
2. **Revocation Checking**: Verifies receipt IDs and evidence SHAs against atomic revocation stores.
3. **Merge Eligibility**: Combines freshness, revocation status, required evidence fields, and policy constraints into a conclusive merge decision.
4. **Evidence Binding**: Binds candidate, base, and tree SHAs against policy expectations and verifies SHA256/SHA512 evidence log digests against files on disk.
5. **Fail-Closed Semantics**: Rejects malformed timestamps, unknown revocation states, missing required fields, non-zero command exit codes, and invalid digest formats.

## Public API

```python
from prismatic.verification import (
    validate_receipt_freshness,
    check_revocation,
    determine_merge_eligibility,
)
```

### `validate_receipt_freshness(receipt: dict, *, max_age_seconds: int = 3600) -> tuple[bool, str | None]`

Evaluates the completion timestamp (`finished_at` or `completed_at`) of a receipt dict:
- **`max_age_seconds`**: Maximum allowable age in seconds (default: 3600s / 1 hour).
- **Clock Skew Tolerance**: Future timestamps are rejected unless within ≤60 seconds of current UTC time.
- **Returns**: `(is_fresh, reason_if_stale)`
  - Reasons include `"missing_timestamp"`, `"malformed_timestamp"`, `"timestamp_future"`, `"receipt_stale"`.

### `check_revocation(receipt: dict, *, revocation_store: Path | None = None) -> tuple[bool, str | None]`

Checks whether a receipt has been revoked:
- **`revocation_store`**: Optional `Path` to a JSON file containing revoked IDs, candidate SHAs, or digests.
- If no store path is provided or file is absent, defaults to `"no_revocations"`.
- Atomic read guarantees full file parsing.
- Checks top-level `revocation_status` (`"revoked"` → blocked).
- **Returns**: `(not_revoked, reason_if_revoked)`
  - Reasons include `"receipt_revoked_by_status"`, `"receipt_revoked_by_id_<id>"`, `"receipt_revoked_by_sha_<sha>"`.

### `determine_merge_eligibility(receipt: dict, policy: dict, *, revocation_store: Path | None = None, evidence_base_path: Path | None = None) -> tuple[bool, str | None]`

Synthesizes policy rules and receipt evidence into an authoritative merge decision:
- Checks schema integrity & fail-closed invariants.
- Validates git commit SHAs (`candidate_sha`, `base_sha`, `tree_sha`) and changed paths.
- Verifies zero exit code across all executed commands (`commands_and_exit_states`).
- Enforces freshness and revocation status.
- Enforces producer/verifier separation (`require_producer_verifier_separation`).
- Verifies required/approved `proof_class` values.
- Validates evidence digests and compares file content digests against disk references when `evidence_base_path` is specified.
- **Returns**: `(eligible, reason_if_blocked)`

## Contract & Invariants

- **Fail-Closed**: Any missing, malformed, or ambiguous field results in immediate rejection (`eligible=False`).
- **Zero Exit Code**: All commands in `commands_and_exit_states` must have `execution_state="executed"`, `exit_state="completed"`, and `exit_code=0`.
- **Producer / Verifier Separation**: When required by policy, `producer_id` and `verifier_id` must both be present and distinct.
- **Digest Validity**: All digests must follow standard `sha256:<hex>` or `sha512:<hex>` formats; all-zero digests are rejected.

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
