# GRO-3475 — Execution Proof & State Sync Audit

This branch adds the Phase 4 proof gate as a side-effect-free engine module.

## Why this exists

Dispatch is not enough. The workflow contract needs a deterministic check that
an issue moved through real worker execution and produced the expected durable
state sync:

1. worker/session launch identity exists;
2. worker start and completion timestamps exist;
3. worker result artifact exists on disk;
4. worker completed successfully;
5. Linear final evidence comment exists;
6. Linear final comment links issue and artifact evidence;
7. Linear state or completion label reflects terminal work.

## Implementation

- `prismatic/execution_proof.py`
  - `WorkerRunEvidence` accepts common dispatcher/supervisor run-record aliases.
  - `LinearSyncEvidence` captures final state, labels, and comment evidence.
  - `assess_execution_proof(...)` returns a structured `ExecutionProofReport` with
    pass/warn/fail status, evidence bullets, and trace fields.
- `prismatic/test_execution_proof.py`
  - Covers full pass, missing artifact failure, identity mismatch failure,
    non-terminal Linear state warning, mapping adapters, and weak comment-body
    evidence warning.

## Verification

Focused command:

```bash
python3 -m pytest prismatic/test_execution_proof.py -v --tb=short
```

This is the canonical focused check for the Phase 4 proof gate. Full-suite green
is intentionally not claimed by this document.
