# 📐 Architectural Specification: Provider-Agnostic Ephemeral Clean-Room Engine

**Engine Version**: Prismatic Engine v0.3.0  
**Feature Name**: Native Ephemeral Clean-Room Engine (`prismatic verify --clean-room`)  
**Task Identifier**: [GRO-4360](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4360)  
**Status**: ARCHITECTURAL SPECIFICATION & PRODUCTION DESIGN  

---

## 1. Executive Summary & Design Rationale

In multi-agent autonomous engineering pipelines, agents often pass test suites locally in their working directory while failing when reviewed on a separate host or clean CI runner. This occurs due to:
1. **Ambient File Leakage**: Untracked local files (`.env`, temporary `.json` caches, local sqlite DBs) present on the developer machine but absent from Git.
2. **Environment Variable Drift**: Hardcoded local user profiles (`HERMES_HOME=/home/ubuntu/...`), ambient `PYTHONPATH`s, or system environment overrides.
3. **Vendor Lock-In**: Verification tools tied exclusively to GitHub REST APIs, breaking compatibility with GitLab, Bitbucket, Azure DevOps, Gitea, or bare SSH remotes.

The **Provider-Agnostic Ephemeral Clean-Room Engine** (`prismatic verify --clean-room`) embeds hermetic sandboxing directly into `prismatic-engine`'s core CLI, using canonical Git primitives and sanitized subprocess isolation.

---

## 2. Core Architectural Invariants

### Invariant 1: Vendor-Agnostic Remote Ref Verification
- **Protocol**: Uses `git ls-remote <remote> <branch>` to query remote ref HEADs.
- **Provider Neutrality**: Operates identically against GitHub, GitLab, Bitbucket, Azure DevOps, Gitea, or bare Git server URLs (`git@server:repo.git`). Zero dependency on GitHub REST/GraphQL APIs.

### Invariant 2: Ephemeral Worktree Lifecycle
```text
  ┌───────────────────────┐
  │ Current Active Repo   │ (Main Working Directory)
  └───────────┬───────────┘
              │ 1. Spawns Detached Worktree
              ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ $env:TEMP/prismatic-cleanroom-<commit_sha>                  │
  │ (Sanitized Env: HERMES_HOME cleared, custom PYTHONPATH clear)│
  └───────────┬─────────────────────────────────────────────────┘
              │ 2. Runs Test Suite (pytest, public_launch_smoke)
              ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ Deterministic Log Digest (DLD) Engine                        │
  │ (Strips timing lines & memory pointers → Hash SHA-256)      │
  └───────────┬─────────────────────────────────────────────────┘
              │ 3. Automatic Worktree Teardown & Teardown Verification
              ▼
  ┌───────────────────────┐
  │ PE_CLEAN_ROOM_OK      │ (Emits JSON / CLI Ledger)
  └───────────────────────┘
```

### Invariant 3: Deterministic Log Digest (DLD) Engine
Raw test log streams contain non-deterministic noise:
- Execution timing strings (`passed in 7.15s` vs `passed in 6.92s`)
- Memory pointer hex addresses (`object at 0x00000265F4CAFFE0`)
- OS-specific temp paths (`C:\Users\...` vs `/tmp/...`)

The **DLD Engine** normalizes raw test output streams by stripping timing and memory pointer noise prior to calculating `Get-FileHash` / SHA-256. This guarantees that identical test results yield **byte-identical SHA-256 verification receipts** across different machines.

---

## 3. CLI Command Interface & Schema

```bash
# Standard CLI invocation
prismatic verify --clean-room

# Custom remote, branch, and JSON output
prismatic verify --clean-room --remote origin --branch main --json
```

### Machine-Readable Output Schema (`prismatic verify --clean-room --json`)
```json
{
  "marker": "PE_CLEAN_ROOM_VERIFIED_OK",
  "status": "PASS",
  "candidate_head": "4e7fd99b703f6292778bdf4e3c209d734364828e",
  "candidate_tree": "24dca5b1c2020244169f18c1c6b24b9d66463b22",
  "remote_name": "origin",
  "remote_ref_matched": true,
  "clean_room_path": "C:\\Users\\MICHAE~1\\AppData\\Local\\Temp\\prismatic-cleanroom-4e7fd99b",
  "environment_sanitized": true,
  "tests_passed": 480,
  "tests_failed": 0,
  "execution_seconds": 6.76,
  "deterministic_log_sha256": "A702DC9C00072C6E043A3F4247426F26009C2DCBA8C35724C10832211E85C138"
}
```

---

## 4. Subsystem Components

1. **`prismatic/quality/clean_room.py`**: Implementation of `CleanRoomRunner`, `DeterministicLogDigest`, and worktree lifecycle manager.
2. **`prismatic/cli.py`**: Mounts `verify` command and `--clean-room` flag.
3. **`prismatic/quality/tests/test_clean_room.py`**: Unit test suite guaranteeing 100% test coverage.
