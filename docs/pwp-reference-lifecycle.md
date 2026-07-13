# PWP reference lifecycle

PWP is the canonical public reference plugin for Prismatic Engine's full plugin lifecycle. It demonstrates the reusable contract future plugins should follow:

```text
connect → run job → produce artifact → register artifact/provenance → show dashboard history → require approval before publish/export → disconnect safely
```

The demo is local-only and credential-free. It writes a tiny HTML artifact under the configured PE state directory and records job/artifact state through PE Core's universal registries.

## Run the demo

```bash
python scripts/pwp lifecycle demo
```

Expected payload highlights:

- `ok: true`
- a `plugjob_*` job in the universal plugin job registry
- a `plugart_*` artifact in the universal artifact/provenance registry
- `approval_before_publish.blocked_before_approval: true`
- final artifact state `approval_state: approved` and `publish_state: publish_ready`
- final connection state `disconnected`
- artifact remains queryable after disconnect

To leave PWP connected after the demo:

```bash
python scripts/pwp lifecycle demo --keep-connected
```

## API path

```bash
curl -s -X POST http://127.0.0.1:9000/api/pwp/lifecycle-demo \
  -H 'Content-Type: application/json' \
  -d '{"actor":"operator","disconnect_after":true}'
```

Read status/history afterward:

```bash
curl -s http://127.0.0.1:9000/api/pwp/status
curl -s 'http://127.0.0.1:9000/api/plugins/jobs?plugin_name=pwp-design-token-plugin'
curl -s 'http://127.0.0.1:9000/api/plugins/artifacts?plugin_name=pwp-design-token-plugin'
```

## Dashboard proof

The PWP dashboard tab includes:

- `Run Lifecycle Demo` action
- `pwp-lifecycle-history`
- lifecycle detail panel
- job and artifact counts from `lifecycle_summary`
- proof chips for job registry, provenance, approval before publish, and safe disconnect

## Contract for future plugins

Future public plugins should use the same pattern:

1. Explicit connect endpoint/state.
2. Durable job creation through the universal plugin job registry.
3. Artifact emission through `artifact_emitted` or the universal artifact registry.
4. Provenance fields identifying source plugin, source job, generator, and provider/service.
5. Approval gate before publish/export.
6. Dashboard-visible job/artifact history.
7. Safe disconnect that never deletes artifacts or core PE state.
