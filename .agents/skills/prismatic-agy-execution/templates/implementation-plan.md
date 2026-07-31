# Prismatic AGY Implementation Plan

## Goal

`${GOAL}`

## Current-state evidence

- `${DISCOVERY_EVIDENCE}`

## Assumptions to verify

- [ ] `${ASSUMPTION}`

## Preservation boundaries

- `${PRESERVE_BOUNDARY}`

## Planned slices

1. `${STEP}`

For each slice record:

- exact files or subsystem;
- expected behavioral change;
- focused verification;
- rollback or repair path;
- whether external authorization is required.

## Risks and mitigations

| Risk | Mitigation | Proof |
|---|---|---|
| `${RISK}` | `${MITIGATION}` | `${PROOF}` |

## Verification ladder

1. Syntax/static checks.
2. Focused behavioral tests.
3. Installed-distribution or clean-room proof when packaging changes.
4. Canonical suite under the repository-defined boundary.
5. Independent exact-artifact review.
6. Browser/production proof only when explicitly in scope and authorized.

## Stop conditions

Stop and report `BLOCKED` rather than guessing if required context, credentials, admission, or authority is missing.
