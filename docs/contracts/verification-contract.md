# Verification Contract

**Status:** Canonical
**Owner:** Prismatic Engine maintainers
**Last verified:** 2026-07-23

## Required task guidance

A promotion-eligible task declares exact input revision, allowed scope, functional and safety invariants, required verifier set, proof classes, risk/reversibility, timeout/retry/idempotency, rollback/compensation, and escalation conditions.

## Proof-carrying result

A result bundle contains:

```text
task_id
policy_id_and_version
producer_identity
verifier_identity
backend_identity
source_repository
source_provider_and_locator
source_revision
base_revision
tree_revision
clean_checkout_identity
source_acquisition_digest
artifact_digests
changed_paths
commands_executed
command_exit_states
command_environment_digest
verifier_versions
functional_results
security_results
runtime_or_rendered_observations
policy_decisions
unverified_claims
residual_risk
evidence_uris_and_digests
started_completed_and_expiry_times
supersession_and_revocation_state
rollback_plan
signature_or_attestation
```

## Acceptance rules

- The producer cannot self-authorize merge.
- Each decision binds to an exact full SHA/artifact digest.
- A revision change invalidates prior approval unless explicitly replayed.
- Targeted checks, approved clean-room policy execution, installed artifact, production, browser, media, and business proof are reported separately.
- Evidence reports must state scope and non-claims.
- Secret-shaped values fail closed and are never echoed into evidence.
- Irreversible/high-blast-radius actions require stronger thresholds and human authorization.
- At least one approved backend must produce an independently validated clean-room receipt; no particular Git provider is mandatory.
- Provider checks/statuses are projections and evidence links, not receipt authority.
- Head/tree changes, expiry, revocation, supersession, incomplete commands, digest mismatch, unapproved backend, or producer/verifier identity collision invalidate merge eligibility.

## Backend and adapter conformance

All hosted, self-hosted, local, and offline backends emit the same versioned receipt shape and pass the same conformance fixtures. Adapters may add provider metadata but may not omit or reinterpret required core fields. A provider outage is reported as `BLOCKED_BACKEND_NO_EXECUTION`; it is not `PASS` or a product-test `FAIL`.

The accepted policy and migration boundary are defined by [ADR-0002](../decisions/ADR-0002-provider-neutral-verification-receipts.md).

## Compact proof packet

```text
COMMAND=<exact command or grouped summary>
RESULT=<PASS|FAIL|BLOCKED>
LOG=<immutable or digest-bound location>
SCOPE=<what was actually examined>
AD_HOC_OR_CANONICAL=<proof class>
NOT_CLAIMING=<explicit boundaries>
REVISION=<full SHA or artifact digest>
MARKER=<stable marker>
```
