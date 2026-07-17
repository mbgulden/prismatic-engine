# AGY Completed-Work Ingestion Design Stub

Marker: `AGY_COMPLETED_WORK_INGESTION_DESIGN_READY_OK`

This is the Phase 2 design stub for the AGY autopilot lane. It is intentionally not a bulk dispatcher and does not enable auto-merge.

## Goal

Separate completed AGY outputs from the original dispatch queue so Michael/Fred can review useful work without losing provenance.

```text
Linear/dashboard task
→ durable task queue
→ assigned-agent resolver
→ AGY preflight
→ one scoped AGY run
→ AGY result packet
→ completed-work ingestion
→ merge-readiness classifier
→ clean PR helper
→ verification gate
→ dashboard + Linear writeback
```

## Initial storage shape

Use JSONL first, SQLite later if query volume requires it.

Default path:

```text
~/.prismatic/agy_completed_work.jsonl
```

Each line should contain:

```json
{
  "ingested_at": "2026-07-17T00:00:00Z",
  "packet_hash": "sha256:...",
  "packet": {"marker": "AGY_TASK_RESULT_PACKET_OK"},
  "classification": {
    "lane": "blocked|merge-ready|clean-rebuild|manual-review|superseded",
    "reasons": []
  },
  "artifact_index": [
    {"path": "/tmp/agy/GRO-3837/result.json", "exists": true, "sha256": "..."}
  ],
  "linear_writeback": {
    "status": "accepted|rejected|pending",
    "comment_url": null
  }
}
```

## Proposed files for implementation slice

| File | Purpose |
|---|---|
| `prismatic/agy_completed_work.py` | Durable JSONL store, packet hashing, artifact indexing, list/filter helpers. |
| `scripts/ingest_agy_result.py` | CLI entrypoint: validate packet, index artifacts, append completed-work item, print compact result. |
| `prismatic/gateway/server.py` | `GET /api/agy/completed-work` and `POST /api/agy/completed-work/ingest`. |
| `prismatic/gateway/templates/dashboard.html` | Real “AGY Completed Work” table sourced from API; no mock rows. |
| `tests/test_agy_completed_work.py` | Store/API-adjacent unit tests with temp JSONL path. |

## API contract draft

### `GET /api/agy/completed-work`

Returns the newest completed-work items.

```json
{
  "ok": true,
  "source": "agy_completed_work.jsonl",
  "items": [],
  "count": 0
}
```

### `POST /api/agy/completed-work/ingest`

Accepts an AGY result packet JSON body or a packet path. It must validate the packet before appending.

```json
{
  "ok": true,
  "accepted": true,
  "issue_identifier": "GRO-3837",
  "packet_hash": "sha256:...",
  "marker": "AGY_COMPLETED_WORK_INGESTION_OK"
}
```

Invalid packets must return a rejected response and record a Linear writeback candidate, not silently disappear.

## Dashboard contract draft

Dashboard table columns:

| Column | Source |
|---|---|
| Issue | `packet.issue_identifier` |
| Branch | `packet.branch` |
| PR | `packet.pr_url` |
| Merge readiness | classifier result |
| Verification | `packet.verification.result` + log path |
| Risk | `packet.risk_level` |
| Next action | `packet.next_action` |
| Artifacts | artifact count / links |

No mock rows. Empty state should say:

```text
No AGY completed-work packets ingested yet.
```

## Safety rules

- Do not ingest prose-only AGY results.
- Do not ingest if `AGY_TASK_RESULT_PACKET_OK` is missing.
- Do not auto-create PRs during ingestion.
- Do not auto-merge during ingestion.
- Do not mark dashboard rows as merge-ready unless classifier later approves.
- Do not treat docs-only packets as runtime enforcement.
- Do not claim production deployment unless public/runtime proof is linked.

## Phase 2 exit criteria

The implementation slice earns `AGY_COMPLETED_WORK_INGESTION_OK` only when:

- valid result packet is saved durably;
- invalid packet is rejected with reasons;
- artifact paths are indexed;
- `GET /api/agy/completed-work` returns real persisted rows;
- dashboard renders the real API response or honest empty state;
- Linear writeback candidate is emitted;
- focused verifier proves all of the above.

This first PR only earns the design-ready marker:

```text
AGY_COMPLETED_WORK_INGESTION_DESIGN_READY_OK
```
