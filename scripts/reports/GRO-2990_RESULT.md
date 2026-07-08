# GRO-2990 RESULT — Wire `record_tokens()` at LLM call sites

## Summary

Implemented and verified Ned-side token metrics wiring for the Prismatic Engine branch `ned/GRO-2990`.

## Changed paths

- `prismatic/agy_live_parser.py` — records `telemetry_token_metrics` rows from parsed AGY status-line token usage.
- `prismatic/billing/cost_attribution.py` — records token metrics alongside `telemetry_credit_ledger` writes in `CostAttributionEngine.record_usage()`.
- `prismatic/dispatcher.py` — parses provider stdout token summaries and records tokens for launch-style agent subprocesses.
- `prismatic/test_gro_2990_tokens_wiring.py` — focused AGY parser + cost attribution coverage.
- `prismatic/tests/test_gro2980_token_metrics_wiring.py` — dispatcher token parser/drain coverage relocated under Ned-owned `prismatic/` lane.
- `scripts/reports/GRO-2990_RESULT.md` — this completion report.

## Verification

Command run from `/tmp/prismatic-gro2990`:

```bash
python3 -m pytest prismatic/test_gro_2990_tokens_wiring.py prismatic/tests/test_gro2980_token_metrics_wiring.py -q
```

Result:

```text
16 passed in 1.67s
```

## Notes

- The previous local branch had a root-level `tests/test_gro2980_token_metrics_wiring.py`, which is outside Ned's current Prismatic lane. It has been moved to `prismatic/tests/test_gro2980_token_metrics_wiring.py` before finalization.
- Work was verified in isolated worktree `/tmp/prismatic-gro2990` to avoid contaminating the shared `/home/ubuntu/work/prismatic-engine` checkout.
