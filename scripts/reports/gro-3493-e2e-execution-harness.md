# GRO-3493 — End-to-End Execution Verification Harness

## Command

```bash
python3 scripts/e2e_execution_harness.py
```

Optional machine-readable output:

```bash
python3 scripts/e2e_execution_harness.py --json
```

## What it proves

The harness runs a deterministic local canary for the full execution chain:

1. **Issue** — creates a synthetic Linear-like issue carrying `agent:ned` and `dispatch:ready`.
2. **Dispatch** — selects that issue by agent label and ready state.
3. **Execution** — invokes a deterministic fake executor.
4. **Artifact** — requires `artifacts/GRO-3493-E2E/RESULT.md` and records its SHA-256.
5. **Linear update** — moves the synthetic issue to `In Review`, swaps routing to `agent:peer-review`, and writes a `Self-Review PASSED` evidence comment.

The harness does not use the live Linear API or a real agent account. That is intentional: it is repeatable, side-effect-free, and suitable for local failure isolation before exercising production dispatch.

## Broken-link isolation

Operators can inject a failure at any link and get a named failing stage plus exit code `2`:

```bash
python3 scripts/e2e_execution_harness.py --break-stage artifact --json
python3 scripts/e2e_execution_harness.py --break-stage linear-update --json
```

The JSON report includes `failed_stage`, `failure`, completed stage details, artifact path/checksum when available, and the synthetic Linear ledger state.

## Repeatability guard

Focused pytest coverage lives at:

```bash
python3 -m pytest scripts/tests/test_e2e_execution_harness.py -q
```

The test suite runs the success path twice in separate workspaces and verifies both failure-isolation cases (`artifact` and `linear-update`).
