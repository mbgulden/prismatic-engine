# ADR-0001: Documentation Source of Truth

- **Status:** Accepted
- **Date:** 2026-07-21
- **Owner:** Prismatic Engine maintainers

## Context

Prismatic accumulated overlapping architecture generations, reports, schemas, external OKF references, and dashboard claims without an explicit precedence or supersession model.

## Decision

Adopt the authority order in `docs/governance/source-of-truth-order.md`; maintain `docs/index.md` as canonical human index and `okf/index.yaml` as machine-readable objective/evidence registry. Dashboard and Telegram are authoritative operator views over durable stores, not underlying systems of record. Historical reports remain evidence and are not silently rewritten.

## Consequences

CI validates canonical paths and OKF references. Conflicts fail closed. Existing documents are progressively marked canonical, transitional, historical, or superseded. Hardcoded external workstation OKF paths cannot be normative.
