# Label debt audit scorecard

`prismatic.label_debt` measures the gap between Linear issues that are merely
agent-labeled and issues that are actually launchable by the dispatcher.

## What it reports

The scorecard returns JSON with:

- `labeled_work`: issues carrying any `agent:*` label.
- `launchable_work`: agent-labeled issues that also carry `dispatch:ready` and
  are not completed, blocked, duplicate/stale-clone, archived, or review noise.
- `label_to_launchable_gap`: labeled work minus launchable work.
- cleanup action counts for dispatch backfill, duplicate cancellation, review
  quarantine, archived-leftover parking, and blocked-work triage.

## CLI usage

```bash
python3 -m prismatic.label_debt issues.json --pretty
```

`issues.json` can be a raw list, `{ "issues": [...] }`, `{ "nodes": [...] }`,
or a Linear-style `{ "data": { "issues": { "nodes": [...] } } }` object.

The analyzer is intentionally side-effect free. It emits recommended actions;
callers decide whether to apply them through Linear mutations after the report is
reviewed.

## Action vocabulary

- `backfill_dispatch_ready`: active legitimate work has an `agent:*` label but no
  `dispatch:ready` label.
- `cancel_duplicate_or_stale_clone`: duplicate/stale clone should be canceled and
  removed from dispatch labels.
- `quarantine_noise`: stale review/post-publish noise should be moved to
  `queue:quarantine` and removed from `dispatch:ready`.
- `park_archived_leftover`: done or archived work still has dispatch labels and
  should be parked.
- `mark_requires_triage`: blocked/manual/credential-gated work should not count as
  launchable capacity until a human clears it.
