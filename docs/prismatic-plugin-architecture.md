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
