# Prismatic Engine Documentation Control Plane

**Status:** Canonical index
**Owner:** Prismatic Engine maintainers
**Governing principle:** **Don’t trust, Verify.**

This index separates current normative truth from read models and historical evidence.

## Precedence

1. Executable schema and runtime invariants.
2. Accepted architecture decision records.
3. Canonical contracts and architecture documents indexed here.
4. Machine-readable `okf/index.yaml`.
5. Generated human/dashboard views.
6. Dated reports and research, which are evidence—not current authority.

When sources disagree, fail closed and open a decision/update rather than choosing silently.

## Canonical map

- [Documentation policy](governance/documentation-policy.md)
- [Source-of-truth order](governance/source-of-truth-order.md)
- [Glossary](governance/glossary.md)
- [North Star](north-star.md)
- [OKF evidence map](okf-evidence-map.md)
- [Verification Engine architecture](architecture/verification-engine.md)
- [Verification contract](contracts/verification-contract.md)
- [Evidence retention](contracts/evidence-retention.md)
- [Decision index](decisions/index.md)
- [ADR-0002: Provider-neutral verification receipts](decisions/ADR-0002-provider-neutral-verification-receipts.md)
- [Verifiers Are King evidence review](research/verifiers-are-king-evidence-review.md)
- Machine-readable OKF registry: `okf/index.yaml`

## Required update rule

A change affecting a governed component must update its canonical contract/OKF entry or state why documentation is unaffected. Producer completion, screenshots, test summaries, and `DONE` markers are never authority by themselves.
