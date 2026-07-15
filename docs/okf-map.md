# OKF location map for Prismatic Engine

Prismatic Engine does **not** currently keep a first-class `okf/` tree in this repository on `deploy-fresh`.

Use this map before creating new project closeouts, incident reports, durable verification records, or cleanup proposals.

## Canonical OKF hub

| Purpose | Location |
|---|---|
| Local workspace | `/home/ubuntu/work/growthwebdev-knowledge` |
| GitHub repo | `https://github.com/mbgulden/growthwebdev-knowledge` |
| Hub master index | `okf/index.md` |
| Canonical Prismatic project index | `okf/projects/prismatic-engine/index.md` |
| Legacy compatibility pointer | `okf/projects/prismatic-engine.md` |
| Prismatic project records directory | `okf/projects/prismatic-engine/` |
| Prismatic archive directory | `okf/projects/prismatic-engine/archive/` |

## Current canonical Prismatic records

These records live in the OKF hub:

```text
/home/ubuntu/work/growthwebdev-knowledge/okf/projects/prismatic-engine/index.md
/home/ubuntu/work/growthwebdev-knowledge/okf/projects/prismatic-engine/ingestion-queue-repair-2026-07-14.md
/home/ubuntu/work/growthwebdev-knowledge/okf/projects/prismatic-engine/tier-7-journey.md
/home/ubuntu/work/growthwebdev-knowledge/okf/projects/prismatic-engine/tier-7-architecture.md
/home/ubuntu/work/growthwebdev-knowledge/okf/projects/prismatic-engine/dispatcher-incident-history.md
/home/ubuntu/work/growthwebdev-knowledge/okf/projects/prismatic-engine/governance-dashboard-history.md
/home/ubuntu/work/growthwebdev-knowledge/okf/projects/prismatic-engine/okf-drift-and-recovery-history.md
```

GitHub paths:

```text
https://github.com/mbgulden/growthwebdev-knowledge/blob/main/okf/projects/prismatic-engine/index.md
https://github.com/mbgulden/growthwebdev-knowledge/blob/main/okf/projects/prismatic-engine/ingestion-queue-repair-2026-07-14.md
https://github.com/mbgulden/growthwebdev-knowledge/blob/main/okf/projects/prismatic-engine/tier-7-journey.md
https://github.com/mbgulden/growthwebdev-knowledge/blob/main/okf/projects/prismatic-engine/tier-7-architecture.md
https://github.com/mbgulden/growthwebdev-knowledge/blob/main/okf/projects/prismatic-engine/dispatcher-incident-history.md
https://github.com/mbgulden/growthwebdev-knowledge/blob/main/okf/projects/prismatic-engine/governance-dashboard-history.md
https://github.com/mbgulden/growthwebdev-knowledge/blob/main/okf/projects/prismatic-engine/okf-drift-and-recovery-history.md
```

## Treasure-map report

The source inventory, hidden-branch extraction, duplicate grouping, and classification report lives here:

```text
/home/ubuntu/work/growthwebdev-knowledge/okf/reports/prismatic-okf-treasure-hunt-2026-07-15.md
https://github.com/mbgulden/growthwebdev-knowledge/blob/main/okf/reports/prismatic-okf-treasure-hunt-2026-07-15.md
```

## Historical/archive records

Batch 3 archive records preserve provenance without making hidden branch contents current truth:

```text
/home/ubuntu/work/growthwebdev-knowledge/okf/projects/prismatic-engine/archive/index.md
/home/ubuntu/work/growthwebdev-knowledge/okf/projects/prismatic-engine/archive/ned-scan-triage-history.md
/home/ubuntu/work/growthwebdev-knowledge/okf/projects/prismatic-engine/archive/agy-audit-history.md
/home/ubuntu/work/growthwebdev-knowledge/okf/projects/prismatic-engine/archive/canonical-merge-winner-maps.md
/home/ubuntu/work/growthwebdev-knowledge/okf/projects/prismatic-engine/archive/plugin-ecosystem-history.md
/home/ubuntu/work/growthwebdev-knowledge/okf/projects/prismatic-engine/archive/other-prismatic-docs-history.md
/home/ubuntu/work/growthwebdev-knowledge/okf/projects/prismatic-engine/archive/unsafe-private-quarantine.md
```

Archive/quarantine boundary:

- Archive docs are historical/provenance records, not current operational truth.
- Unsafe/private candidates remain redacted and quarantined pending manual review.
- Cleanup remains blocked; do **not** delete hidden branches, refs, worktrees, local archive dirs, or duplicate docs from this map alone.

## Reusable standards and decision records

```text
/home/ubuntu/work/growthwebdev-knowledge/okf/standards/prismatic-dashboard-live-proof.md
/home/ubuntu/work/growthwebdev-knowledge/okf/standards/okf-worktree-reconciliation.md
/home/ubuntu/work/growthwebdev-knowledge/okf/decisions/prismatic-okf-hub-and-spoke-map.md
```

Use these before claiming dashboard/control-plane health, reconciling hidden docs, or changing the hub/spoke split.

## Rule of thumb

- **Code, tests, dashboard behavior:** edit this repo, `mbgulden/prismatic-engine`.
- **Durable OKF closeouts, indexes, standards, decisions, and historical archive records:** edit `mbgulden/growthwebdev-knowledge`.
- **If a future `okf/` spoke tree is restored in this repo:** keep this file updated with the canonical split so agents do not create duplicate records.

## Verification boundary

OKF records in the hub explicitly distinguish:

- ad hoc targeted verification;
- durable regression-contract pass where applicable;
- not full suite green;
- remaining operational caveats such as disabled/masked drainer services;
- archive/quarantine records that preserve provenance without authorizing cleanup.
