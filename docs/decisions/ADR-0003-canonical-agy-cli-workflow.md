# ADR-0003: Canonical unattended AGY transport and artifact boundary

**Status:** Accepted
**Owner:** Prismatic Engine orchestration maintainers
**Date:** 2026-07-25
**Supersedes:** Unindexed provider-playbook launch examples and raw detached AGY process patterns

## Context

Prismatic contained several AGY command builders, historical provider notes, sandbox supervisors, and runtime gates, but no single canonical unattended CLI contract. A GRO-4210 producer attempt used a new raw detached `Popen` path, bypassing the tmux transport already established by `agentic-swarm-ops`. The old AGY process terminated near five minutes during an in-place binary update and leaked a terminal subprocess.

The swarm's current workflow contains valuable operational learning but also mutable workstation paths, broad checkpoint commits, Linear-era side effects, and cross-task artifact-selection risks that cannot become PE Core authority unchanged.

## Decision

PE Core owns one canonical AGY CLI workflow in `prismatic/agy_cli.py` and `docs/contracts/canonical-agy-cli-workflow.md`.

Unattended admitted AGY work uses:

- a frozen task file and hash-bound executable;
- a short `/goal` prompt;
- `--print`, permission skip, no PE wall-clock deadline, bounded directories, and sandboxing;
- a unique tmux durable anchor;
- durable exact-process activity receipts projected to the dashboard without automatic time/inactivity kills;
- plan-first and durable result artifacts;
- separate stdout, stderr, and AGY diagnostics;
- exact launch/process receipts and exact-session cleanup;
- independent exact-artifact verification after producer exit.

AGY does not own task admission, Linear/GitHub writes, merge, deployment, restart, concurrency changes, or acceptance. Generic checkpoint auto-commits are excluded from exact-scope work.

## Consequences

- `prismatic agy contract|render|launch|wait` becomes the front-door CLI.
- Existing AGY supervisors are legacy until they call or conform to this contract; they may not claim canonical execution merely because their command includes `--print`.
- Provider and model changes do not alter the transport/evidence contract.
- A mutable auto-updating binary cannot satisfy executable identity.
- Event consumers retain their own admission/fencing logic and bind it to the rendered manifest and launch receipt.
- Dashboard/chat remain read models over durable receipts.

## Verification

The focused suite uses a fake reviewed executable inside real tmux and verifies argument shape, result artifacts, log separation, receipt identity, cleanup, and fail-closed gates. Live AGY and production deployment proofs remain distinct authorization classes.
