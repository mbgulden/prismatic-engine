# Curator Streams 1+2

The curator now separates event classification from task handoff.

## Stream 1: tagging

`CuratorLane.tick_tagging()` reads new rows from the bus database, classifies each
bus event with `tag_event()`, and persists the result in `tagged_events`.

Tagging never calls the supervisor. That keeps event classification responsive
when task dispatch is slow, queued, or temporarily unavailable.

## Stream 2: dispatch

`CuratorLane.tick_dispatch()` reads durable `delegate` rows where
`dispatched = 0`, reloads the original bus payload, decides the lane/model, and
hands the task to the bounded supervisor.

Rows are marked dispatched only after supervisor handoff returns `spawned` or
`queued`. Failed/budget-blocked dispatches remain pending so a later cycle can
retry them.

## Schema

`tagged_events` includes two dispatch-state columns:

```text
dispatched INTEGER DEFAULT 0
dispatched_at REAL
```

`init_curator_db()` adds these columns to existing SQLite files if they are
missing.

## Event taxonomy additions

- GitHub pull request `opened`, `reopened`, and `synchronize` events delegate to
  the `jules` lane for review. The gateway publishes the `X-GitHub-Event`
  header as the bus topic, so PR webhooks arrive as `pull_request` rather than
  the action-only topic `opened`.
- EventBus-wrapped GitHub payloads are unwrapped before taxonomy checks, so
  durable rows shaped as `{type, source, payload}` classify the same way as
  direct webhook payloads.
- GitHub PR dispatch uses a stable handoff identifier: `pull_request.html_url`
  first, then `repository.full_name#number`, and only falls back to topic when
  PR metadata is unavailable.
- `webhook.auth_failed` escalates.
- `webhook.ping` drops.
- `agent_failed` preserves a lane hint from `dispatcher:<lane>`, `lane`, or
  `agent` payload fields when available.

## Gateway auth-failure events

The gateway publishes redacted `webhook.auth_failed` events when GitHub or Linear
HMAC verification fails. The payload includes only:

```json
{"status": "auth-failed"}
```

Signatures and secrets are deliberately not published to the event bus.

## Safety notes

Pending dispatch rows are correlated with bus events by `event_rowid` using
parameterized SQLite queries. The implementation does not build `ATTACH` SQL
from environment-controlled database paths.
