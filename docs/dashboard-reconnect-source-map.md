# Dashboard Reconnect Source Map

Generated: 2026-07-17
Branch: `feature/fred-dashboard-reconnect-source-audit`
Marker: `DASHBOARD_RECONNECT_SOURCE_AUDIT_OK`

## Scope

This is the execution-sized source map for reconnecting dashboard-adjacent work without losing the durable Prismatic Hub Dashboard shell.

Rules followed:

- No merge/reset was performed.
- Work was done in a clean worktree: `/home/ubuntu/work/prismatic-dashboard-reconnect-audit`.
- Runtime/main anchors were diffed before source mining.
- Dirty source worktrees were inspected as evidence only, not treated as clean integration sources.
- Archive/cleanup sources were not opened because A/B/C sources already identified the next candidate.

## Anchor result

| Anchor | Path | Branch | Head | Dirty | Result |
|---|---|---|---|---:|---|
| Runtime canonical | `/home/ubuntu/.prismatic/runtime/prismatic-engine` | `main` | `1510880` | no | Same dashboard/server content as clean reconnect branch. |
| Clean reconnect worktree | `/home/ubuntu/work/prismatic-dashboard-reconnect-audit` | `feature/fred-dashboard-reconnect-source-audit` | `15108802` | no | Clean branch from `origin/main`; safe audit surface. |
| Active prior AGY branch | `/home/ubuntu/work/prismatic-engine` | `feature/fred-agy-autopilot-result-packets` | `79277e43` | no | Left untouched; no AGY work merged/reset. |
| Warm cache | `/home/ubuntu/work/agy_warm_cache/prismatic-engine` | `main` | `a8003e5` | no | Older dashboard shell with `mockAgents`, `mockWorkspaces`, and `mockSignals`; not a shell donor. |

Important finding: current durable runtime/main already contains the restored dashboard shell markers:

```text
Prismatic Hub Dashboard
workspace-tree-mobile-responsive
dashboard-tabs-mobile-wrap
dashboard-header-mobile-wrap
/api/gateway/agents/status
/api/gateway/timeline
/api/workspaces
/api/workspace-tree/preview
```

So the reconnect task should **not** replace the shell. It should mine named missing adapters/panels one at a time.

## A-source findings

| Source | Branch | Dirty | Finding | Action |
|---|---|---:|---|---|
| `/home/ubuntu/work/prismatic-hub-ui` | `feature/prismatic-hub-live-snapshot` | yes | Contains portable Hermes dashboard plugin prototypes: Prismatic Hub, Workspace Tree, Locks, MCP Controller, Orchestrator Deck, Activity Stream, Swarm Manager, GPU Monitor. Several are static/mock plugin prototypes. | Use as UX/component inspiration only. Do not port plugin bundle wholesale. |
| `/home/ubuntu/work/prismatic-engine-site` | `ned/fix-gitignore-env-GRO-1786` | yes | Contains lock dashboard plugin and command/MCP plugin work, but no canonical gateway dashboard/server files. | Mine lock-dashboard behavior only if Resources panel needs lock rows. |
| `/home/ubuntu/work/prismatic-web-plugin` | `feature/gro-2311` | no | PWP distillation package, not dashboard shell work. | Defer unless PWP tab has a named adapter gap. |
| Other A rows | mixed | mostly clean | PRISMATIC_ENGINE.yaml or unrelated project governance docs only. | No immediate dashboard integration target. |

Plugin notes from `prismatic-hub-ui`:

| Plugin | Value | Risk |
|---|---|---|
| `hermes-plugin-prismatic-hub` | Shows dashboard/workspaces/skills/signals tab concept. | Uses fake static sample rows such as `Completed UI mockup`; do not port data model. |
| `hermes-plugin-workspace-tree-navigator` | Workspace Tree UX reference. | Current durable dashboard already has in-tab Workspace Tree/deep link support. |
| `hermes-plugin-lock-dashboard` | Lock rows/stale-lock UX could feed Resources panel. | Fetches hardcoded `http://127.0.0.1:8098/...`; needs gateway adapter before use. |
| `hermes-plugin-realtime-activity-stream` | Activity stream visual reference. | Current durable dashboard already uses `/api/gateway/timeline`; use only for UX polish. |
| `hermes-plugin-swarm-manager` | Large swarm UI prototype. | Static/mock-heavy; fallback only. |

## B-source findings

| Source | Branch | Dirty | Finding | Action |
|---|---|---:|---|---|
| `/home/ubuntu/work/prismatic-engine` | `feature/fred-agy-autopilot-result-packets` | no | Separate AGY packet work. Dashboard/runtime files match current durable shell. | Leave untouched for AGY autopilot PR. |
| `/home/ubuntu/work/kai-gro-3733-reference-themes` | `content/gro-3733-reference-themes` | no | Older dashboard shell with mock dashboard data; server has some workspace routes. | Not a shell donor; defer. |
| `/home/ubuntu/work/kai-gro-3355-resources-panel` | `kai/gro-3355-resources-panel` | yes | Small, concrete Resources panel slice: renames GCP Quotas → Resources, adds `prismatic/budget_caps.py`, `GET/POST /api/quota/caps`, dispatcher auto-pause guard, focused tests/report. | **Best next integration candidate.** Port as a clean bounded slice after review/rebase. |
| `/home/ubuntu/work/prismatic-pe-native-crons` | `design/GRO-3837` | yes | Heavily polluted with deleted docs/modules/tests and untracked `.venv_dev`; dashboard shell is older mock shell. | Do not integrate. Fallback only for a named Native Crons missing adapter. |
| `/home/ubuntu/work/prismatic-pwp-ubersuggest-auth` | `main` | yes | Much older dashboard shell and PWP/SEO work. | Defer unless PWP/Ubersuggest auth is the named missing tab. |
| `/home/ubuntu/work/aot-governance-watchdog-worktree` | detached | no | Website governance guardrails, not Prismatic dashboard shell. | Defer. |

## Immediate integration candidate: GRO-3355 Resources panel budget caps

Source: `/home/ubuntu/work/kai-gro-3355-resources-panel`

Candidate files:

```text
prismatic/budget_caps.py
tests/test_budget_caps.py
prismatic/gateway/server.py
prismatic/gateway/templates/dashboard.html
prismatic/dispatcher.py
reports/gro-3355-resources-panel-budget-caps-20260716.md
reports/gro-3355-resources-panel-source-audit-20260716.md
```

What it adds:

- `GET /api/quota/caps`
- `POST /api/quota/caps`
- Budget cap JSON persistence helper.
- Resources panel copy and daily cap / auto-pause controls.
- Dispatcher auto-pause guard when operator-saved cap is reached.
- Tests for defaults, persistence, normalization, and guard decision.

Why this is the next clean slice:

- It is small relative to other sources.
- It maps to the existing durable Quota/Resources dashboard area.
- It has its own report and focused verification notes.
- It does not require replacing the dashboard shell.

Risks before porting:

- Source worktree is dirty; review exact diffs and recreate on a clean branch from current `origin/main`.
- Current durable dashboard uses `/api/gateway/quota`; decide whether caps should also expose `/api/gateway/quota/caps` for public dashboard consistency.
- Dispatcher guard touches runtime behavior; keep it audit-safe and covered by tests before merge.
- Dashboard HTML from source is older than durable shell; port only the Resources controls, not the surrounding shell.

## Non-candidates for first pass

| Source | Why not first |
|---|---|
| `prismatic-hub-ui` | Valuable UX/plugin reference, but dashboard data is static/mock-heavy and not directly API-backed. |
| `agy_warm_cache/prismatic-engine` | Older shell with mock dashboard data; current runtime supersedes it. |
| `prismatic-pe-native-crons` | Dirty/polluted with broad deletions and venv artifacts. |
| `prismatic-pwp-ubersuggest-auth` | Older shell; inspect only when PWP/Ubersuggest is named. |
| `worktree-cleanup-*` archives | Not needed yet; fallback evidence only. |

## Exact next command plan

When resuming implementation, do this as a separate clean slice:

```bash
cd /home/ubuntu/work/prismatic-engine
git fetch origin --quiet
git switch -C feature/fred-resources-budget-caps origin/main
node /home/ubuntu/.antigravity/swarm.js lock prismatic/budget_caps.py fred
node /home/ubuntu/.antigravity/swarm.js lock tests/test_budget_caps.py fred
node /home/ubuntu/.antigravity/swarm.js lock prismatic/gateway/server.py fred
node /home/ubuntu/.antigravity/swarm.js lock prismatic/gateway/templates/dashboard.html fred
node /home/ubuntu/.antigravity/swarm.js lock prismatic/dispatcher.py fred
```

Then port one file/path at a time from `/home/ubuntu/work/kai-gro-3355-resources-panel`, preserving the current durable dashboard shell and public `/api/gateway/...` conventions.

Focused verification for that next slice should include:

```text
python3 -m py_compile prismatic/budget_caps.py prismatic/gateway/server.py prismatic/dispatcher.py
python3 -m pytest -q tests/test_budget_caps.py tests/test_dispatcher_activation.py
node --check /tmp/hermes-dashboard-inline-resources.js
GET /api/quota/caps -> 200
POST /api/quota/caps -> 200 with temp HOME/state override or explicit test store
public /dashboard -> 200
public /api/gateway/quota -> 200
no shell replacement / no mock regression
```

## Closeout markers

```text
DASHBOARD_RECONNECT_SOURCE_AUDIT_OK
NEXT_INTEGRATION_CANDIDATE=GRO-3355 Resources panel budget caps
NOT_CLAIMING=resources_budget_caps_merged,dashboard_shell_changed,archive_sources_exhausted,agy_autopilot_phase2_resumed
```
