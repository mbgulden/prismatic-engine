# PWP ↔ Prismatic Engine additive plugin integration

PWP is the **Portable Website Plugin**. It is not PE core and should not become a hidden fork of PE behavior. PE owns orchestration, lifecycle, dashboard, scheduling, state, and governance. PWP contributes additive website-production capabilities through a clean plugin contract.

## Golden contract

```text
PE core stays foundational.
PWP remains distinct and removable.
PWP gives PE more capabilities, visibility, workflows, and governance.
Connect/disconnect is explicit, reversible, and dashboard-visible.
Agents discover PWP capabilities through PE APIs/CLI, not tribal memory.
```

## Connect points

| Connect point | Owner | Purpose |
|---|---|---|
| `plugins/pwp/plugin-manifest.yaml` | PWP | Declares plugin metadata, capabilities, governance, connect/disconnect semantics. |
| `plugins/pwp/plugin.py` | PWP | Implements plugin class, tools, and capability/connection contracts. |
| `scripts/pwp` | PWP/PE bridge | Repo-local CLI for credential, theme, and integration operations. |
| `prismatic/pwp_integration.py` | PE | Reads PWP manifest/files, stores connection state, exposes capability/governance status. |
| `/api/pwp/status` | PE Gateway | Dashboard/agents read additive PWP capabilities and blockers. |
| `/api/pwp/connect` | PE Gateway | Marks PWP connected when hard blockers are clear. |
| `/api/pwp/disconnect` | PE Gateway | Marks PWP disconnected without deleting plugin files or artifacts. |
| Dashboard `PWP Plugin` tab | PE Gateway | Operator-visible state, blockers, tools, workflows, connect/disconnect controls. |

## Disconnect behavior

Disconnecting PWP must be safe:

- It must not delete plugin code.
- It must not delete generated site/theme artifacts.
- It must not stop PE queues, native crons, or foundational dashboards.
- It marks the additive PWP capability surface as disconnected so agents/operators do not assume PWP governance is active.

## Additive capabilities

PWP currently contributes three capability groups:

1. **Portable website/theme system**
   - schema-backed PWP themes/modules/tokens
   - theme validation
   - theme diff / compatibility checks
   - template rendering

2. **Credential provider bridge**
   - secret-redacted provider status
   - refresh-token rotation through repo-owned PWP provider code
   - PE-native cron compatibility via `scripts/pwp credentials ...`

3. **Visual/governance augmentation**
   - PWP visual QA workflows
   - cultural diacritics/search compatibility policies
   - page/site governance checks that enhance PE output without entering PE core

## CLI

```bash
python3 scripts/pwp integration status
python3 scripts/pwp integration connect
python3 scripts/pwp integration disconnect
python3 scripts/pwp integration refresh
```

Existing PWP commands remain available:

```bash
python3 scripts/pwp credentials status ubersuggest --verify
python3 scripts/pwp credentials refresh ubersuggest
python3 scripts/pwp theme validate <theme> --json
python3 scripts/pwp theme diff --from <old> --to <new> --engine-version <semver> --json
python3 scripts/pwp theme check-compat <theme> --engine-version <semver> --json
```

## Dashboard operator flow

1. Open **PWP Plugin** tab.
2. Review state, blockers, capabilities, tools, workflows, connect points, and disconnect points.
3. Click **Connect** only when hard blockers are clear.
4. Click **Disconnect** to remove PWP readiness from the operator surface without removing PWP files.
5. Click **Refresh** after manifest/schema/provider changes.

## Production readiness checklist

- [x] PWP has a manifest with capability metadata.
- [x] PWP exposes plugin methods for capability and connection contracts.
- [x] PE has a state-backed PWP integration module.
- [x] PE exposes PWP status/connect/disconnect/refresh API endpoints.
- [x] PE Dashboard has a PWP tab with blockers, capabilities, tools, and controls.
- [x] PWP CLI has integration status/connect/disconnect/refresh commands.
- [x] Tests cover PWP state transitions, API endpoints, manifest contract, CLI contract, and dashboard markers.
- [ ] Real site-side PWP visual-governance modules should be added per project as they mature.
- [ ] Additional credential providers should be added using the same redaction/provider pattern as Ubersuggest.

## Governance rules

- Never expose token material in PWP status, dashboard, logs, or agent outputs.
- PWP may add capabilities; it should not silently override PE core orchestration.
- PWP state belongs under `PRISMATIC_STATE_DIR`, not inside plugin source.
- PWP generated website artifacts must remain explicit repo artifacts, not hidden global state.
- If PWP is disconnected, agents should still be able to run PE core workflows, but should not claim PWP-specific governance is active.
