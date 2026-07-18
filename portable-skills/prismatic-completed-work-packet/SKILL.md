---
name: prismatic-completed-work-packet
description: "Emit canonical Prismatic completed-work packets with proof, provenance, non-claims, lane/risk selection, and safe blocked-output handling."
version: 0.1.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Prismatic, Completed-Work, Proof, Linear, Agents, Safety]
    related_skills: [autonomous-execution-discipline, github-pr-workflow, linear]
---

# Prismatic Completed-Work Packet

Use this skill whenever an agent finishes, blocks, or hands off work in the Prismatic swarm. The goal is not to sound successful. The goal is to make downstream infrastructure able to validate, normalize, reject, recover, and summarize the work without guessing.

Acceptance marker for GRO-3957:

```text
SHARED_COMPLETED_WORK_SKILL_OK
```

## Core rule

```text
Best-output skills + worst-output infrastructure
```

Agents should voluntarily emit valid packets. Infrastructure must still preserve raw/bad output and classify it instead of faking success.

## Canonical completed-work packet fields

Emit these fields in a structured block, preferably JSON or YAML, at the end of the run:

| Field | Required | Meaning |
| --- | --- | --- |
| `schema_version` | yes | Current packet contract version, e.g. `prismatic.completed_work.v1`. |
| `issue_identifier` | yes | Linear issue key, e.g. `GRO-3957`. |
| `agent` | yes | Producing agent/profile, e.g. `kai`, `fred`, `agy`, `ned`. |
| `status` | yes | One of `completed`, `blocked`, `waiting`, `partial`, `failed`. |
| `classification` | yes | Downstream lane: `merge_ready`, `manual_review_scope`, `blocked_external`, `verification_failed`, or `artifact_only`. |
| `lane_scope` | yes | Primary lane: `content`, `active-oahu`, `docs`, `tests`, `config`, `dashboard`, `integration`, `ops`, or `unknown`. |
| `risk_level` | yes | `low`, `medium`, `high`, or `critical`. High/critical requires human review. |
| `source_path` | yes when available | Absolute or repo-relative source artifact path. Never use secret, token, or unsafe traversal paths. |
| `source_branch` | when git work exists | Branch containing work. Do not invent a branch. |
| `base_branch` | when git work exists | Base branch actually used, normally `main`. |
| `changed_files` | when files changed | Explicit list of files changed. Do not use broad claims such as `repo updated`. |
| `artifact_urls` | optional | PR, preview, report, or dashboard URLs that were actually created. |
| `verification` | yes | Commands or checks actually run, with real exit/result summary. |
| `proof_markers` | yes | Exact markers observed or emitted. |
| `non_claims` | yes | Things explicitly not done: no PR, no deploy, no merge, no email sent, no state transition, etc. |
| `blockers` | if blocked/partial/waiting | Exact unresolved dependency and owner if known. |
| `next_action` | yes | Smallest safe next step. |

## Proof block format

Every packet must include a compact proof block. Use real command output only.

```yaml
verification:
  commands:
    - command: "python3 -m json.tool config/example.json >/tmp/example.valid.json"
      exit_code: 0
      summary: "JSON parsed successfully"
    - command: "uv run python -m pytest tests/test_example.py"
      exit_code: 0
      summary: "2 passed in 0.03s"
  artifacts_checked:
    - path: "docs/example.md"
      evidence: "Contains acceptance marker and API contract section"
  external_checks:
    - name: "GitHub PR checks"
      result: "not applicable — no PR opened"
```

If a command failed, keep it in the proof block with its non-zero exit and summarize the failure. Do not remove failed evidence to make the packet look cleaner.

## Non-claims block

Explicitly say what did **not** happen, especially for side effects:

```yaml
non_claims:
  - "No PR was opened."
  - "No branch was pushed."
  - "No production deployment was triggered."
  - "No Linear issue was marked Done."
  - "No emails, webhooks, or customer messages were sent."
  - "No auto-merge or force-push was performed."
```

Never claim a PR URL, deploy URL, Lighthouse result, merge, state transition, email send, or production change unless the tool output proves it happened.

## Lane and risk selection

Choose the narrowest accurate lane.

- `content`: copy, SEO, editorial artifacts.
- `active-oahu`: Active Oahu Tours site/domain operations.
- `docs`: documentation-only repo changes.
- `tests`: test harnesses or verification-only code.
- `config`: config defaults, schemas, environment contracts.
- `dashboard`: UI/operator controls.
- `integration`: API/webhook/automation connection work.
- `ops`: runbooks, monitoring, workflow governance.
- `unknown`: use only when provenance is unclear; classify as manual review.

Risk defaults:

- `low`: docs/report only; no production or credential side effects.
- `medium`: code/config/test changes without deployment.
- `high`: credentials, payments, booking, email sending, production edge/network changes, workflow state transitions.
- `critical`: auto-merge, production deploy, destructive data changes, force-push, bulk dispatch.

High/critical work must include human-review or policy-gate proof before merge/deploy claims.

## Blocked output is valid output

If the task blocks, emit a packet with `status: blocked` and enough evidence for the next agent/operator to resume.

```yaml
schema_version: "prismatic.completed_work.v1"
issue_identifier: "GRO-0000"
agent: "kai"
status: "blocked"
classification: "blocked_external"
lane_scope: "integration"
risk_level: "high"
source_path: "reports/example/blocker.md"
changed_files:
  - "reports/example/blocker.md"
verification:
  commands:
    - command: "gh pr view 123 --json state,statusCheckRollup"
      exit_code: 0
      summary: "PR open; checks green"
proof_markers:
  - "BLOCKER_RECORDED_OK"
non_claims:
  - "No merge performed."
  - "No production deploy triggered."
blockers:
  - "Requires owner credentialed approval for Google Search Console submission."
next_action: "Owner grants credentialed access or performs the GSC submission manually."
```

## Merge-ready example

Only use `merge_ready` when artifacts and verification are concrete and the work is within lane.

```yaml
schema_version: "prismatic.completed_work.v1"
issue_identifier: "GRO-3957"
agent: "kai"
status: "completed"
classification: "merge_ready"
lane_scope: "docs"
risk_level: "low"
source_path: "portable-skills/prismatic-completed-work-packet/SKILL.md"
source_branch: "content/gro-3957-completed-work-skill"
base_branch: "main"
changed_files:
  - "portable-skills/prismatic-completed-work-packet/SKILL.md"
artifact_urls: []
verification:
  commands:
    - command: "python3 scripts/verify_gro3957_completed_work_skill.py"
      exit_code: 0
      summary: "Skill contract, examples, non-claims, lane/risk guidance, and acceptance marker present."
proof_markers:
  - "SHARED_COMPLETED_WORK_SKILL_OK"
non_claims:
  - "No auto-merge enabled."
  - "No production deploy triggered."
  - "No Linear issue marked Done without PR/review policy proof."
blockers: []
next_action: "Review and install/share this portable skill across relevant profiles after lane approval."
```

## Final response checklist

Before ending a run, make sure the human/operator can answer:

1. What issue was worked?
2. What files or artifacts changed?
3. What exact commands/checks ran and what returned?
4. What was **not** done?
5. What is the smallest safe next step?

If any answer is missing, the packet is not complete.
