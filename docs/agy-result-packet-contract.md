# AGY raw result-packet contract and completed-work normalization

Marker: `AGY_RAW_RESULT_PACKET_CURRENT_MAIN_OK`

AGY has two supported packet layers. They are deliberately not the same
contract.

1. Canonical raw AGY-result dialect: AGY-authored task result JSON with
   `agent=agy`, `branch`, `result_artifacts`, `verification`, `merge_lane`,
   `risk_level`, `next_action`, and marker `AGY_TASK_RESULT_PACKET_OK`.
2. Completed-work gate dialect: already-normalized Fred/Jules/AGY packets with
   `source_branch`, `source_path`, `proof`, object-shaped `lane_scope`, and gate
   metadata.

Strict raw validation only runs for the canonical raw AGY-result dialect, before
normalization, completed-work gate classification, durable evidence retention, or
SQLite upsert. Existing normalized Fred/Jules/completed-work packets remain
compatible and are not forced through the AGY-only schema.

## Canonical raw AGY fields

Required:

- `agent`: exactly `agy`.
- `issue_identifier`: `GRO-*` task identifier.
- `branch`: must be `feature/*`, matching current completed-work gate policy.
- `base_branch`: `main` or `origin/main`.
- `changed_files`: non-empty unique repo-relative paths.
- `result_artifacts`: non-empty unique safe provenance artifact paths. Items may
  be strings or object-shaped `{ "path": "..." }` entries; object-shaped entries
  accept exactly the `path` key and reject any nested extras. Arbitrary
  `/tmp/...` paths are not accepted as raw source provenance.
- `verification`: object with non-empty `commands`, `result`, `log_path`, and
  `ad_hoc_or_canonical`.
- `non_claims`: explicit negative claims. These are scanned for secrets/control
  characters, but deployment words inside `non_claims` are not treated as
  positive production claims.
- `merge_lane`: one of `dashboard-ui`, `backend-api`, `docs`, `research`,
  `mixed`, `manual-review`.
- `risk_level`: `low`, `medium`, or `high`.
- `next_action`: `merge-ready`, `needs-fred-cleanup`, `needs-human-review`,
  `blocked`, or `superseded`.
- `marker`: exactly `AGY_TASK_RESULT_PACKET_OK`.

Optional:

- `pr_url`: omitted, `null`, or a real GitHub pull-request URL. A meaningless
  `null` is not required for compatibility.
- `source_commit_sha` / `base_commit_sha`: optional 40-character commit SHAs,
  retained when provided for durable evidence/promotion decisions.

Python validation and `schemas/agy-result-packet.schema.json` both reject unknown
raw properties (`additionalProperties=false`).

## Safety decisions

- Unsafe, malformed, traversal, generated/vendor, credential-path, or
  secret-bearing raw packets raise before normalization. They create no durable
  evidence directory and no SQLite completed-work row.
- `next_action=blocked` must carry `verification.result=FAIL` or `BLOCKED`, so it
  cannot normalize into merge-ready.
- `risk_level=high` cannot be merge-ready. High-risk/manual-review packets remain
  non-merge-ready through normalization and gate classification.
- `non_claims` are negative claims, not production proof.
- Raw provenance follows the current safe-path policy. A raw artifact under an
  arbitrary `/tmp` location is treated as unsafe provenance; controlled operator
  home paths such as `/home/ubuntu/.prismatic/...` are allowed.

## Normalization

`prismatic.agy_completed_work.normalize_agy_result_packet()` adapts accepted AGY
result packets for the completed-work gate:

- `source_branch`: `source_branch` or `branch`.
- `source_path`: explicit safe `source_path`, first safe absolute operator-home
  result artifact, or a controlled fallback
  `/home/ubuntu/.prismatic/agy-result-packets/<issue_identifier>` when issue and
  branch exist and no unsafe artifact was supplied.
- `base_branch`: explicit `base_branch` or `main`.
- `lane_scope`: conservative object scope from `merge_lane` /
  `verification_lane` and `changed_files`.
- `proof`: derived from `verification` and top-level `non_claims` when needed.

Normalization marker:

```text
AGY_RESULT_PACKET_NORMALIZED_OK
```

This document intentionally does not port the obsolete JSONL-first ingestion
design as current architecture.
