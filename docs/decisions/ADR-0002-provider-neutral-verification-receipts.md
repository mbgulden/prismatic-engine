# ADR-0002: Provider-neutral verification receipts are the merge-evidence authority

**Status:** Accepted
**Owner:** Prismatic Engine maintainers
**Decision date:** 2026-07-23
**Supersedes:** The implicit operational assumption that GitHub Actions must be the unique canonical CI witness
**Related Linear epic:** GRO-4203

## Context

Prismatic Engine must operate with GitHub, Bitbucket, GitLab, Forgejo/Gitea, local bare repositories, self-hosted Git, and offline Git bundles. A merge policy that defines verification as “GitHub Actions is green” confuses one execution/status provider with the acceptance contract.

GitHub Actions can also become unavailable for billing, quota, control-plane, or plan reasons before any product command executes. That is not evidence that a candidate failed, and it must not force Prismatic either to stop all verified work or to bypass independent verification.

Producer logs, mutable worktrees, status icons, comments, and task-manager state remain claims/read models. The merge judge needs exact-artifact evidence that is portable across providers.

## Decision

Prismatic merge promotion requires an **independently verified, exact-head, clean-room verification receipt from at least one approved backend**.

GitHub Actions is an approved hosted verifier backend when available. It is not a source adapter, it is not mandatory, and it does not define verification semantics.

The provider-neutral core owns:

1. policy version and required proof classes;
2. repository identity and source acquisition;
3. base, candidate commit, and tree binding;
4. disposable clean checkout or verified Git-bundle isolation;
5. command execution and complete exit-state capture;
6. environment/toolchain digest;
7. bounded logs and artifact digests;
8. verifier/backend identity and approval status;
9. freshness, expiry, supersession, and revocation;
10. producer/verifier separation;
11. deterministic fail-closed receipt validation;
12. merge-judge decision input.

Source adapters own only provider/source-specific transport:

- trigger/webhook/poll ingestion;
- commit/ref metadata translation;
- source retrieval handoff;
- Check/build-status/comment projection;
- links back to the durable receipt.

Adapters cannot weaken the core policy or turn a provider status into canonical truth.

## Normative policy

```text
A merge is eligible only when:
- the current candidate commit and tree exactly match a valid receipt;
- the receipt was produced in an approved clean-room backend;
- every policy-required command and proof class completed successfully;
- logs and artifacts match their recorded digests;
- verifier identity is approved and independent from the producer;
- the receipt is fresh, unrevoked, and not superseded;
- the merge judge independently validates the receipt at decision time.
```

A head change invalidates the receipt. Missing, malformed, unsigned when signing is required, stale, producer-only, mutable-worktree-only, or provider-status-only evidence fails closed.

## Approved backend model

Initial backend classes:

- hosted provider runner, including GitHub Actions;
- Prismatic self-hosted clean-room verifier;
- supervised independent clean-room verifier for emergency migration, explicitly identified and policy-approved.

Future source adapters include Bitbucket, GitLab, Forgejo/Gitea, local bare repositories, and offline Git bundles. They bind source identity and acquisition only. Approval to emit a receipt applies separately to verifier backend identity and policy version, not merely to a provider or source-adapter name.

## Receipt minimum fields

```text
schema_version
policy_id
policy_version
task_id
repository_id
source_kind
source_provider
source_locator
base_sha
candidate_sha
tree_sha
changed_paths
clean_checkout_id
source_acquisition_digest
environment_digest
commands_and_exit_states
proof_classes
logs_and_digests
artifacts_and_digests
verifier_id
backend_id
backend_class
producer_id
started_at
completed_at
expires_at
supersedes
revocation_status
decision
non_claims
signature_or_attestation
```

## Migration and current GitHub outage

This decision does not retroactively declare existing local proof equivalent to the new gate. The self-hosted backend, receipt schema, validator, and conformance tests must exist and pass before they replace a hosted witness for a merge.

PR #383 is the first migration canary. Its existing exact-head evidence may inform the clean-room rerun, but merge eligibility requires a new receipt produced and validated under this policy. GitHub billing failure must remain visible as provider-control-plane evidence and must not be relabeled as product success or failure.

## Consequences

### Positive

- verification remains portable across Git providers and offline workflows;
- provider billing/control-plane outages no longer define product truth;
- exact evidence can be validated independently of status APIs;
- adapters become thinner and easier to test through one conformance suite;
- self-hosted and air-gapped operation become first-class.

### Costs and risks

- Prismatic must operate trust roots, verifier identities, revocation, and receipt retention;
- clean-room execution needs resource limits and deterministic bootstrap rules;
- provider adapters still need status-projection and webhook security tests;
- replacing GitHub-specific policy before conformance proof would weaken governance.

## Rejected alternatives

1. **Ignore failed GitHub checks and merge from local tests.** Rejected: bypasses the current independent-witness contract.
2. **Make the repository public solely to obtain branch protection.** Rejected: changes confidentiality to solve a plan limitation.
3. **Require GitHub Actions forever.** Rejected: conflicts with provider-neutral and self-hosted product goals.
4. **Trust any signed producer receipt.** Rejected: signing does not provide producer/verifier independence.
5. **Use provider status as the receipt.** Rejected: statuses are read models with incomplete execution and provenance semantics.

## Implementation program

Linear epic GRO-4203 owns the build sequence. Child tasks GRO-4204 through GRO-4213 cover canonical documentation, schemas, clean-room acquisition, runner, validator/merge judge, identity/attestation, GitHub, Bitbucket/GitLab, Forgejo/Gitea/local adapters, conformance, rollback, and PR #383 migration proof.
