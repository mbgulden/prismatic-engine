# GRO-3495 — Linear queue duplicate/review-noise cleanup

Date: 2026-07-06
Agent: Ned
Issue: GRO-3495

## Summary

Executed a bounded Linear queue hygiene pass for duplicate silent-cron / cron-fix review tasks and stale unowned review noise.

The cleanup intentionally avoided code/content/assets/design changes. It only mutated Linear issue state/labels/comments and records the evidence here for the task ledger.

## Duplicate families collapsed

Kept one canonical issue per exact-title family and canceled the duplicate active filings. Active routing labels were removed from canceled duplicates.

| Family | Canonical kept | Duplicates canceled |
|---|---:|---:|
| `Hermes daily journal snapshot` silent-failing | GRO-2954 | GRO-3018, GRO-3020 |
| `Hermes daily journal snapshot` cron-fix unknown | GRO-3405 | GRO-3408 |
| `Prismatic Engine State Backup` cron-fix unknown | GRO-3407 | GRO-3410 |
| `gpt-oss-quota-headroom` cron-fix unknown | GRO-3406 | GRO-3409 |
| `Kai Delta Dispatcher` silent-failing | GRO-3037 | GRO-3039 |
| `Ned Delta Dispatcher` silent-failing | GRO-3036 | GRO-3038 |
| `OKF Google Drive Drift Check` silent-failing | GRO-2445 | GRO-3016 |
| `Engine log rotation` silent-failing | GRO-2827 | GRO-2909 |
| `Memory Capacity Alert` silent-failing | GRO-2439 | GRO-2905 |
| `[growthwebdev-knowledge] 11 commits but only 0 merged PRs` | GRO-2934 | GRO-3035 |
| `AGY Sandbox Supervisor — event-driven organic scaling` silent-failing | GRO-2438 | GRO-2862 |

## Stale review noise parked

Moved stale/unowned review-noise issues out of active review/execution states into Backlog with no active routing labels:

- GRO-1927 — Mobile touch action buttons layout jumbled
- GRO-1928 — Sprite cleanup / boss sprites
- GRO-1931 — AOT CRO mobile floating bottom CTA
- GRO-1942 — Restore Prismatic credit policy engine
- GRO-2845 — `Prismatic Engine — Ned autonomous task loop` silent-failing duplicate/no-label residue

## Verification snapshot

Post-mutation verification query returned:

```text
GRO-2954 In Progress ['agent:fred']
GRO-3018 Canceled []
GRO-3020 Canceled []
GRO-3405 In Progress ['agent:fred', 'agent:peer-review']
GRO-3408 Canceled []
GRO-3407 In Progress ['agent:fred', 'agent:peer-review']
GRO-3410 Canceled []
GRO-3406 In Progress ['agent:fred', 'agent:peer-review']
GRO-3409 Canceled []
GRO-3037 Todo ['agent:agy', 'dispatch:ready']
GRO-3039 Canceled []
GRO-3036 In Progress ['agent:peer-review']
GRO-3038 Canceled []
GRO-2445 In Review ['agent:peer-review']
GRO-3016 Canceled []
GRO-2827 In Review ['agent:peer-review']
GRO-2909 Canceled []
GRO-2439 In Review ['agent:peer-review']
GRO-2905 Canceled []
GRO-2934 Todo ['dispatch:ready', 'agent:agy']
GRO-3035 Canceled []
GRO-2438 Todo ['dispatch:ready', 'agent:ned']
GRO-2862 Canceled []
GRO-1927 Backlog []
GRO-1928 Backlog []
GRO-1931 Backlog []
GRO-1942 Backlog []
GRO-2845 Backlog []
GRO-3495 In Progress ['agent:ned']
```

## Notes

- Each canceled/parked issue received a Linear comment explaining the cleanup and the canonical retained issue where applicable.
- GRO-3495 was moved from Backlog to In Progress before execution; finalization should move it to In Review and post the final evidence comment.
- This was a queue-ledger cleanup, not a code change. The report is the committed evidence artifact.
