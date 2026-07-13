# Public architecture overview

Prismatic Engine is a local-first control plane for AI-agent work. The public architecture is intentionally small and inspectable.

## Core loop

```text
Operator / API / Dashboard
        │
        ▼
Gateway + CLI
        │
        ▼
Plugin catalog + loader
        │
        ▼
Governance + policy decision
        │
        ▼
Durable job + audit events
        │
        ▼
Artifact/provenance registry
```

## Major modules

| Module | Responsibility |
|---|---|
| `prismatic/cli/__init__.py` | Local CLI commands and diagnostics. |
| `prismatic/gateway/server.py` | FastAPI app, plugin APIs, dashboard route. |
| `prismatic/core/registry.py` | Plugin loading and capability validation. |
| `prismatic/plugin_architecture.py` | Manifest parsing, catalog, blueprints, scaffold helpers. |
| `prismatic/plugin_jobs.py` | Durable plugin jobs and audit trail. |
| `prismatic/plugin_artifacts.py` | Universal artifact/provenance records. |
| `prismatic/plugin_policy.py` | Stable allow/needs_approval/block policy decisions. |
| `prismatic/quality/` | Verification, plugin load gate, smoke helpers. |

## Plugin lifecycle

1. A plugin declares a `plugin-manifest.yaml`.
2. The catalog validates readiness, risk, capabilities, and governance fields.
3. The load gate imports shipped plugins and verifies required capabilities.
4. Job requests pass through policy preview and start enforcement.
5. Artifacts are registered with provenance.
6. Publish/export attempts require approval and provenance.
7. Dashboard/API surfaces show jobs, artifacts, policy decisions, and blockers.

## State files

By default, local state may live under `./prismatic_state` when configured through `.env.example`:

```text
plugin_jobs.json
plugin_artifacts.json
event_log.sqlite
curator.sqlite
digests/
```

These are runtime artifacts and should not be committed.
