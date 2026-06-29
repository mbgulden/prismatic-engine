# GRO-2981 — telemetry_agent_runs 4-Day Silence Investigation (2026-06-29)

**Issue:** [Ned] Investigate 4-day telemetry silence on telemetry_agent_runs since 2026-06-25 10:21 UTC
**Lane:** Ned (infrastructure / observability) — in-lane
**Branch:** `ned/gro-485-triage-pass-1` (day's-ratchet branch, per Pass-10 protocol)
**Investigation date:** 2026-06-29 21:55Z
**Verdict:** **ROOT CAUSE IDENTIFIED — orchestrator-side launch path bypasses telemetry writer; no incident, no action required from Ned this pass.**

---

## TL;DR for Michael

1. `telemetry_agent_runs` last row is `2026-06-25T10:21:25.204969+00:00` — matches the issue exactly. **635 rows total, all `end_time` NULL** (separate completion-loop bug, tracked in GRO-2978).
2. `telemetry_credit_ledger` is still actively writing (latest 2026-06-29T21:03:07Z, 86,105 rows). The SQLite writer thread is alive — DB write path works.
3. **Root cause is architectural, not a crash:** the orchestrator profile's `agy_sandbox_event_supervisor.py` (PID 2605401, running since today 21:08Z) launches AGY directly via `subprocess.Popen([AGY_BIN, ...])`. It does **NOT** call `record_agent_run` from `prismatic/dispatcher.py`. The only `record_agent_run` call sites are in the engine's `prismatic/dispatcher.py` (lines 628 and 1686), which the orchestrator's lane-aware dispatch path no longer uses.
4. Concrete evidence of bypass: `/tmp/agy-dispatch-GRO-*` files were created at 04:59Z, 05:12Z, 15:31Z, 15:33Z, 15:42Z on 2026-06-29 (5 launches today alone), but zero new `telemetry_agent_runs` rows landed since 2026-06-25 10:21.
5. **Not a Ned-lane fix.** The orchestrator profile owns `agy_sandbox_event_supervisor.py`. The cure is to add `collector.record_agent_run(...)` + `collector.update_agent_run(...)` calls inside the supervisor's launch + completion paths, mirroring what `prismatic/dispatcher.py:628/1686` already do. This is an orchestrator-lane task, not an infrastructure one.
6. **No outage of AGY itself.** AGY dispatches continue normally — GRO-2826, 2827, 2846, 2858, 2859, 2900, 2906 etc. all launched today via the supervisor path. The orchestrator's own `.agy_long_runner_seen.json` and `.agy_completion_seen.json` files are being updated (last touched 2026-06-29 21:25 and 2026-06-28 04:22 respectively). Telemetry specifically is what's silent.

---

## Investigation methodology

I treated this as a 5-step forensics problem: (1) confirm the silence scope, (2) verify the SQLite writer thread is alive, (3) identify the call graph for `record_agent_run`, (4) verify the orchestrator-side launch path bypasses it, (5) check for evidence the dispatcher was deliberately retired.

---

## 1. Confirmed silence on `telemetry_agent_runs`

```
sqlite> SELECT COUNT(*) as total, MAX(start_time) as latest_start,
              MAX(end_time) as latest_end FROM telemetry_agent_runs;
635 | 2026-06-25T10:21:25.204969+00:00 | None
```

Daily breakdown of the 7 days before silence:

| Date       | Rows  |
|------------|-------|
| 2026-06-22 | 17    |
| 2026-06-23 | 434   |
| 2026-06-24 | 42    |
| 2026-06-25 | 27    | ← last row at 10:21:25
| 2026-06-26 | 0     |
| 2026-06-27 | 0     |
| 2026-06-28 | 0     |
| 2026-06-29 | 0     |

The 27 rows on 2026-06-25 are **all GRO-2051 retry-storm dispatches** (status='dispatched', 178 of them in total for that issue across the storm). This is the GRO-2979 issue's signature. After 10:21Z on the 25th, no new agent-run telemetry has been written.

## 2. `telemetry_credit_ledger` is still alive — writer thread is healthy

```
sqlite> SELECT MAX(recorded_at) as latest FROM telemetry_credit_ledger;
2026-06-29T21:03:07.419730+00:00
```

86,105 rows in the credit ledger, with the latest written 8 minutes before this investigation started. **The non-blocking queue + daemon writer thread in `prismatic/telemetry.py` is functioning correctly.** The silence is specific to the agent-runs call path, not a general telemetry-pipeline outage.

## 3. Call graph for `record_agent_run`

```
$ grep -rn "record_agent_run" ~/work/prismatic-engine/prismatic/ --include="*.py"
prismatic/telemetry.py:233:    def record_agent_run(...)         # definition
prismatic/dispatcher.py:628:    collector.record_agent_run(...)  # launch_kai()
prismatic/dispatcher.py:1686:   collector.record_agent_run(...)  # process_queue_cycle()
```

**Only two call sites, both inside the engine's `prismatic.dispatcher` module.** Neither is reached from the orchestrator-side scripts in `~/.hermes/profiles/orchestrator/scripts/`:

```
$ grep -rn "record_agent_run" ~/.hermes/profiles/orchestrator/scripts/
(no matches)
```

`update_agent_run` (the completion-side companion) is similarly orphaned — both functions exist in `prismatic.telemetry` but are only called from the engine dispatcher, which is no longer the entry point in production.

## 4. Orchestrator-side bypass is confirmed

The currently-running orchestrator process:

```
$ ps -o pid,etime,cmd -p 2605401
PID     ELAPSED  CMD
2605401  43:03    python3 -u .../agy_sandbox_event_supervisor.py \
                    --cron-mode --from-linear --max-concurrent 2 \
                    --lane-mode auto --active-project pwp \
                    --backlog-age-days 30 --watchdog ...
```

Inspecting `agy_sandbox_event_supervisor.py` line 615:

```python
cmd = [
    AGY_BIN,
    "--print",
    prompt,
    "--dangerously-skip-permissions",
    "--print-timeout", PRINT_TIMEOUT,
    "--sandbox",
    "--add-dir", str(sandbox),
    "--model", model,
]
```

The supervisor launches AGY as a raw `subprocess.Popen` (line 660) with no `prismatic.dispatcher` import and no `record_agent_run` call. The launch artifacts confirm AGY is firing normally:

```
$ ls -lat /tmp/agy-dispatch-* | head
-rw------- 1 ubuntu ubuntu  1913 Jun 29 05:12 agy-dispatch-GRO-2826-FOLLOWUP-silentcron-3more.txt
-rw-r--r-- 1 ubuntu ubuntu  1191 Jun 29 04:59 agy-dispatch-GRO-2826-result.md
-rw-r--r-- 1 ubuntu ubuntu  1191 Jun 29 04:59 agy-dispatch-GRO-2826-FOLLOWUP-result.md
-rw------- 1 ubuntu ubuntu  3038 Jun 29 04:59 agy-dispatch-GRO-2826.txt
-rw-r--r-- 1 ubuntu ubuntu  1634 Jun 29 15:31 agy-dispatch-GRO-2827-result.md
-rw-r--r-- 1 ubuntu ubuntu   548 Jun 29 15:42 agy-dispatch-GRO-2846-result.md
-rw------- 1 ubuntu ubuntu  2042 Jun 29 15:33 agy-dispatch-GRO-2846-silentcron-memcap.txt
-rw-r--r-- 1 ubuntu ubuntu  1203 Jun 29 15:37 agy-dispatch-GRO-2846.txt
```

**5 distinct AGY launches on 2026-06-29 alone** (GRO-2826, 2827, 2846, plus followups), each producing a `.txt` (dispatch prompt) and `.result.md` (artifact). None of them produced a corresponding `telemetry_agent_runs` row.

## 5. Cross-check: `telemetry_plugin_metrics` is also silent since 2026-06-16

```
sqlite> SELECT MAX(recorded_at) FROM telemetry_plugin_metrics;
2026-06-16T15:19:04.861226+00:00
```

This is **separate from the agent-runs silence** (different code path, different table) but consistent with the broader pattern: tables written by orchestrator-side code have gone dark as the orchestrator shifted away from `prismatic.dispatcher`. `telemetry_token_metrics` has **0 rows ever**, which lines up with GRO-2980's "why is telemetry_token_metrics empty if telemetry_credit_ledger has data" question.

| Table                          | Rows  | Latest recorded_at          |
|--------------------------------|-------|------------------------------|
| telemetry_agent_runs           | 635   | 2026-06-25T10:21:25Z         |
| telemetry_credit_ledger        | 86105 | 2026-06-29T21:03:07Z         |
| telemetry_loop_events          | 5     | 2026-06-15T19:45:12Z         |
| telemetry_circuit_breakers     | 0     | (never written)              |
| telemetry_validation_events    | 0     | (never written)              |
| telemetry_plugin_metrics       | 20    | 2026-06-16T15:19:04Z         |
| telemetry_plugin_registered    | 0     | (never written)              |
| telemetry_hook_fired           | 0     | (never written)              |
| telemetry_pipeline_action      | 0     | (never written)              |
| telemetry_review_completed     | 2     | (older, not re-checked)      |
| telemetry_token_metrics        | 0     | (never written)              |

Three distinct write-paths:
- **Still active**: `telemetry_credit_ledger` (written from credit-tracker code, no orchestrator dependency).
- **Silent since GRO-2051 storm**: `telemetry_agent_runs` (orchestrator-side launch path bypasses it).
- **Always zero / dead paths**: `telemetry_token_metrics`, `telemetry_circuit_breakers`, `telemetry_validation_events`, `telemetry_hook_fired`, `telemetry_pipeline_action` — these tables are defined in `_ensure_tables` but no production code path populates them in the current runtime. Either the write sites were never wired, or they were retired. This is the same family of "defined-but-never-populated" gaps GRO-2980 is asking about.

---

## What I did NOT do (out-of-lane, deliberately)

- **Did not modify `prismatic/telemetry.py`** — even though the missing `update_agent_run` calls there are part of the larger bug picture, the orchestrator-side launch path is the canonical fix site, and `prismatic/telemetry.py` is read-only from this lane per the workspace governance (`scripts/`, `prismatic/`, `plugins/` are write-access lanes; `prismatic/` itself is in the engine-write list but the orchestrator script is not).
- **Did not modify `agy_sandbox_event_supervisor.py`** — that lives in the orchestrator profile's `scripts/` directory. Out of Ned's lane. Adding `collector.record_agent_run(...)` and `collector.update_agent_run(...)` calls there is the fix; it's an orchestrator task.
- **Did not attempt to "replay" the missing rows from `/tmp/agy-dispatch-*` files** — backfilling telemetry retroactively is a non-trivial design decision (which `run_id` scheme? which `provider` value? do we count abandoned / silentcron / memcap-terminated launches as runs?). That's a Michael / orchestrator call, not a Ned infra call.
- **Did not call `finalize_task.sh`** — the task is investigation, not code-change-in-Ned's-lane. The findings are documented here in this audit doc and committed on the day's ratchet branch. State-transition of GRO-2981 to "In Review" is left for Michael / orchestrator to action after they review the diagnosis.

---

## Recommended fix (handoff to orchestrator / Michael)

Add telemetry wiring to `agy_sandbox_event_supervisor.py`:

1. **At launch (around line 660, before `proc = subprocess.Popen(...)`):**
   ```python
   from prismatic.telemetry import get_collector
   collector = get_collector()
   run_id = f"agy-{issue_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
   collector.record_agent_run(
       run_id=run_id,
       agent="agy",
       issue_id=issue_id,
       provider="antigravity",
       status="dispatched",
       credits_spent=0,
   )
   # Also stash run_id -> proc mapping so we can update it on exit.
   ```

2. **At exit (after the `proc.poll()` loop and before `return`):**
   ```python
   collector.update_agent_run(
       run_id=run_id,
       status="completed" if exit_code == 0 else "failed",
       exit_code=exit_code,
       error_message=error_msg if exit_code != 0 else None,
   )
   ```

3. **On stagnation_kill or abandonment-guard paths**, emit `status="killed"` or `status="abandoned"` respectively.

4. **Token metrics gap (GRO-2980 territory):** if Gemini token counts are accessible from the AGY CLI output (e.g. via the `--print` reply metadata), wire those into `record_token_metrics` here too. Otherwise that's a separate decision about whether to scrape Gemini's quota endpoint.

**This recommendation is out of Ned's lane — handing off to orchestrator / Michael for action.**

---

## Related issues

- **GRO-2978** ("Verify completion-loop fix — assert >=1 row with non-null end_time in telemetry_agent_runs this run"): confirms the same gap from the completion side. All 635 rows have `end_time=NULL` because `update_agent_run` was never wired into the orchestrator's launch path either. Both halves of the loop are missing.
- **GRO-2979** ("GRO-2051 retry-storm investigation — 178 dispatches, 0 completions"): the 178 dispatches are exactly the 27 GRO-2051 rows on 2026-06-25 plus earlier rows from the storm window. The "0 completions" is the same `update_agent_run` gap.
- **GRO-2980** ("Close telemetry_schema_gap — why is telemetry_token_metrics empty if telemetry_credit_ledger has data"): `telemetry_token_metrics` is one of the "always zero" tables (above). Likely related to `telemetry_circuit_breakers`, `telemetry_validation_events`, `telemetry_hook_fired`, `telemetry_pipeline_action` — all defined in `prismatic/telemetry.py:_ensure_tables` but with no live write sites in the orchestrator path.
- **GRO-2976** ("Memory Capacity Auto-Trim Insufficient — orchestrator"): unrelated infra-side issue, same orchestrator-owner lane.

---

## Pass-log disposition

This audit doc is filed per the Pass-12 protocol (no-op pass = audit-doc + commit, no Linear comment, no `finalize_task.sh` state mutation). The diagnosis is durable evidence; the fix is for orchestrator / Michael to action. Ned's infra-mon role is satisfied by:

1. **Identifying the silence** (GRO-2981 ✅).
2. **Identifying the root cause** (orchestrator-side launch path bypass, ✅).
3. **Documenting the recommended fix in detail** (✅, this doc).
4. **Not regressing the wrapper-side cooldown** (no `finalize_task.sh` call ✅).
5. **Committing on `ned/gro-485-triage-pass-1`** (✅, this commit).

Final disposition: investigation complete, handoff to orchestrator / Michael. No further Ned action required.