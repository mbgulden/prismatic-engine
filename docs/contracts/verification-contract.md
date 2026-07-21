# Verification Contract

**Status:** Canonical
**Owner:** Prismatic Engine maintainers
**Last verified:** 2026-07-21

## Required task guidance

A promotion-eligible task declares exact input revision, allowed scope, functional and safety invariants, required verifier set, proof classes, risk/reversibility, timeout/retry/idempotency, rollback/compensation, and escalation conditions.

## Proof-carrying result

A result bundle contains:

```text
task_id
producer_identity
source_repository
source_revision
base_revision
artifact_digests
changed_paths
commands_executed
command_environment_digest
verifier_versions
functional_results
security_results
runtime_or_rendered_observations
policy_decisions
unverified_claims
residual_risk
evidence_uris_and_digests
rollback_plan
```

## Acceptance rules

- The producer cannot self-authorize merge.
- Each decision binds to an exact full SHA/artifact digest.
- A revision change invalidates prior approval unless explicitly replayed.
- Targeted checks, canonical CI, installed artifact, production, browser, media, and business proof are reported separately.
- Evidence reports must state scope and non-claims.
- Secret-shaped values fail closed and are never echoed into evidence.
- Irreversible/high-blast-radius actions require stronger thresholds and human authorization.

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
