# Source-of-Truth Order

**Status:** Canonical
**Owner:** Prismatic Engine maintainers
**Last verified:** 2026-07-23

## Authority order

| Rank | Source | Role |
|---:|---|---|
| 1 | Runtime invariants and versioned schemas | Executable acceptance boundary |
| 2 | Accepted ADRs | Why an architecture/policy is binding |
| 3 | Canonical contracts and architecture docs | Human-operable normative explanation |
| 4 | `okf/index.yaml` | Objective → measurable result → function → evidence registry |
| 5 | API/dashboard/Telegram views | Read models and controls over durable stores |
| 6 | Reports, transcripts, screenshots, agent packets | Evidence requiring revision and provenance checks |

## Durable systems of record

- Git commit and artifact digest: code/artifact identity.
- Provider-neutral verification receipts: exact clean-room execution, evidence, verifier identity, and decision binding.
- GitHub/Bitbucket/GitLab/Forgejo/Gitea check or build-status records: provider read models that link to receipts; never the receipt authority by themselves.
- Merge Factory stores: admission, leases, locks, and judge attestations.
- Retained completed-work manifests: source packet and proof identity.
- Plugin/job/artifact/audit stores: governed product lifecycle.
- Linear: task coordination, not proof that implementation merged or deployed.

## Conflict handling

Conflicting claims produce `BLOCKED` until authority, revision, and evidence are reconciled. Newer is not automatically more authoritative; accepted status and exact revision control precedence.

## CI/provider boundary

The canonical merge evidence is a valid receipt under the accepted verification policy, not a specific vendor status icon. A provider control-plane or billing failure means that backend did not execute; it is neither product proof nor product failure. At least one approved independent clean-room backend must execute and issue a valid exact-head receipt before merge eligibility. See [ADR-0002](../decisions/ADR-0002-provider-neutral-verification-receipts.md).
