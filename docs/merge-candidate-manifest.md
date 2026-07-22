# Merge Candidate Manifest v1

`prismatic.merge_candidate_manifest` defines the pure, machine-readable candidate artifact used between deterministic verification and Prismatic's existing merge judge.

It is deliberately **not** another merge ledger or merge authority. It does not instantiate `MergeFactoryStore`, submit attestations, acquire locks, call GitHub, mutate Linear, merge, release, deploy, or write a file unless `write()` is explicitly called.

## Promotion states

The only forward path is:

```text
CANDIDATE
  → REVIEW_REQUIRED
  → CLEAN
  → CI_GREEN
  → MERGE_ELIGIBLE
  → MERGED
  → RELEASE_VERIFIED
```

Every transition returns a new frozen value. Direct construction and deserialization run the same semantic validator, so callers cannot manufacture an advanced state without its prerequisites.

## Identity and evidence bindings

A manifest binds:

- schema and proof-policy version;
- issue and task identity plus the exact `AGY_TASK.md` SHA-256;
- repository, target, base SHA, and candidate SHA;
- normalized, repository-relative changed paths;
- producer and preserved-candidate location;
- risk tier and dashboard flag;
- the exact required GitHub check names;
- deterministic proof records and log SHA-256 values;
- independent review identity, verdict, candidate SHA, and reviewed manifest digest;
- GitHub run identities, conclusions, URLs, and candidate SHA;
- explicit non-claims;
- merge SHA and release evidence when those states are reached.

Canonical JSON is UTF-8, sorted-key, compact JSON. `digest()` is SHA-256 over those canonical bytes. `evidence_digest()` is SHA-256 over the canonical evidence projection. Neither digest is caller-supplied or included in its own preimage.

At `MERGE_ELIGIBLE`, `factory_bindings()` returns the exact values expected by the existing `MergeFactoryStore` methods:

```text
issue_id
repository
target
base_sha
candidate_sha
manifest_digest
evidence_digest
```

`MergeFactoryStore.submit_attestation()`, `validate_approval()`, and merge locks remain authoritative for reviewer authorization, decision history, lock ownership, TTL, and concurrency.

## Proof matrix

| Risk | Required deterministic proof classes |
|---|---|
| A | `focused` |
| B | `focused`, `canonical`, `package` |
| C | Tier B plus `failure`, `recovery`, `rollback` |
| Any dashboard change | Tier requirements plus `browser`, `real_data` |

Proof entries accept only `PASS` and bind the command/summary, log location, and log SHA-256. Unknown or duplicate proof classes fail closed.

`required_ci_checks` is part of candidate policy. CI progression requires an exact set match: every required name appears once, every conclusion is `SUCCESS`, every run has a positive identity and GitHub URL, and every check binds the candidate SHA. Pending, neutral, skipped, stale, absent, duplicate, or extra checks do not satisfy the contract.

## Independent review

`CLEAN` requires:

- verdict exactly `CLEAN`;
- a reviewer identity distinct from the producer after canonical case-fold comparison;
- exact candidate SHA;
- exact digest of the `REVIEW_REQUIRED` manifest;
- affirmative scope-clean and conflict-free assertions.

The architecture review of this contract is not a substitute for exact-head code review.

## Candidate rebinding and stale evidence

A base, candidate-head, task-file digest, or changed-path change must use `rebind_candidate()`.

Rebinding:

- returns state `CANDIDATE`;
- records deterministic `bindings_changed:...` reason text;
- clears deterministic verification, independent review, CI, merge, and release evidence;
- changes the canonical manifest digest;
- prevents the old review or CI artifacts from promoting the new candidate.

A pure rebind does **not** revoke an approval already persisted in `MergeFactoryStore`. The orchestration layer must always re-read the live PR head before merge and should append `SUPERSEDED` for the old binding when a reviewed head is replaced. Manifest state must never override `validate_approval()` or exact live-head verification.

## Strict input behavior

The contract rejects:

- unknown or missing JSON fields, including nested fields;
- duplicate JSON object keys and non-finite JSON values;
- unknown schema versions, states, risk tiers, proof classes, or conclusions;
- bool-as-int and untrusted `str`/list/dict/model subclasses at trust boundaries;
- malformed or uppercase SHAs/digests;
- absolute, traversal, backslash, control-character, duplicate, or noncanonical paths;
- duplicate proof classes, required checks, CI check names, or release identities;
- mutable nested collections;
- premature or stale review, CI, merge, or release fields;
- reviewer/producer identity reuse;
- arbitrary transition skips or reversals.

## Example

```python
from prismatic.merge_candidate_manifest import (
    CICheck,
    IndependentReview,
    MergeCandidateManifest,
    RiskTier,
    VerificationEvidence,
)

candidate = MergeCandidateManifest.create(
    issue_id="GRO-5000",
    task_id="manifest-v1",
    task_file_sha256="a" * 64,
    repository="mbgulden/prismatic-engine",
    target="main",
    base_sha="1" * 40,
    candidate_sha="2" * 40,
    changed_paths=["prismatic/example.py", "tests/test_example.py"],
    producer="agent:producer",
    preserved_candidate_location="/archive/candidates/GRO-5000",
    risk_tier=RiskTier.B,
    dashboard_change=False,
    required_ci_checks=[
        "build package",
        "test py3.10",
        "test py3.11",
        "test py3.12",
        "test py3.13",
    ],
    non_claims=["no deploy", "no Linear mutation"],
)

requested = candidate.request_review(
    [
        VerificationEvidence(
            proof_class=name,
            command=f"verify {name}",
            summary=f"{name} passed",
            result="PASS",
            log_path=f"/tmp/{name}.log",
            log_sha256="b" * 64,
        )
        for name in ("focused", "canonical", "package")
    ]
)

review = IndependentReview(
    reviewer="agent:independent-reviewer",
    review_id="review-5000",
    verdict="CLEAN",
    reviewed_sha=requested.candidate_sha,
    reviewed_manifest_digest=requested.digest(),
    scope_clean=True,
    conflict_free=True,
)
clean = requested.record_review(review)

# Build CICheck values from the refreshed GitHub checks for the exact head.
# green = clean.record_ci(checks)
# eligible = green.mark_merge_eligible()
# store.submit_attestation(**eligible.factory_bindings(), decision=..., principal=...)
```

## Explicit persistence

`write(path)` and `read(path)` only accept a file named `merge_candidate.json`. Writes use a same-directory temporary file, flush and fsync it, atomically replace the target, fsync the directory, and remove temporary files on failure. Symlink targets are rejected.

No path is selected and no filesystem write occurs at import, construction, validation, serialization, digesting, or transition time.

## Non-claims

This contract does not authorize or perform:

- automatic merge or lock acquisition;
- GitHub PR/review/check mutation;
- Linear state or label mutation;
- producer dispatch;
- deployment, restart, release promotion, or cap increase;
- revocation of old stored approvals;
- replacement of live-head, GitHub CI, or `MergeFactoryStore.validate_approval()` checks.
