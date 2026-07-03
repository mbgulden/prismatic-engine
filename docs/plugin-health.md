# Plugin health endpoint

The gateway exposes a lifecycle-aware plugin health route:

```http
GET /api/v1/plugins/{plugin_name}/health
```

The response is derived from `PluginLifecycleSandboxManager` state persisted under
`$PRISMATIC_STATE_DIR/plugin_lifecycle.db`. Telemetry metrics are optional: if the
active telemetry collector exposes `report_plugin_metrics(plugin_name)`, those
values are included; otherwise metric fields default to zero and lifecycle state
remains authoritative.

## Status mapping

| Lifecycle state | HTTP | `status` |
|---|---:|---|
| `RUNNING`, `STARTING` | 200 | `healthy` |
| `STOPPED`, `STOPPING` | 200 | `stopped` |
| `FAILED` | 503 | `unhealthy` |
| `PURGED` | 200 | `removed` |
| not found | 404 | `NOT_FOUND` |

## Example

```json
{
  "status": "healthy",
  "plugin_name": "demo-plugin",
  "state": "RUNNING",
  "container_id": "abc123",
  "runtime": "gvisor",
  "uptime_seconds": 12.4,
  "last_error": "",
  "metrics": {
    "total_starts": 0,
    "total_crashes": 0,
    "avg_execution_time_ms": 0.0,
    "avg_memory_bytes": 0,
    "avg_cpu_seconds": 0.0
  },
  "timestamp": "2026-07-03T00:00:00+00:00"
}
```
