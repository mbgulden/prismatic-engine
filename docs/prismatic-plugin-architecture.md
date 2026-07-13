# Prismatic Engine Core Plugin Architecture

This is the canonical path for building plugins that are **distinct from PE Core** but fully integrated for AI-agent automation, dashboard visibility, asset governance, API access, and optional MCP service exposure.

It covers current plugins such as PWP and future families:

- Prismatic Video
- Prismatic Images
- Prismatic Music/SFX
- Prismatic Game Assets
- Asset Forge 3D — an external app, website, and service that integrates deeply into PE for AI automation

## Golden rule

```text
PE Core owns orchestration, lifecycle, gateway, dashboard, governance, state, and agent/tool discovery.
Plugins own domain-specific capabilities, services, schemas, assets, MCP servers, provider credentials, and workflows.
A plugin can connect deeply without becoming PE Core.
A plugin can disconnect safely without deleting artifacts or breaking unrelated PE functionality.
```

## Proven plugin path

| Step | Deliverable | PE Core integration point | Proof |
|---|---|---|---|
| 1 | `plugin-manifest.yaml` | Loader, catalog, validation | `scripts/plugin_architecture validate` |
| 2 | `plugin.py` subclassing `PrismaticPlugin` | Loader lifecycle | Plugin load gate |
| 3 | `register_tools()` | Agent automation tools | Loader `registered_tools` |
| 4 | `capability_contract()` | Agents/dashboard capability discovery | `/api/plugins/catalog` |
| 5 | `connection_contract()` | Operator connect/disconnect semantics | Dashboard/API contract |
| 6 | `register_mcp_servers()` | MCP resources/tools for AI automation | Catalog + MCP config export path |
| 7 | `register_api_routes()` / manifest `endpoints` | Gateway/plugin API surface | `/api/plugins/architecture` + plugin endpoints |
| 8 | `register_artifact_types()` / manifest `artifact_types` | Universal asset index/artifact store | Asset provenance checks |
| 9 | Dashboard surface | Operator visibility | Dashboard tab/card/plugin panel |
| 10 | Governance rules | Production safety | Validation + plugin docs + tests |
| 11 | Tests | Regression proof | focused pytest + plugin load gate |

## Core files

| File | Purpose |
|---|---|
| `prismatic/interface/plugin.py` | Official plugin base class and optional discovery hooks. |
| `prismatic/core/registry.py` | Real plugin loader. Validates capabilities, loads plugins, registers tools/MCP/API/artifact/capability surfaces. |
| `prismatic/plugin_architecture.py` | Manifest parser, catalog, validation, future media classes, scaffold generator. |
| `scripts/plugin_architecture` | CLI for catalog, validation, blueprint generation, scaffolding. |
| `prismatic/gateway/server.py` | Core plugin catalog/architecture endpoints. |
| `docs/plugin-blueprints/*` | Non-loading future plugin blueprints for video/images/music/game/Asset Forge 3D. |

## Manifest contract

Minimum live plugin manifest:

```yaml
schema_version: "1.1.0"
name: "prismatic-example"
version: "0.1.0"
description: "Example Prismatic plugin."
plugin_type: "creative-media"
entry_point: "prismatic_example.plugin:PrismaticExamplePlugin"
core_version_constraint: ">=0.2.0, <2.0.0"
```

Media/asset/service plugins must additionally declare:

```yaml
categories:
  - creative-media
  - video
required_capabilities:
  - network
  - filesystem-write
asset_domains:
  - video
artifact_types:
  - video/mp4
integration_points:
  - manifest
  - loader
  - tools
  - api
  - dashboard
  - mcp
  - asset-index
  - artifact-store
  - governance
automation_surfaces:
  - agent-tools
  - gateway-api
  - dashboard
  - native-cron
  - mcp-server
capabilities:
  - id: "video.generation"
    label: "Video generation and management"
    category: video
    tools:
      - generate_video
      - edit_video
mcp_servers:
  - name: "prismatic-video-mcp"
    transport: stdio
    command: "python3 -m prismatic_video.mcp_server"
    resources:
      - video.jobs
      - video.assets
    tools:
      - generate_video
      - edit_video
endpoints:
  - method: GET
    path: /api/plugins/prismatic-video/status
    description: Read plugin readiness.
  - method: POST
    path: /api/plugins/prismatic-video/jobs
    description: Create an AI automation job.
  - method: GET
    path: /api/plugins/prismatic-video/assets
    description: List generated/imported assets.
dashboard_surfaces:
  - video jobs
  - video asset library
governance:
  - secret-redacted status
  - artifact provenance required
  - agent jobs must emit durable asset IDs
connect_points:
  - PluginLoader validates and loads this manifest.
  - Gateway exposes status/job/asset endpoints.
  - Agents use registered tools or MCP tools to automate jobs.
  - Generated assets enter the universal asset index with provenance.
disconnect_points:
  - Disconnect hides readiness/job creation without deleting assets.
  - Core PE queues and unrelated plugins continue operating.
```

## Optional plugin class hooks

Every plugin still subclasses `PrismaticPlugin` and implements:

```python
on_init(context)
register_tools()
```

Future-ready plugins should also implement:

```python
capability_contract()       # machine-readable capability/domain/governance contract
connection_contract()       # connect/disconnect semantics
register_mcp_servers()      # MCP descriptors, resources, tools, redacted auth envs
register_api_routes()       # API descriptors expected/served by plugin
def register_artifact_types() # artifact MIME/types for asset index/provenance
```

These optional hooks are best-effort in the loader so older plugins remain compatible.

## CLI

Print the live plugin catalog:

```bash
python3 scripts/plugin_architecture catalog
```

Validate a manifest:

```bash
python3 scripts/plugin_architecture validate plugins/pwp/plugin-manifest.yaml
```

Print a future plugin manifest blueprint:

```bash
python3 scripts/plugin_architecture blueprint prismatic-video --class video
python3 scripts/plugin_architecture blueprint prismatic-images --class images
python3 scripts/plugin_architecture blueprint prismatic-music-sfx --class music-sfx
python3 scripts/plugin_architecture blueprint prismatic-game-assets --class game-assets
python3 scripts/plugin_architecture blueprint asset-forge-3d --class asset-forge-3d
```

Scaffold a safe, non-live blueprint directory:

```bash
python3 scripts/plugin_architecture scaffold asset-forge-3d --class asset-forge-3d --target-dir docs/plugin-blueprints
```

Move the scaffold under `plugins/` only when its implementation is ready to pass the load gate.

## API endpoints

Core endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /api/plugins/catalog` | Live manifest catalog, validation, capability index, media classes. |
| `GET /api/plugins/architecture` | Canonical architecture, supported future plugin classes, required fields. |
| `GET /api/plugins/governance` | Operator-facing readiness, risk, approval gates, surface coverage, and blocker data. |
| `GET /api/v1/plugins/{plugin_name}/health` | Existing plugin health endpoint. |

Domain plugins may add their own endpoints listed in manifest `endpoints`. Asset Forge 3D should start with:

| Endpoint | Purpose |
|---|---|
| `GET /api/plugins/asset-forge-3d/status` | Service readiness, queue health, redacted auth status. |
| `POST /api/plugins/asset-forge-3d/jobs` | Create 3D asset generation/processing jobs. |
| `GET /api/plugins/asset-forge-3d/jobs/{id}` | Job status/progress/provenance. |
| `GET /api/plugins/asset-forge-3d/assets` | List indexed/generated 3D assets. |
| `POST /api/plugins/asset-forge-3d/assets/{id}/export` | Export to glTF/FBX/Blender/engine formats. |

## Governance/readiness contract

PE Core now computes a governance summary for every plugin manifest and exposes it through both `/api/plugins/catalog` and `/api/plugins/governance`.

Each plugin receives:

- `readiness_state`: `ready`, `warning`, or `blocked`
- `risk_level`: explicit manifest value or derived from plugin type/MCP/service usage
- `permissions`: declared scopes/capabilities
- `approval_gates`: operator approvals required before publish/export/costly/destructive actions
- `policy_checks`: checks such as credential redaction, artifact provenance, rate/cost limits, destructive action approval
- `credential_redaction`: `env-names-only` or `blocked`
- `surface_coverage`: counts for tools, MCP servers, API routes, artifact types, dashboard surfaces, connect/disconnect points
- `production_blockers`: blocking and warning messages suitable for dashboard cards

The dashboard **Plugins** tab renders this as an operator catalog with readiness cards, blocker/warning visualization, risk labels, approval gates, API/MCP/artifact surface coverage, and endpoint chips. The PWP tab remains the reference plugin-specific operator surface and now also exposes the catalog-derived governance contract in `/api/pwp/status`.

Rules:

- Raw token-like values in manifests are blockers. Use env var names only.
- Media/service plugins should declare approval gates.
- Provenance-required plugins must declare artifact types.
- MCP/service plugins should declare dashboard surfaces and auth env var names.
- High-risk plugins should declare policy checks before jobs execute.

## Plugin policy/approval enforcement

PE Core enforces plugin policy in `prismatic/plugin_policy.py`. Policy decisions are stable, machine-readable payloads:

```json
{
  "allowed": true,
  "requires_approval": false,
  "decision": "allow",
  "reason": "safe low-risk queued job",
  "risk_level": "low",
  "blockers": [],
  "warnings": [],
  "approval_reasons": [],
  "checks": [
    {"name": "plugin_known", "status": "passed", "details": {}}
  ],
  "context": {},
  "evaluated_at": "..."
}
```

Allowed `decision` values are:

- `allow`
- `needs_approval`
- `block`

Generic policy APIs:

| Endpoint | Purpose |
|---|---|
| `POST /api/plugins/policy/preview` | Preview a policy decision without mutating durable state. |
| `POST /api/plugins/jobs/{job_id}/start` | Start a job only when policy and approvals allow it. |
| `POST /api/plugins/artifacts/{artifact_id}/export` | Export an artifact only when policy and approvals allow it. |

Job enforcement:

- Unknown plugins are blocked.
- Raw token-like job input is blocked and redacted from policy context.
- Jobs with plugin approval gates or risky actions enter `needs_approval`.
- Rejected, failed, cancelled, or completed jobs cannot start without a future explicit retry path.
- Direct `POST /api/plugins/jobs/{job_id}/status` transitions to `running` call the same policy gate as `/start`.
- Every start attempt records durable audit events: `policy_checked`, `start_allowed`/`start_blocked`, `approval_required` when applicable, and `started` when allowed.

Artifact enforcement:

- Rejected artifacts cannot become publish-ready or export.
- Pending artifacts cannot become publish-ready or export without approval.
- Artifacts missing provenance are blocked from publish-ready/export.
- Approved artifacts with provenance can become publish-ready and export.
- Export and publish-ready attempts append `export_history` records containing `allowed`, `policy_result`, `actor`, target/note, and timestamp.

Conservative defaults require approval for actions containing publish/export/deploy/delete/destroy/write/overwrite/batch/costly/external-service/credentialed/production/public. Plugin manifests and blueprints feed policy via governance fields including `risk_level`, `approval_gates`, `policy_checks`, `production_blockers`, and credential redaction state.

The Dashboard **Plugins** tab shows policy visibility through `plugin-policy-summary`, `plugin-policy-decision`, and `renderPluginPolicy()` with `blocked_reason` markers.

## Universal artifact/provenance registry

PE Core persists full plugin artifact/provenance records in `prismatic/plugin_artifacts.py` using an atomic JSON store at:

```text
$PRISMATIC_PLUGIN_ARTIFACTS_STATE
# default: $PRISMATIC_STATE_DIR/plugin_artifacts.json
# default state dir fallback: ./prismatic_state/plugin_artifacts.json
```

Artifact records support:

- `artifact_id`
- `asset_id`
- `plugin_name`
- `job_id`
- `artifact_type`
- `mime_type`
- `path_or_url`
- `sha256` when a safe local file exists
- `size_bytes` when a safe local file exists
- `metadata`
- `provenance`
- `input_summary`
- `provider_or_service`
- `approval_state`
- `publish_state`
- `export_history`
- `created_at`
- `updated_at`

Generic artifact APIs:

| Endpoint | Purpose |
|---|---|
| `GET /api/plugins/artifacts` | List durable artifact/provenance records and summary counts. |
| `POST /api/plugins/artifacts` | Create an artifact/provenance record. |
| `GET /api/plugins/artifacts/{artifact_id}` | Read artifact detail. |
| `POST /api/plugins/artifacts/{artifact_id}/approve` | Approve an artifact. |
| `POST /api/plugins/artifacts/{artifact_id}/reject` | Reject an artifact and mark it rejected for publishing. |
| `POST /api/plugins/artifacts/{artifact_id}/publish-ready` | Mark an approved/selected artifact as ready to publish/export. |

Safety rules:

- External `http`/`https` URLs are stored as references and are not fetched by default.
- Local file hashing is only allowed for paths under the repo, the configured PE state directory, or `/tmp`.
- Unsafe local paths are preserved as references but are not read or hashed.
- Token-like values in metadata, provenance, and input summaries are redacted.
- Plugin disconnect does not delete artifacts.

Gap 1 integration:

- `POST /api/plugins/jobs/{job_id}/events` with `event_type: artifact_emitted` now creates a universal artifact record and links its `artifact_id` back to the job.
- Job detail hydrates linked artifacts from the universal registry, falling back to the legacy lightweight event reference when needed.
- `GET /api/plugins/governance` includes both `jobs` and `artifacts` summaries.
- The Dashboard **Plugins** tab renders artifact summary cards and a **Universal Plugin Artifacts / Provenance Registry** table.

## Plugin jobs and audit trail

PE Core persists plugin jobs and audit events in `prismatic/plugin_jobs.py` using an atomic JSON store at:

```text
$PRISMATIC_PLUGIN_JOBS_STATE
# default: $PRISMATIC_STATE_DIR/plugin_jobs.json
# default state dir fallback: ./prismatic_state/plugin_jobs.json
```

The durable state owns three top-level buckets:

- `plugin_jobs` — job records and latest lifecycle/approval/policy state
- `plugin_job_events` — append-only per-job audit trail
- `plugin_artifacts` — lightweight artifact references emitted by job events, ready for the fuller provenance registry in Gap 2

Job lifecycle states:

```text
queued
running
needs_approval
completed
failed
cancelled
rejected
```

Audit events:

```text
job_created
policy_checked
approval_required
approved
rejected
started
artifact_emitted
completed
failed
cancelled
note_added
```

Every job records:

- `job_id`
- `plugin_name`
- `action`
- `status`
- `risk_level`
- `approval_required`
- `approval_state`
- `policy_result`
- `input_summary` with token-like values redacted
- `created_by`
- `source`
- `operator_notes`
- `artifact_ids`
- timestamps
- `error`

Generic job APIs:

| Endpoint | Purpose |
|---|---|
| `GET /api/plugins/jobs` | List durable plugin jobs and summary counts. |
| `POST /api/plugins/jobs` | Create a plugin job and run the generic policy gate. |
| `GET /api/plugins/jobs/{job_id}` | Read job detail, audit events, and linked artifacts. |
| `POST /api/plugins/jobs/{job_id}/approve` | Approve a job waiting on operator approval. |
| `POST /api/plugins/jobs/{job_id}/reject` | Reject a job and close its lifecycle. |
| `POST /api/plugins/jobs/{job_id}/events` | Append audit events, including lightweight `artifact_emitted` records. |
| `POST /api/plugins/jobs/{job_id}/status` | Update lifecycle status. |

`GET /api/plugins/governance` includes a `jobs` summary so the governance catalog and dashboard can show whether declared lifecycle/audit contracts are actually being used. The Dashboard **Plugins** tab renders durable job/audit summary cards and recent job rows.

Policy behavior today:

- unknown plugins are blocked
- token-like raw secrets in job input are blocked/redacted
- manifest governance approval gates place jobs into `needs_approval`
- publish/export/deploy/delete/destroy/costly/batch actions require approval
- approval/rejection decisions are durable audit events
- artifact-emitted events create lightweight `plugin_artifacts` records without deleting artifacts on disconnect

## MCP setup pattern

MCP is the right bridge when the plugin is also an external app/service or has rich resource/tool semantics.

### Local stdio MCP plugin

```yaml
mcp_servers:
  - name: prismatic-video-mcp
    transport: stdio
    command: python3 -m prismatic_video.mcp_server
    resources:
      - video.jobs
      - video.assets
    tools:
      - generate_video
      - edit_video
```

### External Asset Forge 3D MCP service

```yaml
external_service:
  name: Asset Forge 3D
  kind: external-app-service
  base_url_env: ASSET_FORGE_3D_BASE_URL
  api_key_env: ASSET_FORGE_3D_API_KEY
  webhook_secret_env: ASSET_FORGE_3D_WEBHOOK_SECRET

mcp_servers:
  - name: asset-forge-3d-mcp
    transport: http
    url: ${ASSET_FORGE_3D_MCP_URL}
    auth_env:
      - ASSET_FORGE_3D_API_KEY
    resources:
      - asset_forge.jobs
      - asset_forge.assets
      - asset_forge.scenes
      - asset_forge.exports
    tools:
      - forge_3d_asset
      - retopologize_mesh
      - bake_textures
      - rig_model
      - export_3d_asset
```

PE stores only env var names and redacted status, never raw secrets.

## Asset Forge 3D integration model

Asset Forge 3D can be its own:

- app
- website
- service/API
- MCP server
- asset storage/indexing backend

and still fully integrate with PE by exposing:

1. manifest in PE plugin format
2. PE gateway endpoints or proxy endpoints
3. MCP server descriptor
4. registered tools for agents
5. artifact types and asset domains
6. webhook/callback events into PE
7. redacted credential/service health
8. durable asset IDs and provenance
9. dashboard surface for jobs/assets/service health

Recommended Asset Forge 3D artifacts:

- `model/gltf-binary`
- `model/gltf+json`
- `application/x-fbx`
- `application/x-blender`
- `application/x-prismatic-asset-forge-job+json`

Recommended durable IDs:

```text
afg_job_<id>
afg_asset_<id>
afg_export_<id>
```

## Future plugin families already proven by blueprints

Blueprint manifests live under `docs/plugin-blueprints/` so they do **not** load as live plugins until moved under `plugins/`.

| Family | Blueprint | Capability class |
|---|---|---|
| Prismatic Video | `docs/plugin-blueprints/prismatic_video/plugin-manifest.yaml` | `video` |
| Prismatic Images | `docs/plugin-blueprints/prismatic_images/plugin-manifest.yaml` | `images` |
| Prismatic Music/SFX | `docs/plugin-blueprints/prismatic_music_sfx/plugin-manifest.yaml` | `music-sfx` |
| Prismatic Game Assets | `docs/plugin-blueprints/prismatic_game_assets/plugin-manifest.yaml` | `game-assets` |
| Asset Forge 3D | `docs/plugin-blueprints/asset_forge_3d/plugin-manifest.yaml` | `asset-forge-3d` |

## Development flow

1. Generate blueprint:

   ```bash
   python3 scripts/plugin_architecture scaffold prismatic-video --class video --target-dir /tmp/plugin-blueprints
   ```

2. Implement `plugin.py` and service/MCP code.
3. Validate manifest:

   ```bash
   python3 scripts/plugin_architecture validate /tmp/plugin-blueprints/prismatic_video/plugin-manifest.yaml
   ```

4. Run load gate after moving under `plugins/`:

   ```bash
   python3 -m prismatic.quality.plugin_load
   ```

5. Add focused tests for:
   - manifest validation
   - loader registration
   - tools
   - MCP descriptors
   - API endpoints
   - dashboard markers
   - asset provenance
   - credential redaction

6. Open PR; CI must pass plugin load gate.
7. Connect the plugin through its status/connect flow.
8. Use `/api/plugins/catalog` to verify PE sees capabilities and surfaces.

## Production checklist

- [ ] Manifest validates with no errors.
- [ ] Plugin load gate passes.
- [ ] `register_tools()` returns agent-usable JSON schema tools.
- [ ] `capability_contract()` exposes domains/tools/governance.
- [ ] `register_mcp_servers()` declares MCP resources/tools when relevant.
- [ ] API endpoints are listed and tested.
- [ ] Dashboard has an operator surface.
- [ ] Assets use durable IDs and provenance.
- [ ] Secrets are env-backed and redacted.
- [ ] Disconnect does not delete assets or break unrelated PE services.
- [ ] Tests prove the path.

## Non-goals

- PE Core should not absorb every domain implementation.
- PE Core should not store external service secrets in manifests.
- Blueprint directories should not be placed under `plugins/` until they pass the live load gate.
