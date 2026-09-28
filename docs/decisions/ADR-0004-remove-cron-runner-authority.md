# ADR-0004: Remove the cron_runner / cron_authority scheduler and the swarmcron plugin

**Status:** Accepted
**Owner:** Prismatic Engine orchestration maintainers
**Date:** 2026-09-28
**Deciders:** Michael Gulden (DECISION-1 + DECISION-2, cron-overhaul-plan.md)
**Supersedes:** `docs/contracts/cron-runtime-authority-v1.md` (deleted by this change)

## Context

Under GRO-4317 (#412), Prismatic built a fully-tested cron execution authority:
`prismatic/cron_runner.py` (1,814 lines — transactional run workflow, idempotent
trigger envelopes, execution leases, catch-up buckets, dependency DAGs, signed
receipts) and `prismatic/cron_authority.py` (3,155 lines — SQLite authority store,
schema v3, adversarially hardened), plus 5,643 lines of tests and two contract
docs. The contract itself framed the authority as "**Proposed** future authority
only after immutable regeneration" — and the future never came.

A fresh audit of `origin/main` (2026-09-27) proved the system has **zero
production path**: no CLI, no routes, no sweep driver/ticker calls the due-bucket
functions, nothing implements the `BoundedProcessAdapter` protocol (the module
docstring states "zero subprocess/Popen/fork sites"), and the only importers of
either module are each other and their tests. Wiring it as the real scheduler
would cost an estimated 3–6 weeks plus a release-pinned deployment-model change
(`_RELEASE_ROOT_PATTERN` requires jobs to run from
`/home/ubuntu/.prismatic/releases/<sha40>/`) that nothing else in the codebase
uses — with real risk of ending with two schedulers and a migration nobody
finishes. Meanwhile every real cron pain (last-run never recorded, pause doesn't
stop, no create path, manual dashboard refresh) lives in the native cron
registry and is fixable in days.

Separately, `prismatic/shipped_plugins/cron/` (the dormant swarmcron plugin) is
disabled by default and its dependency `swarmcron>=0.3.0` 404s on PyPI — it
cannot even install. The native registry plus the Schedule Observatory
(`prismatic/schedules.py`) cover its use cases.

## Decision

Delete, per Michael's DECISION-1/DECISION-2:

- `prismatic/cron_runner.py`, `prismatic/cron_authority.py`
- `tests/test_cron_runner.py`, `tests/test_cron_authority.py`,
  `tests/test_plugin_cron.py`
- `prismatic/shipped_plugins/cron/` (README, manifest, plugin)
- `docs/contracts/cron-runtime-authority-v1.md`,
  `docs/contracts/cron-trigger-outcome-v1.md`
- Dangling references updated: `tests/quarantine.yaml` (3 stale plugin entries),
  `docs/infrastructure-capabilities.md` (reference-implementation note),
  `docs/single-node-boundaries.md` (fencing note, past tense)

**Kept deliberately:** `prismatic/cron_receipts/` (schema package). The
overhaul plan proposed deleting it, but WI-9's merged implementation
(`#579`) imports `CronRunReceipt` from it at runtime in
`prismatic/native_crons.py:865` to emit signed-shaped run receipts per native
cron run — it is the extracted receipt value the plan required, so it stays.

**Untouched:** `prismatic/native_crons.py` (the live scheduler),
`prismatic/schedules.py` (Schedule Observatory — verified: no imports of the
deleted modules), the standalone swarmcron journal daemon, and
`scripts/ops/update-swarmcron.sh` (its updater).

## Consequences

- ~11,000 lines removed (4,969 implementation + 6,026 tests + 949 plugin).
  Git history preserves everything; `git log -- prismatic/cron_authority.py`
  recovers the full implementation.
- If signed per-run receipts with authority semantics are ever wanted again,
  reuse starts from `prismatic/cron_receipts/` (the schema) — not from the
  execution model. The ideas worth preserving (immutable registry snapshots,
  catch-up bucket policies, dependency DAGs, execution leases) are documented
  in section 2 of `cron-overhaul-plan.md`.
- The native cron registry + systemd timers remain the two real scheduling
  systems. No migration, no double-firing risk.

## Verification

Full test suite green and ruff clean on the deletion branch before the PR;
`grep` proves zero remaining importers of the deleted modules outside git
history. Live scheduling is unaffected — nothing deleted was reachable from any
service, route, timer, or crontab.
