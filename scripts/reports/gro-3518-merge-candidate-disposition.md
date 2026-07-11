# GRO-3518 merge candidate disposition — 2026-07-06T07:06Z

Task: process the four clean nonterminal merge candidates identified by GRO-3514 without widening the parent audit ticket.

## Live merge status before action

- Source: `/home/ubuntu/.prismatic/merge-pipeline/state_v6.json` via the same `StateManager` path used by `/api/gateway/merge/status`.
- Initial residual queue at this pass: `pending_count=7`.
- The four GRO-3518 candidates were present in `pending`: GRO-3168, GRO-3241, GRO-3295, GRO-3297.
- State backup before mutation: `/tmp/state_v6_gro3518_20260706T070506Z.json`.

## Disposition table

| Candidate | Decision | Evidence / reason |
|---|---|---|
| GRO-3168 | **Reject** | `prismatic_merge.cli apply --tickets GRO-3168 GRO-3241` attempted the candidate. GRO-3168 was rolled back and recorded rejected because import verification failed: `Unit test suite failed (code 2)`. Fresh focused repro in the staging worktree: `PYTHONPATH=. pytest prismatic/supervisor/tests/test_recovery.py -q` fails during collection with `AttributeError: 'Settings' object has no attribute 'prismatic_supervisor_dlq'`. Do not merge until the recovery/settings compatibility is fixed. |
| GRO-3241 | **Reject** | Merge attempt reported `Cherry-pick conflict`. Investigation showed `INSTALL.md` and `setup.py` already exist on `merge/pipeline-staging` from commit `88040ee3` (`[AGY] GRO-3241 work from sandbox supervisor`), but fresh acceptance verification failed: `python3 setup.py egg_info` exits `1` with setuptools flat-layout multiple-top-level-packages discovery error. Do not re-merge until `setup.py` explicitly constrains package discovery. |
| GRO-3295 | **Reclassify / reject stale partial artifact** | Live Linear state is `Todo` with `agent:agy` + `dispatch:ready` after Michael-approved AGY requeue. The sandbox only adds `scripts/agy_env_guard.py`; it does not satisfy the issue acceptance requiring supervisor-startup/per-dispatch integration and the issue is actively handed to AGY. Removed from pending as a stale partial artifact. |
| GRO-3297 | **Reclassify / reject stale partial artifact** | Live Linear state is `In Progress` with `agent:agy` + `dispatch:ready`. Michael already approved closing the insufficient PR #603 path and routing this to AGY for a real rendered-output visual verification implementation. The sandbox only adds design/plan docs; it does not implement visual verification, auto-fix attempts, verified UI state, or the self-correcting loop. Removed from pending as a stale partial artifact. |

## Commands / verification run

```bash
cd /home/ubuntu/work/prismatic-merge
PRISMATIC_HOME=/home/ubuntu python3 -m prismatic_merge.cli apply --tickets GRO-3168 GRO-3241
# -> GRO-3241 rejected: Cherry-pick conflict
# -> GRO-3168 rejected: Import verify failed

cd /home/ubuntu/.prismatic/merge-worktree
PYTHONPATH=. pytest prismatic/supervisor/tests/test_recovery.py -q
# -> ERROR collecting test_recovery.py; missing Settings.prismatic_supervisor_dlq

python3 setup.py --help
# -> command help path works
python3 setup.py egg_info
# -> exit 1; setuptools flat-layout multiple top-level packages error
```

No candidate landed as a new merge commit in this pass. GRO-3241 was already partially present in staging, but failed its legacy-distribution acceptance check, so it is treated as rejected rather than applied.

## Live merge status after action

- Residual `/api/gateway/merge/status`-equivalent pending count: `3`.
- Remaining pending tickets: `GRO-2892`, `GRO-2991`, `GRO-3277`.
- `merged_count`: `266`.
- `last_apply`: `null`.
- `drift_detected`: `false`.

## State mutations recorded

The merge-pipeline state now records rejection/reclassification entries for all four GRO-3518 candidates, removing them from `pending`:

- GRO-3168 — import verification failure.
- GRO-3241 — conflict plus failed `setup.py egg_info` acceptance.
- GRO-3295 — stale partial artifact; active AGY handoff remains the source of truth.
- GRO-3297 — stale partial artifact; active AGY handoff remains the source of truth.

## Follow-up

The residual merge queue is no longer blocked by these four clean active candidates. The next merge-backlog slice should handle the three remaining pending records, especially contention-heavy `GRO-3277` and `GRO-2991`.
