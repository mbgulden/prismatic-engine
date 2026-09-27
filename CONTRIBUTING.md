# Contributing to Prismatic Engine

Thanks for helping make Prismatic Engine better.

## Development setup

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[gateway]"
python scripts/public_launch_smoke.py
```

## Before opening a PR

Run focused checks for the area you changed and at least:

```bash
python scripts/public_launch_smoke.py
python scripts/plugin_architecture catalog
plugin-load-gate
```

For plugin work, also run the relevant focused tests:

```bash
python -m pytest tests/test_plugin_policy.py tests/test_plugin_artifacts.py tests/test_plugin_jobs.py -q
```

## Plugin rules

- Keep incomplete future plugins under `docs/plugin-blueprints/`, not `plugins/`.
- Do not commit credentials, local state, virtualenvs, or generated build artifacts.
- Manifests may document credential variable names, but never credential values.
- Risky actions need policy/approval gates.
- Artifacts should be registered through PE Core with provenance.

## PR contract (required)

Every PR body is a contract. It must carry these five sections (the PR
template pre-fills them):

1. `## What changed`
2. `## How I proved it` — tests run with real results, plus receipt IDs
3. `## Negative paths tested` — what you tried that should fail, and what
   actually happened ("none" is not an answer)
4. `## Risk & rollback` — what could go wrong and how to undo the PR
5. `## Local verification attestation` — a line claiming green local
   verification AND a receipt reference, e.g.
   `Local verification: green, receipt run-2026-09-25-001`

Rules:

- **No PR is opened until the author's local verification is green.**
  The merge path already requires deterministic green before authorize;
  the contract extends that to PR creation.
- A CI job (`contract-lint`) checks the structure mechanically — sections
  present, attestation line present. It never judges prose quality.
  It fails the PR, not the person.
- **Escape hatch:** the `contract-waiver` label plus a `## Waiver reason`
  section in the body. Both are required; the label alone is not enough.
  Every waiver use is audited and surfaced in the autonomy digest.

## PR expectations

Keep the contract sections tight and factual:

- clear summary
- files changed
- verification commands and real output
- screenshots for dashboard UI changes when useful
- docs updates for public behavior changes

## Code style

If available, run:

```bash
python -m ruff check prismatic tests scripts
python -m ruff format prismatic tests scripts
```
