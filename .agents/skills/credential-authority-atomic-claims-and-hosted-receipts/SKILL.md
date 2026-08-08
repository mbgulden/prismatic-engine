---
name: credential-authority-atomic-claims-and-hosted-receipts
description: "Validated credential-derived Principal authority, atomic claim tokens, fail-closed CLI approval gates, and exact-head hosted CI receipt verification."
tags: [security, authority, credentials, claims, ci-receipts]
related_skills:
  - agy-tdd-discipline
  - agy-secure-coding
  - prismatic-full-feature-delivery-gate
---

# credential-authority-atomic-claims-and-hosted-receipts

## Purpose

Enforce that execution authority derives exclusively from validated credentials/attestations rather than unauthenticated actor strings; guarantee atomic claim token semantics for merge authorizations; and verify hosted CI check receipts against exact candidate head SHAs.

---

## Shared Anti-Stub Rule (Mandatory Invariant)

> A declaration, schema, route signature, UI selector, mock response, generated fixture, helper-level unit test, or happy-path-only implementation is not feature completion. Completion requires the real runtime composition path, durable state where required, authenticated operator path, failure/recovery behavior, and tests that exercise the public decision path from an immutable exact candidate.

---

## Trigger

Load for operator CLI authorization, Principal identity construction, authorization creation/claim/consumption, merge authority, or hosted CI receipt verification.

---

## Product Outcome

Authority is derived strictly from cryptographically validated credentials and tokens, never from unverified display strings (`auth.actor`). Exactly one executor can claim an authorization token, and hosted CI receipts are authenticated and bound to candidate head SHAs.

---

## Existing Authority & Preservation Boundary

Inspect `prismatic/review_factory/merge_executor.py`, `prismatic/review_factory/routes.py`, `prismatic/gateway/control_auth.py`, and `prismatic/merge_candidate_manifest.py` before editing. Preserve fail-closed security invariants.

---

## Workflow Protocol

1. **Identity Separation**: Strictly separate display metadata (`actor`) from credential-derived Principal authority (`Principal(actor=..., roles=..., scopes=...)`).
2. **Fail-Closed Security**: Fail closed with `PermissionError` when required operator credentials or environment keys (`PRISMATIC_OPERATOR_KEY`) are absent, malformed, expired, revoked, or insufficiently scoped.
3. **No Privileged Scope Injection**: Never reconstruct privileged scopes from stored strings or synthesize judge/admin scopes inside the executor (`_cli_approve`).
4. **Transactional Authorization Creation**: Create authorization in one transaction that validates state, candidate SHA, and target base SHA, inserting exactly one active authorization.
5. **Atomic Claim Tokens**: Claim authorization atomically before side effects using a unique claim token and an affected-row check (`UPDATE ... WHERE status = 'ACTIVE' AND claim_token IS NULL`).
6. **Post-Lock Re-verification**: Re-verify candidate SHA, target base SHA, expiry timestamp, revocation state, and claim token after acquiring merge lock.
7. **Post-Merge Consumption**: Consume authorization token only after successful merge and result verification; check every state transition result explicitly.
8. **Hosted Receipt Verification**: Verify hosted CI receipts through a provider adapter bound to repository, workflow/check identity, run ID/attempt, exact candidate head SHA/tree, conclusion, freshness, and authenticated response digest.
9. **No Fabricated CI**: Reject fabricated CI records; treat missing provider access as blocked evidence, never as implicit success (`_build_ci_checks()`).

---

## Anti-Stub Gate

Block completion if:
- Token presence is checked without validating scope or provenance.
- Actor-name allowlists are used in place of cryptographically verified tokens.
- Security defaults off or is optional via environment flags.
- Merge authorizations are claimed via read-then-write loops instead of atomic atomic SQL updates.
- Synthetic CI records are generated from hardcoded success strings plus GitHub-looking URLs.

---

## Adversarial Test Requirements

- **Executor Concurrency Race**: Two concurrent executors attempt to claim the same authorization; exactly one succeeds in entering side effects.
- **Unset Operator Key Fail-Closed**: `PRISMATIC_OPERATOR_KEY` is missing or mismatched; CLI approval fails closed with `PermissionError`.
- **Mismatched Candidate CI Rejection**: CI check has a candidate SHA different from `job.candidate_commit`; manifest validation fails (`ManifestValidationError`).
- **Hosted Fixture Pass**: A valid exact-head hosted fixture passes through the exact same public decision path used in production.

---

## Required Proof Packet

```text
COMMAND=pytest prismatic/review_factory/tests/test_merge_executor.py -v
RESULT=<PASS|FAIL|BLOCKED>
LOG=<path to log>
SCOPE=credential-authority-atomic-claims-and-hosted-receipts
HEAD=<commit sha>
TREE=<tree sha>
FAIL_CLOSED_VERIFIED=true
ATOMIC_CLAIMS_VERIFIED=true
MARKER=PE_CREDENTIAL_AUTHORITY_OK
```
