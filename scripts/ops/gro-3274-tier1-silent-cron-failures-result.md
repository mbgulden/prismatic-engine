# GRO-3274 — Tier-1 Silent Cron Failures Verification

Timestamp: 2026-07-08T13:42Z
Agent: Ned
Branch: `ned/GRO-3274`
PR: https://github.com/mbgulden/prismatic-engine/pull/183

## Summary

GRO-3274 was re-verified after a prior AGY/Fred handoff left Linear stuck in `In Progress` with stale `agent:ned`/`agent:fred` labels. The six original Jul 2 silent failures are resolved or intentionally disabled/paused and no longer counted by the Tier-1 watchdog. A later watchdog dry-run now reports a new unrelated live failure in `Hermes daily journal snapshot` because the live `/home/ubuntu/work/prismatic-engine` checkout is on an older AGY branch; the fix and regression test are already present on `origin/deploy-fresh` and this PR branch.

## Six original failures

| Job | Current disposition | Evidence |
| --- | --- | --- |
| `c72b9496a7ef` Ned Dispatcher Daily Summary | Not present in orchestrator `jobs.json`; original daily-summary fix already shipped in live Ned profile. | `/tmp/tier1_silent_failure_state.json` has `known_silent: []`. |
| `da4062c166b4` AOT Mirror — Broken Link Monitor | Healthy. | `last_status: ok`, last run `2026-07-06T08:00:15-06:00`; `aot_broken_link_check.py` compiles. |
| `faf8d91da716` AGY Sandbox Supervisor | Intentionally paused / not a silent failure. | `enabled: false`, `state: paused`, last status `ok`; watchdog now ignores disabled/paused rows. |
| `eb82b536113c` Monthly Journal Continuity Audit | Healthy. | `monthly_journal_continuity_audit.py` now has `os.execvp(...)` inside `main()` and compiles; job `last_status: ok`. |
| `cf06bd7e8463` OKF Google Drive Drift Check | Healthy. | `check-drive-drift.py` imports `google.auth.transport.requests`, compiles, and job `last_status: ok`. |
| `558b141146ed` gpt-oss-quota-headroom | Intentionally disabled / no longer counted as silent. | `enabled: false`; stale `last_status: error` is ignored by watchdog disabled-job guard. |

## Verification commands run

```bash
python3 -m py_compile \
  /home/ubuntu/.hermes/profiles/orchestrator/scripts/monthly_journal_continuity_audit.py \
  /home/ubuntu/.hermes/profiles/orchestrator/scripts/check-drive-drift.py \
  /home/ubuntu/.hermes/profiles/orchestrator/scripts/aot_broken_link_check.py \
  /home/ubuntu/.hermes/profiles/orchestrator/scripts/agy_sandbox_event_supervisor.py
bash -n /home/ubuntu/.hermes/profiles/orchestrator/scripts/gpt_oss_quota_probe.sh
python3 /home/ubuntu/.hermes/profiles/orchestrator/scripts/tier1_silent_failure_watchdog.py --dry-run --json
```

Observed focused regression output:

```text
PYTHONPATH=/tmp/prismatic-gro3274 python3 -m pytest /tmp/prismatic-gro3274/tests/test_journal_last_sync.py -q
→ 4 passed in 0.10s

PYTHONPATH=/tmp/prismatic-gro3274 python3 - <<'PY'
from prismatic.journal import run_snapshot
print(run_snapshot(force=True).keys())
PY
→ dict_keys(['changed', 'signals', 'today_file', 'lines'])
```

Observed watchdog output after the current rerun:

```json
{
  "total_jobs": 94,
  "silent_failures": 2,
  "new_failures": 1,
  "recovered": ["ecc080d17c00"],
  "failures": [
    {"job_id": "ce3dd849ede5", "name": "Hermes daily journal snapshot", "root_cause": "linear (linear-api: endpoint or token issue, check graphQL response)"},
    {"job_id": "0db3cc8a9c40", "name": "AGY Golden Thread Project Review", "root_cause": "unknown — needs investigation"}
  ]
}
```

A direct rerun of `AGY Golden Thread Project Review` exits 0, but the watchdog still lists it until the next scheduled cron row records a healthy `last_status`. `Hermes daily journal snapshot` fails only when invoked through the live `/home/ubuntu/work/prismatic-engine` checkout, which is currently held on `feature/agy-gro-3212` and lacks the already-merged `_last_sync` string guard. The same snapshot path passes under this branch with `PYTHONPATH=/tmp/prismatic-gro3274`. Both current watchdog rows are newer live-checkout/cron-metadata noise, not unresolved members of the original Jul 2 six-failure batch.

## Notes

No production profile script changes were made in this pass. The durable work is a clean Ned-owned verification branch/PR plus Linear label/state cleanup so peer review can close the foundational Jul 2 issue without another Fred/Ned redispatch loop.
