# Agent Completed-Work Skill Packs

Status marker: `AGENT_COMPLETED_WORK_SKILL_PACKS_OK`

This reference wires the shared and agent-specific skill-pack contract for Prismatic completed-work packets. It is intentionally repo-level documentation/config guidance: live Hermes profile skills are managed outside this repository, so this document does **not** claim skills are installed in every live profile or that agents are retrained.

## Principle

```text
Skills optimize for best output.
Infrastructure protects against worst output.
```

Agents should load the applicable skill pack before producing completed-work output. The completed-work lane must still reject, normalize, or route bad output safely.

## Shared skill packs

| Skill pack | Purpose | Required packet effect |
|---|---|---|
| `shared/prismatic-completed-work-contract` | Canonical completed-work packet shape | Ensures `agent`, `task_id`/`issue_identifier`, `source_path`, `changed_files`, `proof`, `artifacts`, `non_claims`, `marker` |
| `shared/prismatic-proof-packet` | Compact proof discipline | Ensures proof includes `command`, `result`, `log`, `scope`, `non_claims`, `marker` |
| `shared/prismatic-non-claims` | Explicit boundary reporting | Prevents accidental claims of auto-merge, production deploy, broad overnight mode, real PR creation, live Linear mutation |
| `shared/prismatic-safe-file-scope` | File/provenance safety | Requires safe repo-relative `changed_files` and non-secret `source_path`/artifact paths |

## Agent-specific skill packs

| Agent | Skill packs | Primary output risk reduced |
|---|---|---|
| AGY | `agy/agy-structured-result-packet`, `agy/agy-one-task-scope`, `agy/agy-dashboard-work`, `agy/agy-model-preflight` | Missing `source_path`, broad task claims, stale model aliases, dashboard fixture claims |
| Fred | `fred/fred-clean-pr-builder`, `fred/fred-verification-gate-runner`, `fred/fred-deploy-proof` | PR/CI/deploy claims without exact evidence |
| George | `george/george-dashboard-operator-audit` | Dashboard observations without operator/audit proof |
| Kai | `kai/kai-prismatic-domain-review` | Domain/content review without source/changed-file boundaries |

## Minimal dispatch preflight checklist

Before dispatching or accepting a completed-work packet, the dispatcher should record:

```yaml
dispatch_preflight:
  assigned_agent: agy|fred|george|kai
  skill_packs_requested:
    - shared/prismatic-completed-work-contract
    - shared/prismatic-proof-packet
    - shared/prismatic-non-claims
    - shared/prismatic-safe-file-scope
  agent_skill_packs_requested: []
  packet_contract_version: prismatic-completed-work-v1
  expected_marker: AGENT_COMPLETED_WORK_PACKET_OK
  live_linear_mutations_allowed: false
  auto_merge_allowed: false
  production_deploy_allowed: false
  real_github_pr_create_allowed: false
```

If skill loading is unavailable, infrastructure should continue safely and include:

```text
skill_pack_state=unavailable_or_not_reported
packet_validation=required
```

For AGY print-mode dispatch, the dispatcher must also make the skills observable in the captured output stream before the model works, because the reconciler cannot assume AGY actually loaded external Hermes skills. The dispatcher should set `PRISMATIC_AGY_OUTPUT_LOG=<reconciler-read-log>` and the launch wrapper should write:

```text
AGY_OUTPUT_CAPTURE_WRAPPER_STARTED
skill_pack_state=loaded
shared_skill_packs=shared/prismatic-completed-work-contract,shared/prismatic-proof-packet,shared/prismatic-non-claims,shared/prismatic-safe-file-scope
agent_skill_packs=agy/agy-structured-result-packet,agy/agy-one-task-scope,agy/agy-dashboard-work,agy/agy-model-preflight
packet_contract_version=prismatic-completed-work-v1
```

and must append a conservative `RESULT=BLOCKED` packet when AGY exits without exact compact packet lines.

## Canonical completed-work packet contract

Required fields:

| Field | Type | Notes |
|---|---|---|
| `agent` | string | Must match resolved assigned agent. Unknown/ambiguous agents fail closed. |
| `task_id` or `issue_identifier` | string | Stable task source identifier. |
| `source_path` | string | Path to the working artifact/root. Must not point to secrets, credentials, generated vendor dirs, or traversal paths. |
| `changed_files` | array[string] | Repo-relative file list where applicable. Empty only when packet explicitly represents observation-only work. |
| `artifacts` or `result_artifacts` | array | Proof artifacts, logs, screenshots, or observation files. |
| `proof` | object | See proof packet contract below. |
| `non_claims` | array[string] | Explicit boundaries. |
| `marker` | string | Agent or lane marker. |

### Canonical JSON shape

```json
{
  "agent": "agy",
  "issue_identifier": "AGY-LIMITED-OBSERVATION",
  "source_branch": "feature/agy-limited-observation",
  "source_path": "/home/ubuntu/.prismatic/artifacts/agy-limited-observation",
  "base_branch": "main",
  "changed_files": ["docs/agy-limited-observation.md"],
  "lane_scope": {
    "merge_lane": "docs",
    "verification_lane": "docs",
    "auto_merge": false,
    "production_deploy": false,
    "real_github_pr_create": false
  },
  "proof": {
    "command": "python3 -m pytest -q tests/test_example.py",
    "result": "PASS",
    "log": "/tmp/fred-example-verify.log",
    "scope": "focused completed-work packet verification",
    "non_claims": [
      "auto_merge_enabled",
      "production_deploy",
      "real_github_pr_created",
      "live_Linear_mutations"
    ],
    "marker": "AGENT_COMPLETED_WORK_PACKET_OK"
  },
  "artifacts": [
    {"path": "/tmp/fred-example-verify.log", "kind": "verification_log"}
  ],
  "non_claims": [
    "unbounded_overnight_autopilot",
    "auto_merge_enabled",
    "bulk_agy_dispatch",
    "production_deploy",
    "canonical_full_suite_green",
    "real_github_pr_created",
    "live_Linear_mutations_without_approval"
  ],
  "marker": "AGENT_COMPLETED_WORK_PACKET_OK"
}
```

## Proof packet contract

Every proof packet example must include these keys:

```text
COMMAND=<exact command>
RESULT=<PASS|BLOCKED|FAIL|PARTIAL_BLOCKED>
LOG=/tmp/<agent-or-fred>-verify.log
SCOPE=<bounded verification scope>
AD_HOC_OR_CANONICAL=<ad-hoc targeted|canonical suite green|GitHub CI green>
NOT_CLAIMING=<comma-separated boundaries>
MARKER=<lane marker>
```

Example:

```text
COMMAND=python3 -m py_compile prismatic/agy_completed_work.py && python3 -m pytest -q tests/test_agy_completed_work.py
RESULT=PASS
LOG=/tmp/fred-agent-skill-packs-verify.log
SCOPE=shared + agent-specific completed-work skill pack contract/docs/config/test proof
AD_HOC_OR_CANONICAL=ad-hoc targeted + GitHub CI if PR opened
NOT_CLAIMING=skills_installed_in_all_live_profiles,agents_retrained,overnight_autopilot_active,auto_merge_enabled,production_deploy,canonical_full_suite_green
MARKER=AGENT_COMPLETED_WORK_SKILL_PACKS_OK
```

## Agent-specific packet examples

### AGY structured result packet

AGY should load:

```text
shared/prismatic-completed-work-contract
shared/prismatic-proof-packet
shared/prismatic-non-claims
shared/prismatic-safe-file-scope
agy/agy-structured-result-packet
agy/agy-one-task-scope
agy/agy-model-preflight
```

Example:

```json
{
  "agent": "agy",
  "issue_identifier": "AGY-DASHBOARD-OBSERVATION",
  "branch": "feature/agy-dashboard-observation",
  "base_branch": "main",
  "source_path": "/home/ubuntu/.prismatic/agy-artifacts/AGY-DASHBOARD-OBSERVATION",
  "merge_lane": "docs",
  "verification_lane": "docs",
  "changed_files": ["docs/agy-dashboard-observation.md"],
  "result_artifacts": [
    {"path": "/home/ubuntu/.prismatic/agy-artifacts/AGY-DASHBOARD-OBSERVATION/OBSERVATION.md"}
  ],
  "proof": {
    "command": "local API observation only; no repo mutation",
    "result": "PASS",
    "log": "/tmp/agy-dashboard-observation.log",
    "scope": "AGY dashboard/API observation artifact",
    "non_claims": ["auto_merge_enabled", "production_deploy", "real_github_pr_created", "more_than_one_AGY_task"],
    "marker": "AGY_STRUCTURED_RESULT_PACKET_OK"
  },
  "non_claims": [
    "unbounded_overnight_autopilot",
    "auto_merge_enabled",
    "bulk_agy_dispatch",
    "production_deploy",
    "real_github_pr_created",
    "live_Linear_mutations_without_approval"
  ],
  "marker": "AGY_STRUCTURED_RESULT_PACKET_OK"
}
```

### Fred clean PR builder packet

Fred should load:

```text
shared/prismatic-completed-work-contract
shared/prismatic-proof-packet
shared/prismatic-non-claims
shared/prismatic-safe-file-scope
fred/fred-clean-pr-builder
fred/fred-verification-gate-runner
fred/fred-deploy-proof
```

Example:

```json
{
  "agent": "fred",
  "issue_identifier": "GRO-EXAMPLE",
  "source_branch": "feature/fred-example",
  "source_path": "/home/ubuntu/work/prismatic-engine",
  "base_branch": "main",
  "changed_files": ["prismatic/example.py", "tests/test_example.py"],
  "proof": {
    "command": "python3 -m py_compile prismatic/example.py && python3 -m pytest -q tests/test_example.py",
    "result": "PASS",
    "log": "/tmp/fred-example-verify.log",
    "scope": "focused PR verification for changed files",
    "non_claims": ["canonical_full_suite_green", "production_deploy"],
    "marker": "FRED_CLEAN_PR_PACKET_OK"
  },
  "artifacts": [{"path": "/tmp/fred-example-verify.log", "kind": "verification_log"}],
  "non_claims": ["canonical_full_suite_green", "production_deploy", "live_Linear_mutations_without_approval"],
  "marker": "FRED_CLEAN_PR_PACKET_OK"
}
```

### George dashboard operator audit packet

George should load `george/george-dashboard-operator-audit` plus all shared packs.

```json
{
  "agent": "george",
  "issue_identifier": "GEORGE-DASHBOARD-AUDIT",
  "source_path": "/home/ubuntu/.prismatic/george-audits/GEORGE-DASHBOARD-AUDIT",
  "changed_files": [],
  "proof": {
    "command": "browser/API dashboard audit with screenshot artifact",
    "result": "PASS",
    "log": "/tmp/george-dashboard-audit.log",
    "scope": "operator dashboard audit observation only",
    "non_claims": ["production_deploy", "real_github_pr_created"],
    "marker": "GEORGE_DASHBOARD_OPERATOR_AUDIT_PACKET_OK"
  },
  "artifacts": [{"path": "/tmp/george-dashboard-audit.png", "kind": "screenshot"}],
  "non_claims": ["auto_merge_enabled", "production_deploy", "real_github_pr_created", "live_Linear_mutations_without_approval"],
  "marker": "GEORGE_DASHBOARD_OPERATOR_AUDIT_PACKET_OK"
}
```

### Kai domain review packet

Kai should load `kai/kai-prismatic-domain-review` plus all shared packs.

```json
{
  "agent": "kai",
  "issue_identifier": "KAI-DOMAIN-REVIEW",
  "source_path": "/home/ubuntu/.prismatic/kai-reviews/KAI-DOMAIN-REVIEW",
  "changed_files": ["docs/domain-review.md"],
  "proof": {
    "command": "domain review of provided source documents; no publish action",
    "result": "PASS",
    "log": "/tmp/kai-domain-review.log",
    "scope": "Prismatic domain/content review packet",
    "non_claims": ["published", "production_deploy", "live_Linear_mutations_without_approval"],
    "marker": "KAI_PRISMATIC_DOMAIN_REVIEW_PACKET_OK"
  },
  "artifacts": [{"path": "/tmp/kai-domain-review.log", "kind": "review_log"}],
  "non_claims": ["published", "auto_merge_enabled", "production_deploy", "real_github_pr_created", "live_Linear_mutations_without_approval"],
  "marker": "KAI_PRISMATIC_DOMAIN_REVIEW_PACKET_OK"
}
```

## Dashboard and writeback language

When available, dashboard/API/Linear writeback should include compact skill-pack state:

```text
skill_pack_state=loaded
shared_skill_packs=shared/prismatic-completed-work-contract,shared/prismatic-proof-packet,shared/prismatic-non-claims,shared/prismatic-safe-file-scope
agent_skill_packs=agy/agy-structured-result-packet,agy/agy-one-task-scope,agy/agy-model-preflight
packet_contract_version=prismatic-completed-work-v1
packet_validation=passed
```

If unavailable:

```text
skill_pack_state=unavailable_or_not_reported
packet_contract_version=prismatic-completed-work-v1
packet_validation=required
next_safe_action=validate packet through completed-work normalizer and reject unsafe output
```

## Static acceptance proof

A static verifier for this document must prove:

```text
shared_contract_exists=true
agy_packet_example_has_source_path=true
proof_packet_example_has_command_result_log_scope_nonclaims_marker=true
non_claims_example_present=true
agent_specific_skill_matrix_present=true
no_secrets_in_docs=true
```

## Explicit non-claims

```text
NOT_CLAIMING=skills_installed_in_all_live_profiles,agents_retrained,overnight_autopilot_active,auto_merge_enabled,production_deploy,canonical_full_suite_green
```
