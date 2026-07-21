# Source-of-Truth Order

**Status:** Canonical
**Owner:** Prismatic Engine maintainers
**Last verified:** 2026-07-21

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
- GitHub PR/check records: public review and CI state.
- Merge Factory stores: admission, leases, locks, and judge attestations.
- Retained completed-work manifests: source packet and proof identity.
- Plugin/job/artifact/audit stores: governed product lifecycle.
- Linear: task coordination, not proof that implementation merged or deployed.

## Conflict handling

Conflicting claims produce `BLOCKED` until authority, revision, and evidence are reconciled. Newer is not automatically more authoritative; accepted status and exact revision control precedence.
