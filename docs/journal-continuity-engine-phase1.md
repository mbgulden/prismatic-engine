# Journal Continuity Engine — Phase 1 Extraction Notes

Date: 2026-06-18

## Intent

Option A is a mechanical kernel extraction, not a redesign. The goal is to prove the engine can be independent and integrated at the same time:

- **Independent:** a fresh LLM setup with shell access can call `prismatic-*` commands without importing Hermes.
- **Integrated:** Hermes keeps cron/profile convenience through tiny wrapper scripts that `exec` the engine CLIs.

## What moved into the kernel

The first canary surface is the journal continuity setup:

- `prismatic-journal inventory` — bounded journal/session/cron inventory.
- `prismatic-journal monthly` — inventory + monthly Linear control/audit issue creation.
- `prismatic-journal-snapshot` — compact daily journal/event index capture.
- `prismatic-linear-import` — conservative Linear import readiness check.
- `prismatic-second-witness` — deterministic process/artifact validation.

These live in `prismatic/journal.py` and are exported via `pyproject.toml` entry points.

## What stayed in the harness

Hermes retains only plumbing:

- Cron schedules.
- Profile-specific `.env` secrets.
- Profile/session/log paths.
- Bot routing and notification policy.

The former orchestrator scripts now delegate:

- `monthly_journal_continuity_audit.py` → `prismatic-journal monthly`.
- `journal_snapshot.py` → `prismatic-journal-snapshot`.
- `import_journal_continuity_plan.py` → `prismatic-linear-import --period initial`.

## Design lessons for Option B

1. **Mechanical enumeration belongs in the engine.** AGY timed out doing filesystem inventory. The engine should provide deterministic inventory CLIs; LLMs should classify and synthesize.
2. **Artifact checks beat process checks.** `prismatic-second-witness` validates required files exist and are non-empty. Exit code 0 is not enough.
3. **Harness shims must be boring.** If a shim contains business logic, it is no longer a shim. That drift is the migration smell.
4. **Engine env vars should be `PRISMATIC_*`.** Harness env vars can be compatibility fallbacks only at the boundary.
5. **Phase 1 should avoid new mutations.** `prismatic-linear-import --execute` is reserved for Phase 2 because safe dedupe/mutation needs a provider abstraction.

## Verification runbook

From the repo root:

```bash
python3 -m pytest tests/test_journal.py -q
python3 -m py_compile prismatic/journal.py
prismatic-journal inventory --period smoke
prismatic-journal-snapshot --help
prismatic-linear-import --period initial
prismatic-second-witness --log-path /tmp/some-log --artifact /tmp/some-artifact
```

Expected:

- Tests pass.
- Inventory writes `source-inventory.json` and `source-inventory.md` under the configured report root.
- Harness scripts remain executable and simply delegate to the engine CLIs.

## Phase 2 backlog seeds

- `prismatic-inventory`: generic deterministic file/cron/env enumeration.
- `prismatic-linear-import --execute`: provider-backed dedupe and create/update mutations.
- `PRISMATIC_ENGINE.yaml` journal source config.
- A formal harness adapter contract: install shims, register cron, route notifications, never own business logic.
