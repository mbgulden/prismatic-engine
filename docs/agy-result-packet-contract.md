# AGY result packet → completed-work normalization

AGY may emit either of these packet dialects:

1. **Completed-work gate dialect** — already includes `source_branch`, `source_path`, `base_branch`, object-shaped `lane_scope`, `changed_files`, and `proof`.
2. **AGY result packet dialect** — may use `branch`, `result_artifacts`, `merge_lane`, `verification`, `non_claims`, and `marker`.

`prismatic.agy_completed_work.normalize_agy_result_packet()` adapts the AGY result packet dialect before completed-work gate classification.

## Safe derived fields

- `source_branch`: `source_branch` or `branch`.
- `source_path`: first safe absolute `/home/ubuntu/...` result artifact, explicit safe `source_path`, or a controlled provenance fallback `/home/ubuntu/.prismatic/agy-result-packets/<issue_identifier>` when both issue and branch exist.
- `base_branch`: explicit `base_branch` or `main`.
- `lane_scope`: object scope derived conservatively from `merge_lane` / `verification_lane` and `changed_files`.
- `proof`: derived from `verification` plus top-level `non_claims` when needed.

## Rejection remains strict

Normalization does **not** hide unsafe provenance. Traversal, secret/config/token paths, generated/vendor paths, or packets with no derivable issue/branch/artifact provenance remain rejected by the completed-work gate.

Normalization marker:

```text
AGY_RESULT_PACKET_NORMALIZED_OK
```
