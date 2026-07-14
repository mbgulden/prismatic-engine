# OKF location map for Prismatic Engine

Prismatic Engine does **not** currently keep a first-class `okf/` tree in this repository on `deploy-fresh`.

Use this map before creating new project closeouts, incident reports, or durable verification records.

## Canonical OKF hub

| Purpose | Location |
|---|---|
| Local workspace | `/home/ubuntu/work/growthwebdev-knowledge` |
| GitHub repo | `https://github.com/mbgulden/growthwebdev-knowledge` |
| Hub master index | `okf/index.md` |
| Prismatic project index | `okf/projects/prismatic-engine.md` |
| Prismatic project records directory | `okf/projects/prismatic-engine/` |

## Current Prismatic Ingestion Queue record

The Governance Dashboard Ingestion Queue repair closeout lives in the OKF hub:

```text
/home/ubuntu/work/growthwebdev-knowledge/okf/projects/prismatic-engine/ingestion-queue-repair-2026-07-14.md
```

GitHub path:

```text
https://github.com/mbgulden/growthwebdev-knowledge/blob/main/okf/projects/prismatic-engine/ingestion-queue-repair-2026-07-14.md
```

Related index entries:

```text
/home/ubuntu/work/growthwebdev-knowledge/okf/index.md
/home/ubuntu/work/growthwebdev-knowledge/okf/projects/prismatic-engine.md
```

## Rule of thumb

- **Code, tests, dashboard behavior:** edit this repo, `mbgulden/prismatic-engine`.
- **Durable OKF closeouts and cross-project records:** edit `mbgulden/growthwebdev-knowledge`.
- **If a future `okf/` spoke tree is restored in this repo:** keep this file updated with the canonical split so agents do not create duplicate records.

## Verification boundary

The Ingestion Queue repair record in the OKF hub explicitly distinguishes:

- ad hoc targeted verification;
- durable regression-contract pass;
- not full suite green;
- remaining drainer caveat: `prismatic-webhook-drain.service` remains masked/inactive unless separately repaired.
