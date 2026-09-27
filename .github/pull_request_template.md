<!--
PR contract (WS3): every PR body must carry these five sections. A CI job
(`contract-lint`) checks the structure mechanically — it never judges prose
quality. The `contract-waiver` label + a `## Waiver reason` section is the
audited escape hatch (waiver use is surfaced in the autonomy digest).
-->

## What changed

<!-- The behavior change in plain language. Link the issue/plan it implements. -->

## How I proved it

<!-- Tests run (commands + real results) and receipt IDs.
     Example: pytest tests/test_x.py -q: 12 passed; receipt: run-2026-09-25-001 -->

## Negative paths tested

<!-- What you tried that should fail, and what actually happened.
     "None" is not an answer — name at least one. -->

## Risk & rollback

<!-- What could go wrong, and how to undo this PR (usually: revert the merge). -->

## Local verification attestation

<!-- Required: a line claiming green local verification AND a receipt reference.
     Example: Local verification: green, receipt run-2026-09-25-001 -->
<!-- Do not open the PR until your local verification is green. -->
