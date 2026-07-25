# Prismatic Engine North Star

**Status:** Canonical
**Owner:** Prismatic Engine maintainers
**Governing principle:** **Don’t trust, Verify.**

Prismatic treats generation as a proposal and verified evidence as completion. The engine must guide work with executable constraints, independently verify exact artifacts and state transitions, solve findings without weakening gates, and preserve enough knowledge for future humans and agents to understand why the system exists and how it remains safe.

## One-sentence North Star

Prismatic Engine lets a user install the engine, get immediate value, attach governed capabilities as needed, operate them visibly from the dashboard, and detach them without losing state, artifacts, provenance, or audit history.

## Why this document exists

Prismatic has two historical threads that both matter:

1. **Phone-first Golden Flow v0** — Telegram/Linear/AGY/GitHub/Jules proved the original operator dream: useful autonomous work controlled from a phone.
2. **Public plugin-governed engine** — the current public-launch work proves the reusable platform spine: plugin catalog, governance, policy, jobs, artifacts, audit events, dashboard visibility, PWP reference lifecycle, and safe public-local onboarding.

This document reconciles those threads so future work does not lose the plot.

## Current public-launch milestone

The current public milestone is **public local-launch / developer preview**, not hosted multi-user SaaS.

A fresh external user should be able to:

```bash
git clone https://github.com/mbgulden/prismatic-engine.git
cd prismatic-engine
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[gateway]"
cp .env.example .env
python scripts/public_launch_smoke.py
```

Expected marker:

```text
PUBLIC_LAUNCH_SMOKE_OK
```

The milestone is complete when the public path proves:

| Capability | Proof surface |
|---|---|
| install/run locally | `README.md`, `docs/public-launch.md`, `scripts/public_launch_smoke.py` |
| dashboard starts locally | `prismatic-gateway --host 127.0.0.1 --port 9000` |
| shipped plugins load | plugin load gate + `/api/plugins/catalog` |
| governance visible | `/api/plugins/governance` + dashboard Plugins tab |
| policy gates visible | `/api/plugins/policy/preview` + dashboard policy card |
| jobs durable | `/api/plugins/jobs` |
| artifacts/provenance durable | `/api/plugins/artifacts` |
| audit events visible | `/api/plugins/audit-events` |
| PWP full lifecycle demonstrated | `docs/pwp-reference-lifecycle.md`, `/api/pwp/status`, `scripts/pwp lifecycle demo` |
| public security assumptions documented | `docs/security.md`, `docs/public-security-readiness.md`, `scripts/public_security_readiness_audit.py` |

## Dashboard as the intended primary touchpoint

Today's working workflow is Telegram/headless: Michael can steer the engine through messages, and agents can run commands/tools/functions behind the scenes. That is valid and important for mobile continuity.

The product direction is different:

```text
Telegram/headless = current operator bridge and notification channel.
Dashboard = intended main user touchpoint for most commands, tools, functions, approvals, visibility, and governance.
```

In the desired end state, a normal user should not need to know shell commands for daily operations. The dashboard should make most actions available as validated UI controls backed by the same PE Core APIs that agents and CLI commands use.

Canonical unattended AGY execution follows that rule today: `prismatic agy` is a headless facade over `AGYCLIHarness`, durable admission/run/artifact receipts, and independent verification—not a competing source of truth. A future dashboard control must call the same harness contract and project the same lifecycle states. See `docs/contracts/canonical-agy-cli-workflow.md`.

Dashboard-first does **not** mean removing Telegram. Telegram remains useful for:

- lock-screen notifications
- urgent approvals
- quick status checks
- human steering when away from the dashboard
- compact summaries of completed or blocked runs

The dashboard should become the authoritative operator view, backed by durable systems of record, for:

- plugin catalog and connection state
- job creation and lifecycle control
- policy preview and approval/rejection decisions
- artifact/provenance review
- audit event history
- PWP/media/business plugin operations
- OKF status and evidence views
- troubleshooting, smoke checks, and readiness reports

See [`dashboard-primary-touchpoint.md`](dashboard-primary-touchpoint.md) for the target interaction model.

## Plugin ecosystem maturity ladder

| Level | Name | Definition | Required proof |
|---:|---|---|---|
| 0 | Idea | Capability is discussed but has no manifest. | Architecture note only. |
| 1 | Blueprint | Manifest lives under `docs/plugin-blueprints/` and does not load as a shipped plugin. | `scripts/plugin_architecture validate ...` passes. |
| 2 | Structured blueprint | Blueprint declares structured governance, risk, approval gates, policy checks, artifact types, dashboard/API surfaces, and provenance expectations. | Blueprint governance smoke passes. |
| 3 | Loadable plugin | Plugin lives under `plugins/`, imports cleanly, and passes the load gate. | `plugin-load-gate` / `verify_shipped_plugins_load()` passes. |
| 4 | Governed plugin | Jobs, policy decisions, artifacts, provenance, approvals, and audit events use PE Core generic stores. | `/api/plugins/governance`, `/jobs`, `/artifacts`, `/audit-events` show activity. |
| 5 | Dashboard-operable plugin | A user can operate normal plugin actions from the dashboard without shell commands. | Dashboard controls and smoke markers exist. |
| 6 | Reference plugin | Plugin demonstrates connect → job → policy → artifact/provenance → approval/export → audit history → safe disconnect. | PWP currently holds this role. |
| 7 | Public plugin | Docs, security assumptions, support boundaries, install path, and tests are sufficient for external users. | Public smoke + security + release smoke pass. |

## Media plugin readiness checklist

A media plugin is not ready to become live until it satisfies this checklist:

- [ ] Manifest has `plugin_type` and a media/plugin class (`video`, `images`, `music-sfx`, `game-assets`, or `asset-forge-3d`).
- [ ] Structured governance declares `risk_level`, `readiness_state`, `approval_gates`, `policy_checks`, `provenance_required`, and `audit_events`.
- [ ] Artifact types are declared, including generated media and job metadata formats.
- [ ] Provenance records include model/provider, prompt or input summary, source asset IDs, generated artifact IDs, and export target.
- [ ] Costly/batch/external-provider actions require policy evaluation and approval when appropriate.
- [ ] Publish/export actions are blocked until approval and provenance are present.
- [ ] Dashboard surfaces show provider health, jobs, artifact library, approval state, and audit history.
- [ ] Secrets are env-var names only; no raw provider credentials appear in manifests/docs/tests.
- [ ] The plugin remains removable without deleting generated artifacts or history.
- [ ] Blueprint validation, plugin load gate, public smoke, and relevant dashboard smoke all pass.

Recommended next media slice:

```text
Media Blueprint Governance Upgrade
```

That slice should upgrade all existing media blueprints to the structured governance schema and add a smoke check proving those fields exist.

## Business plugin readiness checklist

A business plugin is not ready to become live until it satisfies this checklist:

- [ ] Plugin class is explicitly identified (`seo-ops`, `booking-ops`, `crm`, `billing`, `business-intelligence`, `email-marketing`, or another documented business class).
- [ ] Structured governance declares risk, approval gates, policy checks, and audit events.
- [ ] PII/customer-data/payment/public-send/public-publish risks are explicitly modeled.
- [ ] Read-only actions are separated from write/send/export/destructive actions.
- [ ] Customer, booking, invoice, report, or campaign artifacts use durable IDs and provenance.
- [ ] External system IDs are recorded without storing raw secrets.
- [ ] Actions that modify external systems require approval and audit events.
- [ ] Dashboard labels speak business language, not only generic jobs/artifacts.
- [ ] Security docs state production/auth/CORS/data-retention assumptions.
- [ ] The plugin can disconnect without deleting business records, reports, or audit history.

Recommended next business slice:

```text
Business Plugin Blueprint Pack
```

That slice should add first-class blueprints for `seo-ops`, `booking-ops`, and `business-intelligence` before higher-risk CRM/billing write plugins.

## Golden Flow status

The original Golden Flow v0 remains valuable, but it is now a **separate milestone** from public plugin launch.

Original Golden Flow:

```text
Telegram → Linear issue → AGY session → live progress → GitHub PR → Jules review → Linear update → schedule event → phone-visible completion
```

Current plugin/public-launch milestone:

```text
clone → install → dashboard/API → plugin catalog → governance/policy → PWP lifecycle → jobs/artifacts/audit → public smoke/security/release checks
```

These are complementary:

- **Golden Flow** proves phone-first autonomous work execution.
- **Plugin public launch** proves reusable governed capability attachment.
- **Dashboard primary touchpoint** is the product bridge between them.

Future Golden Flow work should use the dashboard as the main durable surface and Telegram as notification/steering, not as the only interface.

## Current North Star sequence

1. **Complete public-local plugin launch** — done enough for developer preview; keep smokes green.
2. **Consolidate North Star and rubric docs** — this document.
3. **Upgrade media blueprints to structured governance** — next slice.
4. **Add business plugin blueprint pack** — next after media governance.
5. **Make dashboard the primary touchpoint** — progressively move shell/Telegram-only actions into validated dashboard controls.
6. **Revisit Golden Flow as a dashboard-visible, phone-notified milestone** — do not collapse it into plugin launch.

## Non-negotiables

- PE Core owns lifecycle, governance, policy, state, dashboard/API, and auditability.
- Plugins own domain-specific implementation.
- Dashboard actions and headless/Telegram commands must call the same core APIs.
- No raw secrets in manifests, docs, tests, or fixtures.
- Incomplete future plugins stay under `docs/plugin-blueprints/`, not `plugins/`.
- Disconnecting a plugin never deletes artifacts, provenance, or audit history.
- Public launch claims require code, docs, tests, API/CLI/dashboard proof, and real verification output.
