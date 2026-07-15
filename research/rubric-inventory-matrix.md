# Prismatic Engine — Rubric 10/10 Inventory & Scoring Rules
**Date:** July 15, 2026  
**Document version:** 1.0.0  
**Target Issue:** GRO-3837  
**Parent Epic:** [Rubric 10/10][Epic] PE scorecard baseline + evidence ledger  

---

## 1. Scoring Rules and Thresholds

To establish a measurable, objective scoring framework across all Prismatic Engine capabilities, the system uses a **0 to 10 scale**. This scale corresponds directly to the three visual statuses (Green, Yellow, Red) shown on the dashboard:

| Score | Status | Meaning | Action / Requirements |
|:---:|:---|:---|:---|
| **9 - 10** | **Green** | **Production-grade / Standard** | Fully implemented, automated end-to-end, passes all test gates, documented in docs/code, verified under a CLI/API/dashboard smoke check, and active in daily operations. |
| **7 - 8** | **Green/Yellow** | **Development Complete** | Code is fully functional and passes unit tests, but has minor UX polish, missing edge-case validation, or slight documentation gaps. |
| **5 - 6** | **Yellow** | **Partial Implementation** | The capability works in a CLI context or local script but lacks integration with the Gateway/API or the Dashboard UI. |
| **3 - 4** | **Yellow/Red** | **Blueprint / Design Only** | Manifest templates and architecture designs exist under `docs/plugin-blueprints/`, but no loadable code or engine-enforced rules exist yet. |
| **1 - 2** | **Red** | **Acknowledged / Backlogged** | Requirement or check is identified in a spec or ticket, but no blueprints, models, or code stubs have been written. |
| **0** | **Red** | **Blind Spot** | Capability or risk is not considered or documented in the system. |

### Scoring Matrix Alignment Rules
- A rubric dimension is considered **complete** only when it scores **9/10 or 10/10 (Green)**.
- Any score **8/10 or below** must map to a concrete Linear issue or child task under the parent scorecard epic.
- All proof checks must run against the local-first repository environment using non-credentialed mock fixtures or local database states.

---

## 2. Complete Rubric Matrix

This matrix covers all required rubric criteria from `docs/north-star.md`, `docs/public-launch.md`, `docs/dashboard-primary-touchpoint.md`, `docs/pwp-reference-lifecycle.md`, and `docs/prismatic-plugin-architecture.md`.

### Section A: Public Launch & Local Setup
*Focuses on onboarding developers, quickstarts, and engine bootstrap operations.*

| # | Rubric Item / Objective | 10/10 Definition (Green) | Proof Surface | Current Evidence Path / Command | Owner Lane |
|:---|:---|:---|:---|:---|:---|
| **A1** | **Local Installation** | Clean local clone, virtual environment creation, and `pip install -e ".[gateway]"` installs all core and API dependencies without conflict. | `pyproject.toml`, `setup-dual-venvs.sh`, `scripts/public_launch_smoke.py` | `PYTHONPATH=. ./.venv_dev/bin/python3 scripts/public_launch_smoke.py` (specifically imports step) | `agent:fred` |
| **A2** | **Dashboard Startup** | Gateway runs locally on port 9000 and serves dashboard assets cleanly with no HTTP/JS errors or missing resources. | `/dashboard`, `scripts/public_launch_smoke.py` ("Gateway API smoke" step) | `prismatic-gateway --host 127.0.0.1 --port 9000` & `python3 scripts/dashboard_visual_qa.py` | `agent:fred` |
| **A3** | **Shipped Plugins Load** | All default plugins in `plugins/` (including PWP) load successfully through the loader, verifying manifest schema compatibility. | `/api/plugins/catalog`, `prismatic/core/registry.py` | `plugin-load-gate` or `python3 -m prismatic.quality.plugin_load` | `agent:ned` |
| **A4** | **Governance Summary Visibility** | The gateway exposes a detailed operator checklist count (risk, permissions, blockers) for each plugin, rendered on dashboard cards. | `/api/plugins/governance`, `dashboard.html` (`plugin-dashboard-health-cards` marker) | GET `/api/plugins/governance` | `agent:fred` |
| **A5** | **Policy Gate Visibility** | Gateway exposes inline policy decisions, allowing users to preview evaluation results (allow/block/needs_approval) for plugin jobs. | `/api/plugins/policy/preview`, dashboard policy cards | POST `/api/plugins/policy/preview` | `agent:fred` |
| **A6** | **Durable Job State** | Job registry persists job history, lifecycle phases, inputs, and errors to a localized, atomic JSON file. | `/api/plugins/jobs`, `$PRISMATIC_PLUGIN_JOBS_STATE` | GET `/api/plugins/jobs` & check `prismatic_state/plugin_jobs.json` | `agent:ned` |
| **A7** | **Durable Artifact & Provenance** | Emitted plugin outputs are registered with prompts, file SHA256 hashes, sizes, and provider details, surviving plugin disconnect. | `/api/plugins/artifacts`, `$PRISMATIC_PLUGIN_ARTIFACTS_STATE` | GET `/api/plugins/artifacts` & check `prismatic_state/plugin_artifacts.json` | `agent:ned` |
| **A8** | **Audit Event Stream** | All execution milestones (job creation, status transitions, policy preview, operator approval) log to a central normalized log. | `/api/plugins/audit-events`, dashboard logs | GET `/api/plugins/audit-events` | `agent:fred` |
| **A9** | **PWP Reference Lifecycle** | PWP reference plugin executes all steps: connect -> job queue -> policy evaluation -> approval -> artifact generation -> safe disconnect. | `docs/pwp-reference-lifecycle.md`, `/api/pwp/status` | `python3 scripts/pwp lifecycle demo` | `agent:ned` |
| **A10**| **Security Assumptions Audit** | Checks ensure zero hardcoded secrets exist, CORS settings are restricted, path traversal is blocked, and inputs are redacted. | `docs/security.md`, `scripts/public_security_readiness_audit.py` | `python3 scripts/public_security_readiness_audit.py` | `agent:fred` |

---

### Section B: Dashboard Primary Touchpoint
*Focuses on transition from Telegram-first/headless commands to validated UI controls.*

| # | Rubric Item / Objective | 10/10 Definition (Green) | Proof Surface | Current Evidence Path / Command | Owner Lane |
|:---|:---|:---|:---|:---|:---|
| **B1** | **No Raw Shell Access** | UI restricts arbitrary terminal box inputs, ensuring all operations are validated Gateway API calls. | Dashboard HTML templates, API endpoints | `python3 scripts/public_launch_smoke.py` ("dashboard markers") | `agent:fred` |
| **B2** | **Guided Job Creation** | Job forms are dynamically generated from plugin schemas, ensuring input validations and token redactions occur before posting. | `/api/plugins/jobs`, dashboard form views | GET `/api/plugins/catalog` (checking action schemas) | `agent:ned` |
| **B3** | **Interactive Approvals** | Dashboard displays approval context (policy reason, risk, target) and exposes button overrides for pending jobs. | `/api/plugins/jobs/{id}/approve`, dashboard controls | POST `/api/plugins/jobs/{job_id}/approve` | `agent:fred` |
| **B4** | **Artifact Publish/Export Gating** | Export and publication actions remain blocked in UI and API until approval states are satisfied and logged. | `/api/plugins/artifacts/{id}/publish-ready` | POST `/api/plugins/artifacts/{artifact_id}/publish-ready` | `agent:fred` |
| **B5** | **Audit Log Filters & Pagination** | Audit dashboard supports querying and filtering logs by plugin, job, artifact type, or severity with no UI freezes. | `/api/plugins/audit-events`, UI log viewer | GET `/api/plugins/audit-events?plugin_name=pwp-design-token-plugin` | `agent:fred` |
| **B6** | **Interactive Smoke Runner** | Diagnostic triggers (public smoke, security audit) can be launched from the dashboard with live terminal/log streaming. | Dashboard diagnostics panel | POST `/api/plugins/jobs` with diagnostic action | `agent:fred` |

---

### Section C: Plugin Ecosystem & Maturity Ladder
*Focuses on manifest contracts, class designs, and custom capabilities.*

| # | Rubric Item / Objective | 10/10 Definition (Green) | Proof Surface | Current Evidence Path / Command | Owner Lane |
|:---|:---|:---|:---|:---|:---|
| **C1** | **Plugin Base Interface Compliance** | Plugins implement the base `PrismaticPlugin` class and support optional discovery hooks without crashing old loaders. | `prismatic/interface/plugin.py`, `/api/plugins/catalog` | `plugin-load-gate` verification | `agent:fred` |
| **C2** | **Manifest Capability Map** | Every plugin declares a `plugin-manifest.yaml` specifying categories, required capabilities, endpoints, and governance rules. | Manifest files under `plugins/` | `python3 scripts/plugin_architecture validate <manifest_path>` | `agent:ned` |
| **C3** | **MCP Integration & Auth Redaction** | MCP servers are registered HTTP/stdio sidecars with redacted credentials and structured resource descriptions. | Manifest `mcp_servers`, catalog payload | GET `/api/plugins/catalog` checking MCP fields | `agent:ned` |
| **C4** | **API Route Registry** | Plugin manifests declare custom routes that the gateway exposes, matching the plugin's internal service endpoints. | Manifest `endpoints`, `/api/plugins/architecture` | GET `/api/plugins/architecture` | `agent:ned` |

---

### Section D: Media Plugin Readiness
*Focuses on creative-media capabilities, mesh files, image providers, and cost gates.*

| # | Rubric Item / Objective | 10/10 Definition (Green) | Proof Surface | Current Evidence Path / Command | Owner Lane |
|:---|:---|:---|:---|:---|:---|
| **D1** | **Media Class Validation** | Media blueprints validate against standard types (`video`, `images`, `music-sfx`, `game-assets`, `asset-forge-3d`). | `docs/plugin-blueprints/`, blueprint catalog | `python3 scripts/plugin_architecture blueprint prismatic-video --class video` | `agent:ned` |
| **D2** | **Structured Governance Preset** | Manifest contains detailed risk profiles, required approval gates, cost limits, and audit logs. | Manifest governance rules, API validation | GET `/api/plugins/governance` (verifying media blueprints) | `agent:fred` |
| **D3** | **Prompt & Model Provenance** | Media artifact registry details prompts, provider models, and source assets, allowing trace-back of generation. | Universal artifact store payloads | GET `/api/plugins/artifacts` returning media properties | `agent:ned` |
| **D4** | **Cost & Provider Policies** | Budget/token limits are evaluated before sending prompts to external APIs; jobs are blocked if quotas are exceeded. | `prismatic/plugin_policy.py` rules | POST `/api/plugins/policy/preview` with high-cost mockup | `agent:fred` |

---

### Section E: Business Plugin Readiness
*Focuses on operations, CRM, booking systems, billing, and PII handling.*

| # | Rubric Item / Objective | 10/10 Definition (Green) | Proof Surface | Current Evidence Path / Command | Owner Lane |
|:---|:---|:---|:---|:---|:---|
| **E1** | **Business Category Scope** | Plugins identify with classes (`seo-ops`, `booking-ops`, `crm`, `billing`, `business-intelligence`) and speak business terms. | Manifest categories, API catalog | `python3 scripts/plugin_architecture catalog` (filter category) | `agent:ned` |
| **E2** | **PII / Payment Security Gating** | Security gates identify and block PII/payment data exposure in logs, requiring double-token approvals for updates. | `prismatic/plugin_policy.py` checks | Job request with mock customer payload checks | `agent:fred` |
| **E3** | **Disconnect Data Continuity** | Business plugins disconnect without removing generated reports, customer lists, or invoices from the database. | Universal artifact/jobs stores | PWP lifecycle disconnect validation | `agent:fred` |

---

### Section F: Golden Flow & Mobile Continuity
*Focuses on multi-agent collaboration, mobile alerts, and Telegram continuity.*

| # | Rubric Item / Objective | 10/10 Definition (Green) | Proof Surface | Current Evidence Path / Command | Owner Lane |
|:---|:---|:---|:---|:---|:---|
| **F1** | **Multi-Agent Workspace Sync** | Code changes and workspace handoffs occur under lane governance without merge conflicts or lease breaches. | `PRISMATIC_ENGINE.yaml` lane assignments | `python3 scripts/pre-push-hook.py` & `git status` check | `agent:fred` |
| **F2** | **Dual-Surface Operations** | Dashboard serves as the central data model while Telegram serves status updates, alarms, and quick mobile decisions. | Telegram bot adapter, Gateway events | Verification of notification payload delivery | `agent:fred` |

---

## 3. Evidence of Local Execution & Verification

To satisfy the 10/10 rubric closure requirement, both local diagnostic suites were executed successfully under the python environment:

1. **Public Launch Smoke Test:**
   - **Command:** `PYTHONPATH=. ./.venv_dev/bin/python3 scripts/public_launch_smoke.py`
   - **Result:** Complete verification of core imports, CLI help Positional args, Plugin Catalog, Shipped Plugins Load Gate, Gateway API, Public Docs existence, and Dashboard HTML markers.
   - **Marker Output:** `PUBLIC_LAUNCH_SMOKE_OK`

2. **Public Security Readiness Audit:**
   - **Command:** `PYTHONPATH=. ./.venv_dev/bin/python3 scripts/public_security_readiness_audit.py`
   - **Result:** Complete verification of zero raw credentials, `.env.example` configurations, restricted CORS settings, policy secrets redaction, and local path traversal protection.
   - **Marker Output:** `PUBLIC_SECURITY_READINESS_OK`

3. **Release Smoke Audit:**
   - **Command:** `PYTHONPATH=. ./.venv_dev/bin/python3 scripts/release_smoke.py`
   - **Result:** Runs and verifies project entrypoints, plugins loading, launch smoke, security audit, and dashboard visual QA.
   - **Marker Output:** `RELEASE_SMOKE_OK`
