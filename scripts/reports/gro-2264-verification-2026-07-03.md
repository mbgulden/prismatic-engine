# GRO-2264 verification — Morning Digest silent cron

**Verified by:** Ned  
**Verified at:** 2026-07-03T03:57:18Z  
**Issue:** GRO-2264 — `[CRON-FIX] Silent failure: Morning Digest — Linear + dispatch + AGY status to Telegram`

## Verdict

The prescribed silent-cron fix is already present and the job is no longer hard-failing. This is a verification-only closure pass.

## Evidence gathered today

### Cron job state

Inspected `/home/ubuntu/.hermes/profiles/orchestrator/cron/jobs.json` for job `47b66f4df172`:

- Name: `Morning Digest — Linear + dispatch + AGY status to Telegram`
- Enabled: `true`
- Last run: `2026-07-02T07:00:38.785000-06:00`
- Last status: `ok`
- Last error: `null`
- Last delivery error: `null`
- Script: `morning_digest.py`
- Deliver mode: `local`

### Scheduled-run artifact

Latest output artifact exists and is non-empty:

- `/home/ubuntu/.hermes/profiles/orchestrator/cron/output/47b66f4df172/2026-07-02_07-00-38.md`
- Size: 783 bytes
- Contents include a valid digest body:
  - `☀️ *Morning digest — 2026-07-02 13:00*`
  - `📦 Dedup store: 110 linear`
  - `🤖 AGY idle`
  - `📋 Linear activity (last 12h, 44 changed)`

### Live rerun without Telegram side effect

Imported `/home/ubuntu/.hermes/profiles/orchestrator/scripts/morning_digest.py`, set `TOKEN_TELEGRAM=None`, and executed `main()` so the digest rendered locally without posting to Telegram.

Result:

- Verification timestamp: `2026-07-03T03:57:18.606582+00:00`
- Return code: `0`
- Rendered digest contained:
  - `📦 Dedup store: 112 linear`
  - `🤖 AGY idle`
  - `📋 Linear activity (last 12h, 49 changed)`
  - Todo entries included the rerouted issues `GRO-2278`, `GRO-2307`, and `GRO-324`

### Defensive handling in script

Inspected `/home/ubuntu/.hermes/profiles/orchestrator/scripts/morning_digest.py`:

- `fetch_recently_changed()` catches `urllib.error.HTTPError` and `json.JSONDecodeError`, logs a warning, and returns `[]` instead of crashing.
- `categorize()` handles missing state with `Unknown`.
- Todo label extraction uses `it.get('labels', {}).get('nodes', [])`, so missing label payloads do not crash the digest.
- Telegram send retries plain text if Markdown parsing returns HTTP 400.

## Remaining gap

None for GRO-2264's stated acceptance criteria. The cron has successful scheduled artifacts and a fresh local run returned 0.

## Linear action taken

Recommended/expected Linear transition: move GRO-2264 to **In Review** and remove stale `agent:needs-human-review` abandonment residue. Human can mark Done after review.
