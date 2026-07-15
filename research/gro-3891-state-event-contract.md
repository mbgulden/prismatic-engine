# Prismatic Engine — Unified App State and Event Contract Design Spec
**Document version:** 1.0.0  
**Date:** July 15, 2026  
**Issue Reference:** GRO-3891  
**Lanes Governance:** Written under AGY lane design prefix (`design/`)

---

## 1. Frame

- **Decision Under Design:** Define a single unified state and event contract vocabulary across the Prismatic Engine Core (PE Core), Gateway APIs, dashboard UI, and external adapters (e.g. Telegram/CLI).
- **Target Audience & Impact:** Developers (`agent:ned`, `agent:fred`), quality reviewers (`agent:agy`, `agent:jules`), and human operators (Michael Gulden). If wrong, state drift or private side-channel state will lead to UI dead-ends, silent failures, and audit discrepancies.
- **Time Horizon:** Long-term core design (durable across the life of Prismatic Engine 1.x and 2.x).

---

## 2. Constraints

- **Hard Invariants:**
  1. **Zero Secret Leaks:** No raw credentials, tokens, or private keys may ever enter logs, state registries, or event schemas. All credentials must be env-variable names only.
  2. **Continuity of Artifacts:** Disconnecting a plugin must never delete its generated artifacts, provenance records, or audit history from the central store.
  3. **Local-First Safety:** Hashing of files must be restricted to absolute paths under the repository root, the PE state directory, or `/tmp` to prevent directory traversal.
  4. **Strict Schema Gating:** No event may be published to the SQLite event bus without passing the canonical event schema check.

- **Soft Preferences:**
  - Preference for lightweight, stdlib-only representations inside python classes.
  - Consistent naming prefixes across models (e.g. `plugjob_` for jobs, `plugart_` for artifacts, `plugevt_` for audit events).

- **Non-Goals:**
  - Out of scope: Implementing remote database engines (Postgres/Redis). We rely on atomic local JSON files and SQLite.

---

## 3. System Shape

### Core Components

```
                ┌────────────────────────────────────────────────┐
                │             Telegram Bot / CLI (shims)          │
                └───────────────────────┬────────────────────────┘
                                        │ (Reads/Steers)
                                        ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                            Prismatic Gateway / Dashboard                       │
│  ┌───────────────────────┐  ┌───────────────────────┐  ┌────────────────────┐  │
│  │   /api/plugins/jobs   │  │/api/plugins/artifacts │  │/api/audit-events   │  │
│  └───────────┬───────────┘  └───────────┬───────────┘  └─────────┬──────────┘  │
└──────────────┼──────────────────────────┼────────────────────────┼─────────────┘
               │ (Durable States)         │ (Durable Provenance)   │ (SQLite PubSub)
               ▼                          ▼                        ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                             Prismatic Core (PE Core)                           │
│  ┌───────────────────────┐  ┌───────────────────────┐  ┌────────────────────┐  │
│  │    PluginJobStore     │  │  PluginArtifactStore  │  │  Swarm Event Bus   │  │
│  │    (plugin_jobs.json) │  │(plugin_artifacts.json)│  │ (event_log.sqlite) │  │
│  └───────────────────────┘  └───────────────────────┘  └────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────────┘
```

### Transition and Event Flow

1. **Job Request:** The client posts to `/api/plugins/jobs`. The engine creates a `plugjob_<id>` record in `PluginJobStore` with status `queued` (or `needs_approval`).
2. **Policy Evaluation:** The policy engine checks the action. It appends a `policy_checked` event to the job timeline.
3. **Execution & Emission:** When approved and started, status changes to `running`. The running job emits an artifact. It appends an `artifact_emitted` event, which registers a `plugart_<id>` record with detailed provenance.
4. **Publish/Export Gating:** The client attempts to export. The policy engine evaluates the artifact state. Once approved, the artifact transitions to `publish_ready`.
5. **Auditing:** All transitions write to the SQLite `event_log.sqlite` bus, visible on the dashboard and streamable in real-time.

---

## 4. Trade-off Matrix

| Alternative | Pros | Cons | Rejected because… |
|---|---|---|---|
| **A. Ad-hoc/Private Plugin Registries** | Simplifies plugin code; no central schemas. | Disconnect deletes artifacts; dashboard cannot query globally. | Breaks North Star requirement of "disconnect without losing state". |
| **B. Complete SQLite Database Engine** | Relational integrity, complex querying. | High overhead, complex migrations, less portable. | Unnecessary complexity for the current developer-preview local-first milestone. |
| **C. Universal JSON Stores & SQLite Bus (Chosen)** | Portable, atomic writes, persistent history, central audit stream, easy backups. | Requires careful lock file management for concurrent writes. | Matches our constraints of simplicity, local-first operation, and high audibility. |

---

## 5. Contract: State and Event Vocabulary

### 5.1. Plugin & Connection States

| State | Scope | Description |
|---|---|---|
| `ready` | Plugin | Manifest is valid, load gate passed, no production blockers. |
| `warning` | Plugin | Load gate passed but has non-blocking anomalies (e.g. missing endpoints config). |
| `blocked` | Plugin | Hard production blocker (missing files, manifest error, raw secrets detected). |
| `connected` | Connection | Active integration with PE Core; capabilities visible to dashboard/agents. |
| `disconnected` | Connection | Detached from PE Core; capabilities hidden, but job/artifact history preserved. |

### 5.2. Job States

| Status | Type | Description |
|---|---|---|
| `queued` | Non-Terminal | Job is waiting for start signal / execution queue. |
| `running` | Non-Terminal | Agent or task runner is actively executing the job. |
| `needs_approval` | Non-Terminal | Blocked at policy gate; awaits operator approve/reject override. |
| `completed` | Terminal | Job finished successfully. |
| `failed` | Terminal | Job failed with an error, or was blocked by policy. |
| `cancelled` | Terminal | Explicitly cancelled by operator or timeout governor. |
| `rejected` | Terminal | Rejected by operator during the `needs_approval` stage. |

### 5.3. Policy Decisions

| Decision | Meaning | Gateway Response Behavior |
|---|---|---|
| `allow` | Safe to execute | Returns HTTP 200, job/action proceeds immediately. |
| `needs_approval` | Risky/restricted | Enters queue with `needs_approval`, triggers operator notification. |
| `block` | Prohibited/unsafe | Request rejected immediately; job marked `failed` with blockers. |

### 5.4. Approval States (Jobs and Artifacts)

- `not_required`: Low-risk action, no approval gates triggered.
- `pending`: Action is blocked awaiting operator check.
- `approved`: Operator approved; action can proceed.
- `rejected`: Operator rejected; action is terminated.

### 5.5. Artifact & Provenance Fields

Artifacts are registered under `plugart_<id>` with the following contract:

```json
{
  "artifact_id": "plugart_183867cc4b0d4487",
  "asset_id": "pwp-reference-plugjob_071d4182a58a4229",
  "plugin_name": "pwp-design-token-plugin",
  "job_id": "plugjob_071d4182a58a4229",
  "artifact_type": "text/html",
  "mime_type": "text/html",
  "path_or_url": "repo-relative/prismatic_state/pwp/reference_lifecycle/plugjob_071d4182a58a4229.html",
  "sha256": "767392cce5385ef5038417c13fc09bca69c1129e29df3b39214ee1457098cdb3",
  "size_bytes": 348,
  "approval_state": "approved",
  "publish_state": "publish_ready",
  "provenance": {
    "generated_by": "run_pwp_reference_lifecycle",
    "registry": "universal-plugin-artifacts",
    "source_job": "plugjob_071d4182a58a4229",
    "source_plugin": "pwp-design-token-plugin"
  },
  "export_history": [
    {
      "actor": "pwp-reference-demo",
      "allowed": true,
      "note": "Reference export demonstrates approved artifact export history",
      "target": "pwp-reference-demo://dashboard-history",
      "timestamp": "2026-07-15T20:51:41.075110+00:00"
    }
  ]
}
```

### 5.6. Audit Events

| Event Type | Source | Triggers When |
|---|---|---|
| `job_created` | `PluginJobStore` | Job requested and stored. |
| `policy_checked` | `PluginPolicy` | Policy engine evaluates the request or start check. |
| `approval_required`| `PluginPolicy` | Policy determines action needs operator check. |
| `approved` | `Operator` | Operator approves a `needs_approval` job/artifact. |
| `rejected` | `Operator` | Operator rejects a job/artifact. |
| `started` | `PluginJobStore` | Job start is validated and begins running. |
| `artifact_emitted` | `Plugin` | A job produces and registers an output. |
| `completed` | `TaskRunner` | Job finishes successfully. |
| `failed` | `TaskRunner`/`Policy`| Job fails with exception, or gets blocked by policy. |
| `cancelled` | `Operator` | Job is explicitly aborted. |
| `note_added` | `Operator`/`Agent`| Contextual markdown notes added to job/audit trace. |

### 5.7. Golden Flow Loop Steps (the 7-step loop)

| Step | Responsible Agent | Description |
|---|---|---|
| `decompose` | `agent:fred` | Deconstruct issue into tasks and dependencies. |
| `dispatch` | `agent:kai` | Select and assign appropriate builder agent. |
| `execute` | `agent:agy` | Code development and local execution checks. |
| `review` | `agent:jules` | Automated review, lints, and test verification. |
| `feedback` | `agent:fred` | Consolidate review findings back to developer. |
| `refine` | `agent:codex` | Apply feedback and resolve deficiencies. |
| `integrate` | `agent:done` | Merge codebase, record evidence, and resolve issue. |

---

## 6. Risk Register

| Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|
| **State File Corruption** | Low | High | Use atomic write protocol (`tempfile.mkstemp` + `os.replace`) to prevent partial writes. | `agent:fred` |
| **Credential Exposure in Inputs**| Medium | High | Apply recursive `redact_secrets` checks to all logs, events, and job inputs before storage. | `agent:fred` |
| **Path Traversal Attacks** | Low | Critical| Verify absolute paths against allowed roots (repo root, PE state, or `/tmp`) before hashing. | `agent:ned` |
| **Out-of-Order Events** | Low | Medium | Store ISO-8601 UTC timestamps with microsecond resolution for strict ordering. | `agent:agy` |

---

## 7. Scorecard Maturity Matrix

This PR defines the shared vocabulary and review contract for GRO-3891. It is a design/specification deliverable, not fresh end-to-end implementation proof. Current scores therefore stay review-pending until the listed evidence commands are run against the merged contract and attached to the issue/PR.

| ID | Category | Rubric Item | Current Score | Target Score | Evidence | Gap | Blocker | Owner | Next Action |
|:---|:---|:---|:---:|:---:|:---|:---|:---|:---|:---|
| **A6** | Unified State | Durable Job State | **TBD by follow-up verification** | 10 | Required: `PYTHONPATH=. python3 scripts/app_surface_golden_demo.py` or equivalent API proof showing `/api/plugins/jobs` uses this vocabulary. | Contract defined here; fresh API/dashboard proof not attached to this PR. | Pending verification artifact. | `agent:fred` | Run the golden app-surface verifier after merge and attach log path + marker. |
| **A7** | Unified State | Durable Artifact & Provenance | **TBD by follow-up verification** | 10 | Required: proof that `/api/plugins/artifacts` emits `plugart_*` records with the provenance fields in §5.5. | Contract defined here; artifact-store evidence not attached to this PR. | Pending verification artifact. | `agent:fred` | Capture artifact API response and provenance record hash in the closure ledger. |
| **A8** | Unified State | Audit Event Stream | **TBD by follow-up verification** | 10 | Required: event-log/API proof containing the normalized event types in §5.6. | Event vocabulary defined here; event-bus readback not attached to this PR. | Pending verification artifact. | `agent:fred` | Run audit-event readback and attach event types observed. |
| **A9** | Unified State | PWP Reference Lifecycle | **TBD by follow-up verification** | 10 | Required: lifecycle command output with repo-relative log/evidence path. | Lifecycle command claimed by AGY output, but no repo-relative log is attached to this PR. | Pending verification artifact. | `agent:fred` | Re-run lifecycle proof or link existing accepted evidence if already merged. |
| **F2** | Unified State | Dual-Surface Operations | **TBD by follow-up verification** | 10 | Required: `verify_shipped_plugins_load`, public launch smoke, security readiness, release smoke/check logs from current head. | Design vocabulary exists; dual API/dashboard proof is not attached here. | Pending verification artifact. | `agent:fred` | Run public/security/release commands and attach exact markers before closure. |

## 8. Acceptance Boundary and Follow-up Evidence

GRO-3891 acceptance requires the dashboard, API, and docs to share one state/event vocabulary and for lifecycle transitions to be traceable without private side-channel knowledge. This PR satisfies the **contract-definition** part of that requirement by creating the shared vocabulary and scorecard gates. It intentionally does **not** claim implementation closure or 10/10 runtime maturity by itself.

Before GRO-3891 can be marked Done, a reviewer must attach fresh evidence showing the merged contract is exercised by the app surface. Minimum acceptable evidence:

1. A repo-relative or durable artifact path for `scripts/app_surface_golden_demo.py` output containing `APP_SURFACE_GOLDEN_DEMO_OK`.
2. Readback showing job, artifact/provenance, approval, publish/export, and audit-event states use the vocabulary in this document.
3. A clear statement of whether proof is API/TestClient-only or includes live dashboard visual proof.
4. Linear/PR review signoff from Fred or the staging governor; AGY dispatch success alone is not acceptance.
