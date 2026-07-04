# GRO-2264 verification — Morning Digest cron

Date: 2026-07-04T17:43Z  
Verifier: Ned

## Verdict

`GRO-2264` is not currently silent-failing. The Morning Digest cron job is enabled, scheduled, produced a fresh 2026-07-04 artifact, and the script completed successfully in a side-effect-suppressed live smoke run.

This is a stale redispatch / label-cleanup case: the Linear issue is already `In Review`, but still carried active routing labels (`agent:ned`, `dispatch:ready`) when this pass began.

## Current scheduler evidence

Source: `/home/ubuntu/.hermes/profiles/orchestrator/cron/jobs.json`

- Job ID: `47b66f4df172`
- Name: `Morning Digest — Linear + dispatch + AGY status to Telegram`
- Enabled: `true`
- State: `scheduled`
- Script: `morning_digest.py`
- Schedule: `0 7 * * *`
- Last run: `2026-07-04T07:00:44.906077-06:00`
- Last status: `ok`
- Last error: `null`
- Last delivery error: `null`
- Next run: `2026-07-05T07:00:00-06:00`

## Latest cron artifact

Source: `/home/ubuntu/.hermes/profiles/orchestrator/cron/output/47b66f4df172/2026-07-04_07-00-44.md`

Observed artifact content:

- Run Time: `2026-07-04 07:00:44`
- Mode: `no_agent (script)`
- Digest rendered successfully:
  - `Dedup store: 323 linear`
  - `AGY idle`
  - `Linear activity (last 12h, 49 changed)`
  - Done / In motion / Todo sections populated

The artifact is the current scheduled-run proof that the cron fired and produced digest output.

## Defensive handling evidence

Source: `/home/ubuntu/.hermes/profiles/orchestrator/scripts/morning_digest.py`

The script has the defensive empty-upstream handling the stale issue description requested:

- `fetch_recently_changed()` catches `urllib.error.HTTPError` and `json.JSONDecodeError`, logs `[WARN] linear fetch failed`, and returns `[]`.
- `dedup_summary()` returns `{}` when the dedup DB is missing.
- `categorize()` returns empty bucket lists when `issues` is empty.
- Per-issue state and labels are guarded with `(it.get('state') or {})`, `(it.get('labels') or {})`, and `nodes or []`.
- Digest rendering tolerates empty `done`, `in_progress`, `todo_fresh`, and `blocked` buckets.

Script stat observed during this pass:

- Modified: `2026-07-04 01:59:07.259784609 +0000`
- Size: `8862` bytes

## Live smoke run

To avoid duplicate Telegram delivery, I imported the script as a module and monkeypatched `send_telegram()` to a verifier stub before calling `main()`.

Command shape:

```python
spec = importlib.util.spec_from_file_location('morning_digest_verify', path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.send_telegram = fake_send
rc = mod.main()
```

Observed output:

- Rendered digest at `2026-07-04 17:43`
- `Dedup store: 217 linear`
- `AGY idle`
- `Linear activity (last 12h, 50 changed)`
- `[VERIFY] send_telegram suppressed; digest_chars=639`
- `[VERIFY] main_return=0; suppressed_sends=[639]`

No exceptions. The live smoke proves the current script tolerates the live upstream state and renders a digest.

## Linear state at pickup

Live Linear read at pickup:

- State: `In Review`
- Labels: `agent:ned`, `dispatch:ready`

That label set is stale active-dispatch residue. Correct cleanup after this verification is to keep the issue in `In Review`, remove `agent:ned` and `dispatch:ready`, and add `agent:peer-review` if available.

## Files inspected

- `/tmp/issue-batches/GRO-2264.txt`
- `/home/ubuntu/.hermes/profiles/ned/scripts/autonomous-task-skeleton.md`
- `/home/ubuntu/.hermes/profiles/orchestrator/cron/jobs.json`
- `/home/ubuntu/.hermes/profiles/orchestrator/cron/output/47b66f4df172/2026-07-04_07-00-44.md`
- `/home/ubuntu/.hermes/profiles/orchestrator/scripts/morning_digest.py`
- `/home/ubuntu/.hermes/profiles/ned/scripts/finalize_task.sh`

## Recommendation

No code change to `morning_digest.py` is needed. Preserve `In Review`, clean stale active routing labels, and leave the issue for peer/human review.
