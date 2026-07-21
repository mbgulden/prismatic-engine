# Verification Engine Architecture

**Status:** Canonical
**Owner:** Prismatic Engine maintainers
**Last verified:** 2026-07-21

## Why

AI generation is probabilistic and increasingly prolific. Prismatic exists to turn proposals into durable, auditable outcomes without confusing plausible output with verified completion. The governing principle is **Don’t trust, Verify**.

## Model: Guide → Produce → Verify → Solve

### Guide

Before execution, bind the task to repository revision, allowed paths/tools/networks, invariants, risk class, required proof classes, budget, rollback, and escalation triggers. Guidance is pre-emptive verification.

### Produce

The producer operates in an isolated worktree/sandbox. Its result, self-review, screenshots, logs, and `DONE` marker are claims—not acceptance.

### Verify

Use a graph of diverse checks:

1. schema and scope;
2. deterministic lint/type/static/security;
3. unit/integration/property/adversarial tests;
4. build and installed-artifact behavior;
5. semantic, architectural, visual, media, or business-invariant review;
6. canonical CI;
7. exact-SHA merge-judge attestation;
8. post-merge and, when authorized, production proof.

### Solve

Repair findings without weakening the gate. Bind regression evidence to the corrected revision, preserve the failed attempt, update the knowledge/decision record, and re-run invalidated checks.

## Nested loops

- **Agent loop:** fast bounded feedback and self-repair.
- **Promotion loop:** independent review, canonical CI, artifact installation, merge authorization.
- **Maintenance loop:** runtime canaries, drift/debt scans, seeded-fault calibration, rollback and restore drills.

## Agent-level state flow

`guided → admitted → leased → producing → proposed → independently_verified → CI_green → merge_authorized → merged → post_merge_verified`

No transition may be inferred from Linear `Done`, producer `DONE`, or file presence alone.

## Systemic invariants

- admission and lease claims are atomic;
- stale holders cannot mutate or renew without the exact fence token;
- current active leases never exceed the stage cap;
- reviewed SHA equals PR head at merge time;
- evidence is immutable or digest-bound and revision-specific;
- mutable development checkouts are not production authority;
- failure, timeout, replay, and rollback have durable dispositions;
- dashboard and chat remain read/control surfaces over durable stores;
- verifier health is calibrated with planted known faults.

## Sustainable-throughput measures

Prefer verified completion rate, escaped defects, rollback rate, rework, mean time to evidence, verification cost per accepted outcome, reproducibility, maintenance burden, and recovery time over generated lines or raw task counts.
