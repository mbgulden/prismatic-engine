---
name: immutable-candidate-materialization-and-provenance
description: "Verification execution over immutable materializations of exact candidate commits, preventing worktree mutation leakage and computing content-addressed provenance."
tags: [verification, immutable, archive, provenance, materialization]
related_skills:
  - agy-tdd-discipline
  - prismatic-full-feature-delivery-gate
---

# immutable-candidate-materialization-and-provenance

## Purpose

Ensure verification checks run strictly against read-only, immutable materializations of the exact candidate commit and compute content-addressed provenance records bound to repository identity, commit, tree, command manifest, exit codes, and log hashes.

---

## Shared Anti-Stub Rule (Mandatory Invariant)

> A declaration, schema, route signature, UI selector, mock response, generated fixture, helper-level unit test, or happy-path-only implementation is not feature completion. Completion requires the real runtime composition path, durable state where required, authenticated operator path, failure/recovery behavior, and tests that exercise the public decision path from an immutable exact candidate.

---

## Trigger

Load whenever verification executes commands against a candidate repository, commit SHA, git tree, archive, worktree, or verification result packet.

---

## Product Outcome

Verification runs only against immutable bytes independently resolved from the exact candidate commit, and produces reconstructable provenance bound to repository, commit, tree, commands, exits, and log digests.

---

## Existing Authority & Preservation Boundary

Inspect `prismatic/review_factory/verifier.py` and `prismatic/verifiers/registry.py` before editing. Never execute verifier commands directly inside the caller's mutable source repository directory (`cwd=self.repo_path`).

---

## Workflow Protocol

1. **Commit Validation**: Accept repository identity and full 40-character hex commit SHA; reject branch names (e.g. "main"), short SHAs, or relative refs.
2. **Tree Resolution**: Independently resolve the commit and compute `HEAD^{tree}` via `git rev-parse candidate_commit^{tree}`.
3. **Tree Matching**: Compare resolved tree to queued candidate tree before execution; reject mismatches immediately.
4. **Immutable Materialization**: Materialize a disposable, isolated archive or detached checkout (`_materialize_immutable_archive()`) from the exact commit SHA into a temporary directory outside the producer worktree.
5. **Isolated Execution**: Execute all test and verifier commands with `cwd=archive_path`; never use caller-supplied mutable `repo_path` as proof authority.
6. **Command Manifest**: Build a canonical command manifest before execution detailing all sub-commands, flags, and environment variables.
7. **Content-Addressed Provenance**: Compute a content-addressed provenance record over repository identity, commit, tree, command manifest, environment policy, exit codes, artifact hashes, and log hashes.
8. **Durable Metadata**: Persist immutable metadata sufficient to reproduce and audit candidate identity later.
9. **Clean Disposal**: Clean up disposable reviewer-created resources upon completion; never normalize or mutate the shared producer worktree.

---

## Anti-Stub Gate

Block completion if:
- A typed label such as `archive-tree-<input>` is accepted without resolving actual git tree.
- Provenance digest is computed over unverified user assertions instead of actual execution outputs.
- A materialization helper function exists in code but `VerificationWorker.verify()` continues running with `cwd=self.repo_path`.
- Importer accepts mutable branch names (e.g. "main", "deploy-fresh") instead of valid hex SHAs.

---

## Adversarial Test Requirements

- **Worktree Mutation Immunity**: Mutate the source worktree during active verification; executed bytes and verification receipt identity remain unchanged.
- **Commit/Tree Mismatch Gate**: Queue a job with mismatched commit and tree SHAs; verification halts immediately without running commands.
- **Branch Name Rejection**: Supply a branch name or short SHA to backlog importer; enqueue fails closed.
- **Provenance Reproducibility**: Recompute the provenance record from stored evidence and obtain the exact same hash.

---

## Required Proof Packet

```text
COMMAND=pytest prismatic/review_factory/tests/test_verification_worker.py -v
RESULT=<PASS|FAIL|BLOCKED>
LOG=<path to log>
SCOPE=immutable-candidate-materialization-and-provenance
HEAD=<commit sha>
TREE=<tree sha>
IMMUTABLE_ARCHIVE_MATERIALIZED=true
MUTATION_IMMUNITY_VERIFIED=true
MARKER=PE_IMMUTABLE_PROVENANCE_OK
```
