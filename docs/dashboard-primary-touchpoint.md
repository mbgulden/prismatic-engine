# Dashboard-primary touchpoint

## Position

The current working operator flow is Telegram/headless: Michael gives direction in chat, agents call tools, commands run in the background, and verification is reported back into Telegram.

That flow is valuable, but it is not the desired final user experience.

The intended product direction is:

```text
Dashboard first for normal users.
Telegram/headless for notifications, urgent steering, and mobile continuity.
CLI/scripts for development, automation, and recovery.
```

A user should eventually be able to do most day-to-day Prismatic work from the dashboard as the main or only touchpoint.

## Principle

Every user-facing command, tool, and function should have one core implementation and multiple surfaces:

```text
PE Core API / command registry
        │
        ├── Dashboard control
        ├── Telegram action / notification
        ├── CLI command
        └── Agent/tool call
```

The dashboard must not become a separate implementation. It should call the same Gateway/API/command surfaces that Telegram, CLI, and agents use.

## What already exists

The dashboard already has the beginnings of the operator console:

| Existing dashboard area | Backing surface |
|---|---|
| Plugin catalog/governance cards | `/api/plugins/governance` |
| Policy summary and latest decision | `/api/plugins/policy/preview`, job/artifact policy state |
| Durable job summary/table | `/api/plugins/jobs` |
| Job timeline | plugin job events |
| Artifact inventory/table | `/api/plugins/artifacts` |
| Audit event stream | `/api/plugins/audit-events` |
| Approval controls | job/artifact approve/reject APIs |
| PWP lifecycle history | `/api/pwp/status` |
| PWP lifecycle demo action | `/api/pwp/lifecycle-demo` |

That means the infrastructure/workflow exists for a dashboard-first model, even where documentation and polish still need to catch up.

## Target dashboard user journey

A non-technical user should be able to:

1. Open the dashboard.
2. See system readiness and plugin health.
3. Connect or inspect a plugin.
4. Start a safe job from a guided form.
5. Preview the policy decision before submitting.
6. Watch job progress and audit events.
7. Review artifacts/provenance.
8. Approve/reject publish/export actions.
9. Copy/share the resulting artifact/report/evidence.
10. Disconnect a plugin without losing artifacts/history.

No shell command should be required for that normal path.

## Current vs target surface map

| User intent | Current reliable surface | Target primary surface | Notes |
|---|---|---|---|
| Check plugin catalog | CLI/API/dashboard | Dashboard | Already close. |
| Validate plugin manifests | CLI | Dashboard + CLI | Dashboard should expose validation result and remediation hints. |
| Run public smoke | CLI | Dashboard button + CLI | Button should launch a governed diagnostic job. |
| Run security readiness audit | CLI | Dashboard button + CLI | Keep local-only warning visible. |
| Create plugin job | API/agent | Dashboard form | Needs guided schema/action forms. |
| Preview policy | API/dashboard display | Dashboard inline preview | Should run before submit/approval. |
| Approve/reject | Dashboard/API | Dashboard | Already basic; needs richer context. |
| Review artifacts | Dashboard/API | Dashboard | Needs detail page and previews. |
| Inspect audit history | Dashboard/API | Dashboard | Needs filters, pagination, and export. |
| Run PWP lifecycle demo | CLI/API/dashboard | Dashboard | Already wired. |
| Receive urgent alert | Telegram | Telegram + dashboard notification center | Telegram remains best for lock-screen alerts. |
| Steer running agent | Telegram/headless | Dashboard command panel + Telegram fallback | Dashboard should become durable steering console. |

## Dashboard command center model

The dashboard should eventually expose a command center with validated actions, not raw shell execution.

Example command card shape:

```text
Action: Public Launch Smoke
Risk: low
Inputs: none
Policy: allow
Runs as: diagnostic job
Outputs: smoke result artifact + audit events
Surfaces: dashboard result, Telegram optional summary
```

Example plugin action shape:

```text
Action: Export Media Artifact
Risk: medium/high
Inputs: artifact_id, target_format, destination
Policy: needs_approval
Outputs: export artifact + provenance update
Surfaces: artifact detail page, audit event stream, optional Telegram approval
```

## Do not expose raw shell as the product

The dashboard should not become a web terminal. For safety and clarity:

- no arbitrary shell textbox for normal users
- no direct secret entry into manifests
- no destructive action without policy preview
- no publish/export without approval and provenance
- no hidden state changes outside audit events

Development/recovery shell access can remain a maintainer tool, but user-facing dashboard actions should be validated RPC/API operations.

## OKF documentation gap closure

The infrastructure/workflows that exist today should have dashboard-readable OKF documentation and evidence surfaces.

Minimum dashboard-visible OKF fields:

| Field | Meaning |
|---|---|
| Objective | What user/business outcome this capability serves. |
| Key result | How we know it worked. |
| Function | The specific tool/API/job/plugin action. |
| Evidence | Test output, artifact, audit event, PR, or report. |
| Owner | PE Core, plugin, or operator. |
| Risk | low/medium/high and policy gate. |
| Current surface | dashboard/API/CLI/Telegram. |
| Target surface | dashboard-first, Telegram fallback, CLI/dev-only. |

For plugin/public-launch work, the OKF map should reference:

- `docs/north-star.md`
- `docs/public-launch.md`
- `docs/prismatic-plugin-architecture.md`
- `docs/pwp-reference-lifecycle.md`
- `docs/public-security-readiness.md`
- `scripts/public_launch_smoke.py`
- `scripts/public_security_readiness_audit.py`
- `scripts/release_smoke.py`

## Dashboard-first OKF map

| Objective | Key result | Function/workflow | Current surface | Target surface |
|---|---|---|---|---|
| Public local launch works | `PUBLIC_LAUNCH_SMOKE_OK` | public launch smoke | CLI | Dashboard diagnostic job + CLI |
| Plugins are attachable/governed | 5 shipped plugins load; governance shows ready/warning/blockers | plugin catalog/governance | API/dashboard/CLI | Dashboard |
| Risky actions are gated | rejected jobs cannot start; approvals required where needed | policy preview/start/export | API/tests/dashboard summary | Dashboard policy preview before submit |
| Work is auditable | job/artifact audit events visible | audit event stream | API/dashboard | Dashboard with filters/export |
| Artifacts survive disconnect | PWP lifecycle preserves artifacts/history | PWP reference lifecycle | CLI/API/dashboard | Dashboard guided demo/reference |
| Media plugins can be added safely | media blueprint governance smoke passes | media blueprint validation | CLI/docs | Dashboard plugin developer checklist |
| Business plugins can be added safely | business blueprint pack exists with policy presets | business plugin blueprints | docs/API | Dashboard plugin developer checklist |
| Mobile users can steer when away | urgent approvals/status available | Telegram bridge | Telegram | Telegram notification + dashboard source of truth |

## Migration path from Telegram/headless to dashboard-first

| Phase | User experience | Implementation expectation |
|---|---|---|
| 0 — Current | Michael directs agents in Telegram; agents run tools/commands. | Headless workflow works and reports evidence. |
| 1 — Mirror | Dashboard shows the same jobs/artifacts/audit events created by headless runs. | No duplicated logic; dashboard reads PE Core state. |
| 2 — Trigger | Dashboard can trigger common diagnostics and plugin jobs. | Dashboard posts to Gateway APIs that create governed jobs. |
| 3 — Govern | Dashboard policy preview and approval flows become standard before risky actions. | Policy engine is mandatory for UI actions. |
| 4 — Operate | Most user workflows can be completed in dashboard without shell/Telegram. | Forms, detail views, filters, evidence panels, notification center. |
| 5 — Notify | Telegram becomes optional notification/steering layer. | Dashboard remains source of truth; Telegram links back to dashboard records. |

## Dashboard surfaces still needed

To reach dashboard-first, add:

- plugin/job/artifact detail routes or stable deep links
- job creation forms generated from plugin action schemas
- policy preview panels before submit
- audit event filters, pagination, and export
- diagnostic job runner for public/security/release smokes
- OKF/evidence board showing objective → key result → function → evidence
- notification center with Telegram delivery status
- plugin developer checklist pages for blueprint readiness
- media/business plugin readiness dashboards

## Acceptance criteria for dashboard-primary maturity

The dashboard-primary milestone is done when a user can, without shell access:

1. Run public launch diagnostics.
2. Inspect plugin catalog/governance.
3. Create a safe plugin job.
4. Preview policy for a risky job.
5. Approve or reject a job/artifact.
6. Review artifact provenance.
7. Export/publish only after approval.
8. Inspect audit history with filters.
9. Run the PWP reference lifecycle demo.
10. See OKF/evidence status for the above.

Shell/CLI should remain available for maintainers, CI, and recovery, but not be required for the normal user journey.
