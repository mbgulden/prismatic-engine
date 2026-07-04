# GRO-3399 verification — Hermes daily journal snapshot

Issue: GRO-3399
Date: 2026-07-04
Agent: Ned

## Disposition

GRO-3399 is the same silent-cron failure shape as GRO-3396 for orchestrator cron job `ce3dd849ede5` (`Hermes daily journal snapshot`). The underlying race fix is already present on the current `origin/deploy-fresh` baseline: `prismatic.journal.collect_candidates()` caches mtimes observed during traversal and no longer re-stats candidate cron-output files during sort.

No additional code change was required for this duplicate/resurfaced ticket. This report records fresh verification and gives Linear a durable review artifact for GRO-3399.

## Evidence

- Issue body target: profile `orchestrator`, job `ce3dd849ede5`, schedule `every 60m`.
- Current scheduler status after direct run:
  - `last_run_at`: `2026-07-04T10:26:10.780755-06:00`
  - `last_status`: `ok`
  - `last_error`: `None`
  - `last_delivery_error`: `None`
  - `next_run_at`: `2026-07-04T11:26:10.780755-06:00`
- Latest cron artifact: `/home/ubuntu/.hermes/profiles/orchestrator/cron/output/ce3dd849ede5/2026-07-04_10-26-10.md` (273 bytes).
- `python3 -m py_compile prismatic/journal.py` passed.
- Focused ad-hoc verifier created under `/tmp` with a `hermes-verify-` prefix and cleaned up afterward:
  - `AD-HOC PASS: collect_candidates survives transient cron-output deletion race`
  - `AD-HOC DETAIL: candidate stat calls=2 (old code would call 3 and raise FileNotFoundError)`
- Direct scheduler exercise:
  - `hermes --profile orchestrator cron run ce3dd849ede5`
  - Result: `Ran now: succeeded.`

## Follow-up

Remove active routing residue (`agent:ned`, `agent:needs-human-review`) after finalization so this verified duplicate does not keep re-entering Ned's active queue. Preserve human review visibility with `agent:peer-review` when available.
