# Evidence-cited journal recap contract

**Status:** Canonical
**Owner:** Prismatic Engine journal subsystem
**Implementation:** `prismatic/journal.py`

## Purpose

Daily and weekly recaps are bounded operational-memory artifacts. They are not an unbounded copy of the event index and they do not treat historical cron outcomes as current scheduler health.

## Rendering contract

- Recaps require a timezone-aware `now` and use the requested deterministic daily or weekly UTC window. Naive datetimes fail closed.
- Accepted events are sorted by normalized UTC `_timestamp`, full citation ID, and canonical event content before at most the latest 50 are rendered.
- Every rendered event claim includes its unambiguous full `[E:<64-character-ID>]` citation.
- The same ordered full citation IDs are written to the adjacent `.citations.json` manifest.
- A valid stored citation ID is 64 lowercase hexadecimal characters. Missing or malformed IDs are replaced with a deterministic SHA-256 ID derived from normalized event content.
- Every dynamic rendered field is processed through the configured secret-pattern redactor, including common unquoted, single-quoted, and double-quoted credential values, then bounded: event type to 80 characters, event detail to 180, scheduler name to 120, and scheduler status to 80. Newlines are flattened before interpolation.
- Current scheduler health is rendered in a separate section and is not inferred from historical cron events.
- Quiet windows explicitly report that no normalized events were accepted.

## Operational bounds

```text
MAX_RECAP_EVENTS=50
MAX_RECAP_BYTES=32768
MAX_RECAP_MANIFEST_BYTES=8192
```

`max_events` is an integer in the inclusive range 1–50. Callers cannot disable or widen the global cap. If the recap or citation manifest exceeds its byte bound, generation fails before creating the recap directory or either output artifact.

## Output artifacts

For `<period>-<window-start>` the generator writes:

- `recaps/<stem>.md` — deterministic rendered recap;
- `recaps/<stem>.citations.json` — period, source-event count, rendered-claim count, and full citation IDs.

The compact return object contains paths, source/rendered counts, recap bytes, citation-manifest bytes, period, and quiet-window state. Full citation lists are intentionally excluded from the return object and remain in the manifest.

The `recaps` output path must be a real directory. Generation opens it with no-follow semantics, verifies its device/inode identity, and binds writes through that open directory descriptor; a pre-existing `recaps` symlink is rejected before artifact writes.

Both payloads are staged in one hidden transaction directory before installation. Existing recap/manifest pairs are backed up during replacement. Ordinary staged-write or rename failures restore the previous pair and remove the transaction directory. If rollback itself fails, generation raises a distinct error and preserves the hidden transaction directory with any recoverable backups instead of deleting the only recovery copy. This is in-process exception recovery, not a claim of crash-atomic multi-file filesystem transactions.

## Synthesis boundary

Optional LLM synthesis may use only claims backed by citation IDs in the deterministic draft. The deterministic draft and manifest remain the evidence authority; synthesis is a derived view.

## Non-claims

- A recap is not live scheduler authority.
- A historical failed cron event does not mean a currently recovered job is failing.
- The 50-event cap does not claim all source events were rendered; `source_event_count` and `rendered_claim_count` preserve that distinction.