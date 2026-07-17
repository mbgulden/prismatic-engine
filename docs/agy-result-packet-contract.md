# AGY Result Packet Contract

Marker: `AGY_RESULT_PACKET_SCHEMA_OK`

This contract turns raw AGY output into a typed, reviewable handoff packet before any merge or dashboard writeback path is allowed to treat the work as useful.

## Boundary

AGY may produce prose, logs, branches, and artifacts. Prismatic only ingests a completed AGY task when a result packet validates against this contract.

This packet is **not** approval to merge. It is the input to the completed-work ingestion bridge, merge-readiness classifier, PR helper, verification gate, and dashboard operator controls.

## Required packet fields

| Field | Type | Requirement |
|---|---:|---|
| `agent` | string | Must be `agy`. |
| `issue_identifier` | string | Linear-style identifier, e.g. `GRO-3837`. |
| `branch` | string | Non-empty source branch that AGY used or produced. |
| `base_branch` | string | Must be an allowed base; currently `main`. |
| `changed_files` | array[string] | Repo-relative paths; no absolute paths, traversal, virtualenv/vendor junk, or secret paths. |
| `pr_url` | string/null | GitHub PR URL if already opened, otherwise null. |
| `result_artifacts` | array[string] | Existing artifact path(s) or durable URI(s) produced by AGY. |
| `verification.commands` | array[string] | Exact commands run by AGY/Fred for this output. |
| `verification.result` | string | `PASS`, `FAIL`, or `BLOCKED`. |
| `verification.log_path` | string | Durable log path or URI. |
| `verification.ad_hoc_or_canonical` | string | `ad-hoc targeted` or `canonical suite`. |
| `non_claims` | array[string] | Boundaries explicitly not claimed. |
| `merge_lane` | string | `dashboard-ui`, `backend-api`, `docs`, `research`, `mixed`, or `manual-review`. |
| `risk_level` | string | `low`, `medium`, or `high`. |
| `next_action` | string | `merge-ready`, `needs-fred-cleanup`, `needs-human-review`, `blocked`, or `superseded`. |
| `marker` | string | Must be `AGY_TASK_RESULT_PACKET_OK`. |

## Validation rules

The Python validator in `prismatic/agy_result_packet.py` enforces the schema plus policy checks that plain JSON Schema cannot express well:

- AGY output must be from `agent: agy`.
- `issue_identifier` must match `GRO-####`.
- `base_branch` must currently be `main`.
- `changed_files` and `result_artifacts` may not contain absolute paths, `..`, control characters, virtualenv/cache/vendor junk, or secret-like paths.
- `verification.commands` must be non-empty.
- `verification.log_path` must be present and path-safe.
- Dashboard/UI lanes must not claim `merge-ready` with no changed files.
- Production deployment claims are blocked unless proof text references production/public/runtime proof.
- Secret-like content in packet strings is blocked.
- `marker` must be exactly `AGY_TASK_RESULT_PACKET_OK`.

## Ingestion status

The completed-work ingestion bridge is intentionally separate from the original task queue:

```text
AGY completed task
→ result packet validate
→ packet saved
→ artifacts indexed
→ branch / PR discovered
→ changed files listed
→ proof logs linked
→ completed-work dashboard item appears
→ Linear writeback accepted/rejected
```

The first implementation slice includes the result-packet schema/validator and a design stub for the durable completed-work ingestion bridge. It does **not** bulk-dispatch AGY, enable auto-merge, or mark overnight autopilot ready.

## Example minimal packet

```json
{
  "agent": "agy",
  "issue_identifier": "GRO-3837",
  "branch": "agy/GRO-3837-dashboard-canary",
  "base_branch": "main",
  "changed_files": ["docs/example.md"],
  "pr_url": null,
  "result_artifacts": ["/tmp/agy/GRO-3837/result.json"],
  "verification": {
    "commands": ["python3 -m pytest tests/test_example.py"],
    "result": "PASS",
    "log_path": "/tmp/fred-agy-autopilot-verify.log",
    "ad_hoc_or_canonical": "ad-hoc targeted"
  },
  "non_claims": ["canonical_full_suite_green", "auto_merge_enabled"],
  "merge_lane": "docs",
  "risk_level": "low",
  "next_action": "merge-ready",
  "marker": "AGY_TASK_RESULT_PACKET_OK"
}
```
