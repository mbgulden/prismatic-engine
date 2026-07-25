# Canonical AGY CLI Workflow Contract

**Status:** Canonical
**Owner:** Prismatic Engine orchestration maintainers
**Last verified:** 2026-07-25
**Runtime contract:** `prismatic/agy_cli.py`  
**Lifecycle harness:** `prismatic/harnesses/agy_cli.py`

## Purpose

Prismatic Engine has one unattended Google Antigravity (AGY) workflow. Provider selection may vary, but launch transport, goal framing, artifact durability, process containment, and acceptance boundaries do not.

This contract ports the useful current workflow from `agentic-swarm-ops` into PE while removing mutable workstation paths, cross-task result selection, generic checkpoint commits, direct Linear/GitHub writes, and producer self-acceptance.

## Canonical sequence

```text
admitted frozen task
  → hash-bound AGY binary
  → short `/goal` prompt
  → unique tmux durable anchor
  → implementation plan before edits
  → bounded execution and self-validation
  → separate stdout/stderr/AGY diagnostics
  → durable RESULT marker and process receipt
  → exact-session cleanup
  → independent exact-artifact verification
  → operator-authorized merge/deploy gates
```

## Required invariants

1. A real launch requires an owner-only `PRISMATIC_AGY_ADMISSION_V1` receipt binding the event, attempt token/cap, task digest, and executable digest. `--execute` is necessary but is not authorization by itself.
2. The task file is absolute, regular, non-symlinked, and frozen by SHA-256.
3. The AGY executable is absolute, regular, non-symlinked, non-group/world-writable, and bound to a reviewed SHA-256.
4. The prompt begins with `/goal ` and stays at or below 1,200 characters. Detailed scope belongs in the frozen task file.
5. Headless mode uses `--print`, `--dangerously-skip-permissions`, a canonical model ID, bounded `--add-dir` paths, and `--sandbox` by default. PE imposes no wall-clock runtime deadline. Because AGY requires a Go duration, the adapter passes its maximum whole-second duration (`2562047h47m16s`) only as a protocol bridge—not as an operational kill policy.
6. AGY runs inside a unique tmux session. Raw detached `Popen` is not canonical transport.
7. stdout, stderr, and `--log-file` diagnostics are separate files. The AGY child receives a minimal allowlisted environment rooted at an explicit AGY home; unrelated operator/provider secrets are not inherited.
8. AGY writes an implementation plan before edits and a result containing `PRISMATIC_AGY_RESULT_V1` before completion.
9. The launch receipt binds workflow version, admission event/attempt/token, session, pane PID/start ticks, task digest, executable digest, and manifest digest.
10. While running, the exact process tree emits durable activity receipts from CPU ticks, process count, I/O counters, diagnostics/log growth, and artifact changes. The dashboard classifies recent progress as `working`, `quiet`, or `suspect`; these are monitoring signals, never automatic termination triggers.
11. Completion writes a process result and tears down the exact tmux session. The original pane identity must no longer be live.
12. Producer completion sets verification state to `pending`; it never authorizes acceptance, merge, deployment, or a downstream workflow transition. Cancellation is an explicit operator or governed-policy action against the exact run.

## Book-end adaptation

The useful `agentic-swarm-ops` Book End protocol is retained inside the artifact boundary:

1. implementation plan;
2. execute;
3. summary;
4. walkthrough with changed files and verification evidence;
5. durable result;
6. terminate.

AGY does **not** post Linear comments, relabel tasks, create PRs, merge, deploy, restart services, or increase concurrency. PE's governed adapters and explicit operator gates own those actions.

## CLI

```bash
prismatic agy contract
prismatic agy render <hash-bound specification arguments>
prismatic agy launch <hash-bound specification arguments> \
  --admission-receipt <receipt.json> --runtime-dir <dir> --execute
prismatic agy wait --receipt <launch-receipt.json>
```

`render` is side-effect free. `launch` requires both the literal `--execute` gate and an owner-only admission receipt. The receipt binds event identity, attempt token/cap, task digest, and executable digest into the retained manifest and launch receipt.

## Failure behavior

- Binary, task, manifest, or receipt digest drift fails closed.
- Unsafe identifiers, mutable executable paths, output paths outside the task/workspace boundary, or malformed model values fail closed.
- A vanished tmux session without a process result is failure.
- Quiet or suspect activity is visible in the dashboard but does not terminate AGY. Cancellation must target the exact admitted run and requires an explicit operator or governed-policy action.
- A result file without the required marker, exact diff proof, or independent verification is not accepted work.
- Auto-updaters may not replace the reviewed runtime binary during a run.

## Deliberately not ported

The generic `agentic-swarm-ops` launcher is useful research but is not called verbatim because it:

- is currently mode `0777`;
- invokes a mutable workstation binary;
- discovers `result.md` by recent brain-directory timestamps;
- can start generic auto-commit checkpoint daemons;
- does not preserve exact AGY exit status in every mode;
- embeds Linear-era workstation paths and broad task behavior.

PE ports the tmux anchor, goal framing, plan-first discipline, output separation, stall awareness, and book-end artifact shape. PE keeps admission, exact scope, fencing, proof, merge, and deployment authority in PE.

## Verification

Required focused evidence:

```bash
python -m pytest -q tests/test_agy_cli_canonical_workflow.py
ruff check prismatic/agy_cli.py prismatic/cli/__init__.py tests/test_agy_cli_canonical_workflow.py
ruff format --check prismatic/agy_cli.py prismatic/cli/__init__.py tests/test_agy_cli_canonical_workflow.py
```

The tmux integration test uses a fake hash-bound AGY executable and verifies plan/result creation, argument shape, separate logs, durable receipts, and exact cleanup. A live AGY run is a separate authorized proof class.
