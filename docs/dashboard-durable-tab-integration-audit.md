# Dashboard Durable Tab Integration Audit

Date: 2026-07-16  
Scope: Prismatic Hub Dashboard durable tab integration + mobile Workspaces viewer.  
Runtime checkout: `/home/ubuntu/.prismatic/runtime/prismatic-engine` on clean `main`.  
Production commit verified: `3253e06`.  

## Summary

The current durable dashboard preserves the canonical `Prismatic Hub Dashboard` shell and reconnects the best previously implemented Fred dashboard adapters instead of replacing the dashboard with a fallback shell. The Workspaces tab remains embedded in the main dashboard, supports `/dashboard?file=...` deep links, and the standalone `/workspace-tree?file=...` remains as a legacy/fallback surface.

Verification is ad-hoc targeted plus GitHub CI for the repair PRs, not canonical full-suite green.

## Prior branch/source comparison

Dashboard-relevant branch inspection covered:

- `feature/fred-dashboard-missing-integration-404s` / PR #253
- `feature/fred-dashboard-lock-shape-compat` / PR #254
- `feature/fred-connect-pwp-quota-tabs` / PR #255
- `feature/fred-real-dashboard-adapters` / PR #256
- `feature/fred-real-ingestion-recovery-adapters` / PR #257
- `feature/fred-real-dashboard-tab` / PR #258
- `feature/fred-wire-remaining-dashboard-tabs` / PR #259
- `feature/fred-dashboard-regression-contract` / PR #260
- `feature/fred-governance-control-plane-ux` / PR #264
- `feature/fred-ingestion-attention-deeplink` / PR #265
- `feature/fred-ingestion-queue-operator-semantics` / PR #267
- `feature/fred-ingestion-queue-real-contract` when present
- `feature/fred-dashboard-workspace-tree-main`
- `origin/main`
- `/home/ubuntu/.prismatic/runtime/prismatic-engine`

Best reusable adapter sources came primarily from `feature/fred-dashboard-regression-contract`, plus the later durable Workspace Tree integration from `feature/fred-dashboard-workspace-tree-main` and public gateway alias follow-up.

## Tab/surface audit table

| Tab / surface | Current live status | Best known branch/source | Missing pieces | Action |
|---|---|---|---|---|
| Dashboard overview | Real adapter-backed. Agents load from `/api/gateway/agents/status`; activity loads from `/api/gateway/timeline`; Workspaces summary loads from `/api/workspaces`. | `feature/fred-real-dashboard-tab`, `feature/fred-dashboard-regression-contract` | None known in this slice. | Preserved/ported. |
| Workspaces | Real embedded folder tree + file viewer in main dashboard; `/dashboard?file=...` opens the Workspaces tab and previews the file; standalone `/workspace-tree?file=...` still works. | `feature/fred-dashboard-workspace-tree-main` | None known after mobile fix. | Preserved and mobile-fixed. |
| Governance / control plane | Dashboard shell and plugin governance remain present. Browser actions that would otherwise dispatch/shell are audit-safe and explicit. | `feature/fred-governance-control-plane-ux`, `feature/fred-dashboard-regression-contract` | Some browser control endpoints intentionally return safe `accepted_noop` instead of executing operator actions. This is honest compatibility, not real execution. | Keep as safe no-op until explicit backend action endpoints are approved. |
| Merge backlog | Real status endpoint `/api/gateway/merge/status` backed by `merge_state+governance_triage+merge_control_state`. | `feature/fred-dashboard-regression-contract` | Direct browser action controls are safe/audited, not full merge execution. | Preserve real status; implement real action execution in a separate governed slice only. |
| Ingestion queue | Real queue/status endpoints exposed through `/api/gateway/webhooks/stats`, `/api/gateway/webhooks/queue`, `/api/gateway/dispatcher/status`, `/api/gateway/recovery/status`. | `feature/fred-real-ingestion-recovery-adapters`, `feature/fred-ingestion-queue-operator-semantics`, `feature/fred-dashboard-regression-contract` | Recovery/control buttons may record accepted requests rather than execute irreversible actions. | Preserve real queue status; separate recovery-action execution slice if needed. |
| Recovery | Live recovery status visible through `/api/gateway/recovery/status`; safe operator intent recording remains explicit. | `feature/fred-real-ingestion-recovery-adapters`, `feature/fred-dashboard-regression-contract` | Some controls are `accepted_noop` by design. | Do not relabel noop as real; wire real actions only behind explicit approval. |
| Foundation | Live adapter restored via `/api/gateway/foundation/peer_review`, backed by `run_records+foundation_control_state`. | `feature/fred-dashboard-regression-contract` | None known for read/status view. | Preserved/ported. |
| Native cron | Existing Native Cron tab and routes preserved. | `feature/fred-dashboard-regression-contract`, current main | Not reworked in this slice. Verify separately before claiming scheduler-control completeness. | Preserve; audit control semantics in a separate cron-specific slice if requested. |
| Timeline/runs/signals | Signals tab uses `/api/gateway/timeline?limit=80`, backed by `prismatic.timeline`; run records contribute to agent/timeline state. | `feature/fred-real-dashboard-tab`, `feature/fred-dashboard-regression-contract` | None known for read/status view. | Preserved/ported. |
| Plugins | Plugin governance surface remains mounted and visible. | `feature/fred-governance-control-plane-ux`, current main | Not deeply revalidated beyond tab render/API status in this slice. | Preserve; deeper plugin governance contract can be separate. |
| PWP | PWP plugin tab remains mounted and visible. | `feature/fred-connect-pwp-quota-tabs`, current main | Not deeply revalidated beyond tab render/API status in this slice. | Preserve; deeper PWP contract can be separate. |
| Quota | Dashboard now fetches public-safe `/api/gateway/quota`; local legacy `/api/quota` also exists. Backed by `quota_state.db`. | `feature/fred-connect-pwp-quota-tabs`, PR #289 follow-up | Public `/api/quota` may remain Cloudflare Access challenged; dashboard uses `/api/gateway/quota`. | Preserved through public gateway alias. |
| Skills | Dashboard now fetches public-safe `/api/gateway/skills`, backed by `prismatic.skills`. | `feature/fred-dashboard-regression-contract`, PR #289 follow-up | Public `/api/skills` may remain unavailable; dashboard uses `/api/gateway/skills`. | Preserved through public gateway alias. |

## Remaining mock/sample/no-op surfaces

| Surface | Status | Next action |
|---|---|---|
| `mockAgents`, `mockWorkspaces`, `mockSignals` | Removed from live dashboard render path. | Keep regression checks. |
| Static fake task strings (`Completed UI mockup`, `Watcher daily backup`, `Creating rebase`) | Removed from live dashboard render path. | Keep regression checks. |
| `accepted_noop` control responses | Still present where browser controls should not execute irreversible operator actions yet. | Implement real backend actions only in a separate explicitly approved control/action slice. |
| Public legacy `/api/skills` and `/api/quota` | Not relied on by dashboard. `/api/gateway/skills` and `/api/gateway/quota` are the public dashboard routes. | Optional nginx/Cloudflare cleanup only if legacy public routes are required. |

## Mobile Workspace Tree proof contract

Expected behavior at mobile widths around 390px:

- Workspaces tab stacks folder tree and file viewer vertically instead of forcing side-by-side columns.
- Folder tree and file viewer have independent scrolling.
- File preview uses safe horizontal scrolling for code-like content.
- Selected file name and preview are visible.
- `/dashboard?file=prismatic%2Fgateway%2Fserver.py` opens the Workspaces tab and previews that file.
- `/workspace-tree?file=...` remains available as fallback/legacy.
- Traversal attempts still return `403`.

Implementation marker in dashboard HTML: `workspace-tree-mobile-responsive`.

## Verification evidence

Latest compact production verifier:

```text
COMMAND=py_compile + node --check inline JS + local/public gateway tab API matrix + dashboard/workspace mobile markers
RESULT=PASS
LOG=/tmp/fred-dashboard-durable-tab-integration-production-verify.log
SCOPE=durable dashboard tab adapters and mobile Workspace Tree viewer without replacing canonical dashboard
AD_HOC_OR_CANONICAL=ad-hoc targeted plus CI green for PRs #288 and #289
NOT_CLAIMING=canonical_full_suite_green,agy_completed_work_integration_gate
MARKER=DASHBOARD_DURABLE_TAB_INTEGRATION_AUDIT_OK,DASHBOARD_DURABLE_TAB_INTEGRATION_OK,DASHBOARD_WORKSPACE_TREE_MOBILE_OK
```

Fresh changed-path stale-guard verifier for `dashboard.html`:

```text
CANONICAL_TEST_LINT_BUILD_COMMAND=node --check /tmp/hermes-dashboard-html-inline-stale-guard.js
AD_HOC_VERIFICATION=PASS
changed_paths_checked=/home/ubuntu/work/prismatic-engine/prismatic/gateway/templates/dashboard.html
runtime_head=3253e06
PUBLIC_DASHBOARD=200 text/html; charset=utf-8
PUBLIC_AGENTS_SOURCE=run_records+agent_registry+queue_state+timeline+health_context
PUBLIC_TIMELINE_SOURCE=prismatic.timeline
PUBLIC_SKILLS_SOURCE=prismatic.skills
PUBLIC_QUOTA_SOURCE=quota_state.db
LOCAL_TRAVERSAL=403 application/json
```

## Non-claims

- Not claiming canonical full-suite green.
- Not claiming `AGY_COMPLETED_WORK_INTEGRATION_GATE_OK`.
- Not claiming browser control buttons execute irreversible production actions unless their backend endpoint explicitly does so.
