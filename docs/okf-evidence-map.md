# OKF evidence map

**Status:** Canonical human view
**Owner:** Prismatic Engine maintainers
**Machine-readable registry:** `okf/index.yaml`

The registry is the validation input; this document is the operator-readable explanation. The validator fails on objective-ID or system-of-record drift in the canonical parity table; other semantic discrepancies must be resolved through independent review rather than silently choosing one.

## Purpose

This document closes the OKF documentation gap for Prismatic Engine public-launch and plugin-governance work where the infrastructure already exists but the objective/key-result/function/evidence framing was scattered across docs, scripts, dashboard markers, and audit reports.

OKF here means:

```text
Objective → Key Result → Function → Evidence
```

The goal is to make every real workflow visible, auditable, and eventually operable from the dashboard.

## Canonical registry parity

The validator requires this table to contain every machine-readable objective ID exactly once with a byte-exact matching system of record. Other explanatory tables remain human guidance and require semantic review.

<!-- OKF_REGISTRY_PARITY_BEGIN -->
| Objective ID | System of record |
|---|---|
| `verified-agent-output` | Git object identity + versioned verification receipts + Merge Factory attestations |
| `provider-neutral-verification` | versioned verification receipts + Merge Factory attestations + Git object identity |
| `systemic-orchestration-correctness` | durable state stores and event/lease records |
| `canonical-knowledge` | docs/index.md + okf/index.yaml + accepted ADRs |
| `sustainable-maintainability` | versioned quality, incident, rollback, and evidence metrics |
| `canonical-agy-execution` | canonical AGY manifest + launch/process receipts + exact task and executable digests |
| `authoritative-operator-view` | plugin/job/artifact/audit and orchestration stores |
<!-- OKF_REGISTRY_PARITY_END -->

## Documentation rule

If a workflow exists in PE Core, it should have:

1. a user-facing objective,
2. measurable key result,
3. named function/API/CLI/dashboard surface,
4. evidence artifact or smoke marker,
5. owner boundary,
6. risk/policy note,
7. current surface and target dashboard surface.

## Verification Engine OKF map

| Objective | Key result | Function/workflow | Evidence | System of record | Current surface |
|---|---|---|---|---|---|
| Agent output is accepted only with independent evidence | Reviewed SHA equals candidate head; required proof classes pass | Merge Factory admission, lease, lock, and judge attestation | exact-SHA review, validated clean-room receipt, installed-artifact and post-merge proof | Git object identity + receipt store + Merge Factory attestations | CLI/API/provider adapters |
| Merge evidence is portable across Git providers | One approved independent clean-room backend emits a valid exact-head receipt; provider status cannot override it | provider-neutral policy runner, receipt validator, merge judge, and thin provider adapters | versioned receipt, clean-checkout identity, command/log/artifact digests, verifier identity, freshness/revocation decision | receipt store + Git object identity + Merge Factory attestation | canonical policy; Linear epic GRO-4203; runner pending |
| Orchestration remains correct under replay and contention | cap is never exceeded; stale holders cannot mutate | atomic leases, fencing, idempotent cohort, recovery drills | barrier/race/adversarial tests and retained recovery evidence | durable orchestration stores | CLI/API |
| Verification remains effective | planted faults are detected and stale policies are surfaced | meta-verification maintenance loop | seeded-fault detection and verifier-drift reports | verification evidence ledger | CI/operations |
| Agent speed remains sustainable | accepted value rises without hidden debt | quality/debt maintenance loop | escaped defects, rollback, rework, evidence latency, 30/90/180-day burden | versioned quality/incident metrics | reports/dashboard target |

## Canonical AGY execution OKF map

| Objective | Key result | Function/workflow | Evidence | System of record | Current surface |
|---|---|---|---|---|---|
| Unattended AGY work follows one durable workflow | Every PE launch uses `/goal`, a hash-bound binary/task, and a unique tmux anchor | `prismatic agy contract|render|launch|wait` | manifest, launch receipt, process receipt, plan/result/log artifacts | canonical AGY manifest + launch/process receipts + exact task and executable digests | CLI/harness/contract |
| Long AGY work remains observable without arbitrary termination | No wall-clock deadline; exact process-tree CPU/I/O/log/artifact activity is classified as working/quiet/suspect without auto-kill | `prismatic.agy_activity` + `/api/gateway/agy/activity` | activity receipts, API tests, dashboard source/generated markers | canonical AGY activity receipts | Dashboard AGY Exact-Run Activity panel |
| AGY failure is contained and attributable | Explicit cancellation and terminal cleanup target the exact session; stdout/stderr/diagnostics remain separate; drift fails closed | `launch_tmux()` / `wait_tmux()` / `AGYCLIHarness.cancel()` | focused fake-binary tmux integration test and retained receipts | launch/process/cancel receipt store | CLI/harness/dashboard |
| Producer completion does not authorize acceptance | Every result remains pending independent exact-artifact verification | canonical result marker + downstream verifier/merge judge | result digest, reviewed commit/tree, independent receipt | result artifact + verification receipt + Git identity | contract; event binding pending |

## Public-launch OKF map

| Objective | Key result | Function/workflow | Evidence | Current surface | Target dashboard surface |
|---|---|---|---|---|---|
| External users can run PE locally | Public launch smoke passes | `scripts/public_launch_smoke.py` | `PUBLIC_LAUNCH_SMOKE_OK` | CLI/CI | Dashboard diagnostic job |
| Public docs are discoverable | Stable public entrypoints exist | README + public docs | `docs/public-launch.md`, `docs/plugin-developer-quickstart.md`, `docs/security.md`, `docs/contributing.md` | Docs | Dashboard Help/Docs panel |
| Release surface is coherent | Release smoke passes | `scripts/release_smoke.py` | `RELEASE_SMOKE_OK` | CLI/CI | Dashboard release readiness card |
| Security assumptions are visible | Security readiness audit passes | `scripts/public_security_readiness_audit.py` | `PUBLIC_SECURITY_READINESS_OK` | CLI/CI/docs | Dashboard security readiness card |

## Plugin-governance OKF map

| Objective | Key result | Function/workflow | Evidence | Current surface | Target dashboard surface |
|---|---|---|---|---|---|
| Shipped plugins load safely | All shipped plugins pass load gate | `verify_shipped_plugins_load()` / `plugin-load-gate` | `all 5 shipped plugins loaded successfully` | CLI/API/CI | Plugin catalog health card |
| Plugin readiness is visible | Governance summary exposes ready/warning/blocked | `/api/plugins/governance` | ready/warning/blocker counts | API/dashboard | Dashboard governance cards |
| Risky plugin actions are governed | Policy returns `allow`, `needs_approval`, or `block` | `prismatic/plugin_policy.py`, `/api/plugins/policy/preview` | policy decision payload | API/tests | Inline policy preview panel |
| Job lifecycle is durable | Jobs persist lifecycle state and audit events | `/api/plugins/jobs` | job summary + event history | API/dashboard | Job detail page |
| Rejected jobs cannot run | Start policy blocks rejected jobs | `/api/plugins/jobs/{job_id}/start` | policy_checked + start_blocked audit event | API/tests | Dashboard blocked-action explanation |
| Approved jobs can proceed | Approval state unlocks start when policy allows | `/api/plugins/jobs/{job_id}/approve` then start | approved + started audit events | API/tests/dashboard controls | Guided approval flow |
| Audit history is operator-visible | Cross-plugin audit endpoint returns events | `/api/plugins/audit-events` | event_count/job/artifact summaries | API/dashboard | Filterable audit log |

## Artifact/provenance OKF map

| Objective | Key result | Function/workflow | Evidence | Current surface | Target dashboard surface |
|---|---|---|---|---|---|
| Plugin outputs are durable | Artifact records persist independent of plugin connection | `/api/plugins/artifacts` | artifact summary + detail | API/dashboard | Artifact library |
| Provenance is preserved | Artifact record includes source plugin/job, metadata, provenance, hash/size when safe | `PluginArtifactStore` | artifact detail payload | API/tests | Artifact provenance panel |
| Unsafe paths are not read | Path safety blocks hashing unsafe local paths | artifact path policy | security readiness audit/tests | tests/docs | Security warning in artifact form |
| Publish/export requires governance | Approval/provenance enforced before export/publish-ready | `/api/plugins/artifacts/{id}/publish-ready`, `/export` | policy_result + export_history | API/tests | Approval/export panel |

## PWP reference lifecycle OKF map

| Objective | Key result | Function/workflow | Evidence | Current surface | Target dashboard surface |
|---|---|---|---|---|---|
| Reference plugin proves lifecycle | PWP demonstrates connect → job → artifact → approval → export/publish-ready → safe disconnect | `run_pwp_reference_lifecycle()` | lifecycle summary/history | CLI/API/dashboard | PWP guided reference flow |
| Plugin disconnect preserves history | Artifacts and job/audit history remain after disconnect | PWP lifecycle demo | preserved artifact/job records | tests/API/dashboard | Disconnect confirmation panel |
| Reference flow is documented | Public docs explain safe lifecycle | `docs/pwp-reference-lifecycle.md` | doc + tests | docs | Dashboard reference/tutorial panel |

## Dashboard-primary OKF map

| Objective | Key result | Function/workflow | Evidence | Current surface | Target dashboard surface |
|---|---|---|---|---|---|
| Dashboard becomes the authoritative operator view | Dashboard mirrors durable jobs/artifacts/audit events without inventing live state | dashboard Plugins tab | dashboard markers + visual QA | dashboard/API | Dashboard-first command center |
| Users need fewer shell commands | Common diagnostics can be launched from UI | future diagnostic job actions | public/security/release smoke outputs | CLI today | Dashboard diagnostic runner |
| Approvals are contextual | UI shows policy reason, risk, affected job/artifact, and audit trail before approval | approval controls + policy detail | dashboard markers/tests | dashboard basic controls | Rich approval workflow |
| Telegram remains useful but secondary | Telegram sends urgent alerts that link to dashboard records | Telegram adapter/future notification center | message + dashboard link | Telegram/headless | Dashboard notification center + Telegram fallback |

## Media plugin OKF map

| Objective | Key result | Function/workflow | Evidence | Current surface | Target dashboard surface |
|---|---|---|---|---|---|
| Media plugins can be added safely | All media blueprints use structured governance | Media Blueprint Governance Upgrade | blueprint validation smoke | docs/CLI | Plugin developer checklist |
| Generated media is traceable | Artifacts include prompt/input/model/source/export lineage | media plugin artifact provenance | artifact records | future plugin/API | Media artifact library |
| Costly/provider actions are gated | Costly/batch/external provider jobs require policy and approvals | media policy presets | policy decisions/audit events | future policy/docs | Cost/risk approval panel |

## Business plugin OKF map

| Objective | Key result | Function/workflow | Evidence | Current surface | Target dashboard surface |
|---|---|---|---|---|---|
| Business plugins are first-class | Business blueprint pack exists | `seo-ops`, `booking-ops`, `business-intelligence` blueprints | blueprint validation smoke | future docs/CLI | Business plugin setup checklist |
| Customer/business risk is explicit | PII/payment/public-send/write risks have policy presets | business policy checks | policy decisions | future policy/docs | Risk-specific approval panels |
| Business outputs are auditable | Reports/bookings/campaigns/customer records have durable provenance | business artifact types | artifact records | future plugin/API | Business artifact/evidence library |

## Dashboard implementation implication

Dashboard actions should not duplicate CLI code. Each OKF row should eventually map to one of:

- Gateway API endpoint,
- command registry action,
- governed plugin job,
- diagnostic job,
- artifact/provenance record,
- audit event query.

When a workflow is dashboard-ready, the dashboard should show:

```text
Objective
Key result
Current status
Run/preview/approve action
Evidence links
Audit history
Fallback CLI/API command
```

## Current documentation status

| Area | Status |
|---|---|
| Public launch docs | present |
| Security readiness docs | present |
| Plugin architecture docs | present |
| PWP lifecycle docs | present |
| Dashboard-primary target model | documented in `dashboard-primary-touchpoint.md` |
| Canonical North Star | documented in `north-star.md` |
| Media blueprint structured governance | next slice |
| Business plugin blueprint pack | following slice |
| Dashboard OKF/evidence board implementation | future implementation |
| Provider-neutral verification policy | accepted in ADR-0002; implementation tracked by GRO-4203 |
