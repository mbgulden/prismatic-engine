# Prismatic Execution Evidence Contract

## Goal

Every Prismatic-controlled result must end with one of these explicit verdicts:

- `verified`
- `partially_verified`
- `blocked`
- `failed`
- `self_reported`

`self_reported` is intentionally **not acceptable as Done evidence**.

## Why this exists

Prismatic should not let an agent say “done” unless the operator can see:

- verification scope,
- commands and/or artifacts,
- files changed,
- external side effects,
- cleanup status,
- failure category or blocker when applicable.

This contract prevents ad hoc smoke checks from being confused with canonical/full-suite green.

## Verification scopes

| Scope | Meaning |
|---|---|
| `ad_hoc_targeted` | Focused smoke/check for a specific behavior. Useful, but not broad suite green. |
| `canonical_full_suite` | The project’s configured canonical suite/build passed. |
| `live_integration` | Credentialed/live system path was exercised. |
| `not_run` | No verification command ran. Valid only for blocked/not-run evidence. |

## Verification statuses

| Status | Done eligible? | Required evidence |
|---|---:|---|
| `verified` | Yes | commands or artifacts, cleanup status, no blocker/failure category |
| `partially_verified` | No | at least one command/artifact plus remaining blocker |
| `blocked` | No | blocker text and non-none failure category |
| `failed` | No | command/artifact context and failure category |
| `self_reported` | No | always rejected as Done evidence |

## Failure taxonomy

| Category | Use when |
|---|---|
| `timeout` | process/check exceeded its allowed time |
| `blocked_external_api` | Linear/GitHub/API/service dependency blocked progress |
| `blocked_missing_context` | required input/credentials/context was unavailable |
| `verification_failed` | command/test/smoke ran and failed |
| `conflict` | git/merge/workspace conflict blocked completion |
| `hallucinated_claim` | agent claimed a result not backed by evidence |
| `tooling_error` | local tooling or harness failed unexpectedly |
| `none` | only valid for `verified` evidence |

## Gate rule

`done_gate("Done", evidence)` returns:

- `done` only when evidence validates and `status == verified`.
- `not_done` for missing evidence, self-report, partial, blocked, or failed evidence.

## Fixture verification command

```bash
python3 scripts/verify_execution_evidence_contract.py --output-dir artifacts/execution-evidence-contract/latest --clean
```

Expected result:

```json
{
  "verdict": "PASS"
}
```

The command writes sample evidence payloads for:

- success,
- partial,
- blocked,
- failed,
- verification summary.

## Operator surface rule

Any status report, dashboard row, Telegram digest, or Linear evidence comment should display at minimum:

```text
verification_status=<status>
verification_scope=<scope>
failure_category=<category>
cleanup_status=<cleanup_status>
```

That gives Michael the distinction he keeps asking for: **ad hoc targeted verification vs canonical/full-suite green**, and **blocked vs failed vs self-reported**.
