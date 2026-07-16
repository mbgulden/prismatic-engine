# Handoff Contracts Specification

**Issue:** GRO-549
**Status:** specification slice, not runtime enforcement
**Owner lane:** Fred / orchestration
**Marker:** `HANDOFF_CONTRACTS_SPEC_OK`

`HANDOFF_CONTRACTS_SPEC_OK`

## Purpose

A handoff contract is the durable agreement between two Prismatic actors when work moves across an integration boundary: Fred, AGY, Kai, Ned, Jules, Linear, GitHub, cron, or a production verifier.

The contract prevents ambiguous transfers such as "agent started but left no `RESULT.md`" by requiring a consistent input shape, output shape, capability declaration, SLA, retry policy, and evidence packet.

This document defines the baseline contract. It does **not** claim runtime enforcement is implemented yet.

## Contract levels

| Level | Meaning | Required before dispatch? |
|---|---|---:|
| `advisory` | Recommended metadata for humans and agents | no |
| `required` | Dispatcher or reviewer must reject missing fields | yes |
| `enforced` | Runtime validates the schema and blocks invalid handoffs | yes, when implemented |

Current slice target:

```text
level = required-by-review
runtime_enforcement = not yet implemented
```

## Required input shape

Every handoff SHOULD be represented as a JSON-compatible object with these top-level fields:

```json
{
  "handoff_id": "GRO-549:2026-07-16T00:00:00Z:fred-to-agy",
  "source": {
    "agent": "fred",
    "system": "hermes",
    "session_ref": "optional-session-or-run-id"
  },
  "target": {
    "agent": "agy",
    "lane": "design-or-implementation",
    "required_capabilities": ["repo_read", "repo_write", "linear_comment"]
  },
  "work": {
    "issue": "GRO-549",
    "title": "Define and implement Handoff Contracts specification",
    "scope": "one narrow reviewable slice",
    "branch": "docs/fred-gro-549-handoff-contracts",
    "base": "origin/main",
    "allowed_paths": ["docs/handoff-contracts-spec.md"],
    "forbidden_paths": ["content/", "active-oahu/"]
  },
  "acceptance": {
    "expected_markers": ["HANDOFF_CONTRACTS_SPEC_OK"],
    "verification_commands": ["focused /tmp/hermes-verify-* docs verifier"],
    "not_claiming": ["runtime enforcement", "canonical full suite green"]
  },
  "retry": {
    "max_attempts": 1,
    "on_missing_result": "pause_and_require_review",
    "on_scope_violation": "block_and_comment",
    "on_verification_failure": "return_to_source_with_evidence"
  },
  "evidence": {
    "required_artifacts": ["PR URL", "commit SHA", "verification output", "Linear comment"],
    "result_file": "RESULT.md or equivalent durable summary when sandboxed"
  }
}
```

## Required fields

| Field | Required | Why |
|---|---:|---|
| `handoff_id` | yes | stable dedupe key for retry/recovery |
| `source.agent` | yes | who is yielding responsibility |
| `target.agent` | yes | who is expected to act next |
| `target.required_capabilities` | yes | prevents assigning work to an incapable lane |
| `work.issue` | yes | Linear/GitHub traceability |
| `work.scope` | yes | keeps handoffs narrow and reviewable |
| `work.base` | yes | prevents stale or dirty branch starts |
| `work.allowed_paths` | yes | supports lane governance |
| `acceptance.expected_markers` | yes | reviewers know the finish line |
| `acceptance.not_claiming` | yes | prevents overclaiming |
| `retry.on_missing_result` | yes | handles abandoned-agent cases deterministically |
| `evidence.required_artifacts` | yes | keeps review evidence portable |

## Capability vocabulary

Use concrete capability names. Avoid vague labels such as `can_fix_it`.

| Capability | Meaning |
|---|---|
| `repo_read` | inspect repository state |
| `repo_write` | edit allowed paths |
| `git_branch` | create/update a branch |
| `github_pr` | open/update PR |
| `linear_comment` | write issue evidence |
| `linear_state_update` | move workflow state |
| `browser_proof` | capture browser/console/screenshot proof |
| `production_deploy` | intentionally update/restart production services |
| `cloudflare_access` | read/update narrow Cloudflare Access policy |
| `cron_manage` | create/update cron jobs |
| `artifact_publish` | write durable artifacts outside transient sandbox |

## SLA fields

Each handoff should declare a response and closeout expectation:

```json
{
  "sla": {
    "ack_within_minutes": 15,
    "first_evidence_within_minutes": 60,
    "closeout_within_hours": 24,
    "stale_after_hours": 24
  }
}
```

If an agent cannot meet the SLA, it must return a blocker packet rather than silently holding the issue.

## Retry semantics

| Failure | Required behavior |
|---|---|
| Missing `RESULT.md` / no output artifact | add/keep review hold, comment exact missing artifact, do not redispatch blindly |
| Branch starts from dirty base | block before work begins; request clean branch/worktree |
| Out-of-lane file touched | block or route to owner lane; do not bypass hooks |
| Verification fails | return to source/owner with command, output, and failing contract |
| Production proof missing | do not claim production fixed; apply production durability review gate |
| Ambiguous target agent | route to `needs_manual_review`, not `no_op` |

## Output packet shape

Every completed handoff should return:

```json
{
  "handoff_id": "...",
  "status": "pass|partial|blocked",
  "issue": "GRO-549",
  "branch": "docs/fred-gro-549-handoff-contracts",
  "commit": "short-sha",
  "pr": "https://github.com/.../pull/NNN",
  "verification": {
    "scope": "ad-hoc targeted docs verifier, not canonical suite green",
    "commands": ["..."],
    "marker": "HANDOFF_CONTRACTS_SPEC_OK"
  },
  "remaining_blockers": []
}
```

## Review checklist

Before accepting a handoff, reviewers must answer:

- [ ] Is the source agent clear?
- [ ] Is the target agent/lane clear?
- [ ] Are required capabilities explicit?
- [ ] Is the scope narrow enough for review?
- [ ] Is the branch/base stated and clean?
- [ ] Are allowed and forbidden paths stated?
- [ ] Are expected markers and non-claims explicit?
- [ ] Are retry behaviors deterministic?
- [ ] Is there a durable evidence packet?
- [ ] If production-facing, does it import the Production Durability Standard?

## Relationship to existing Prismatic routing

This spec complements, but does not replace:

- assigned-agent wake dispatch,
- Linear labels such as `agent:fred`, `agent:agy`, and `dispatch:ready`,
- sandbox `RESULT.md` expectations,
- GitHub PR review gates,
- production durability requirements for live routes/services.

## Validator usage

The review-contract schema and semantic checks can be run against a handoff packet with:

```bash
python scripts/validate_handoff_contract.py tests/fixtures/handoff-contracts/pass.json
python scripts/validate_handoff_contract.py tests/fixtures/handoff-contracts/missing-result.json
python scripts/validate_handoff_contract.py tests/fixtures/handoff-contracts/out-of-lane.json
python scripts/validate_handoff_contract.py tests/fixtures/handoff-contracts/production-proof-missing.json
python scripts/validate_handoff_contract.py tests/fixtures/handoff-contracts/ambiguous-target-agent.json
```

Expected fixture behavior:

| Fixture | Expected |
|---|---:|
| `pass.json` | exit 0 |
| `missing-result.json` | nonzero |
| `out-of-lane.json` | nonzero |
| `production-proof-missing.json` | nonzero |
| `ambiguous-target-agent.json` | nonzero |

The CLI validator is intentionally standalone for this slice. It does **not** wire validation into dispatcher preflight yet.

## Next implementation slices

1. Wire validator into assigned-agent dispatch preflight.
2. Add dispatcher fixtures for pass/missing-result/out-of-lane/production-proof-missing cases.
3. Add Linear writeback templates for validation failures.

Until dispatcher preflight lands, this document, schema, fixtures, focused tests, and CLI validator are the review contract and source of truth for GRO-549.
