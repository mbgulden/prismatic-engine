---
name: subagent-claim-verification-gate
description: "Mandatory Subagent & Task Claim Verification Protocol: Enforces zero unverified subagent claims, empirical handle checks (file stat, process exit code, SHA256 digest), remote push pre-checks, self-verifying evidence packets, and variable isolation across environment profiles."
category: agent-governance
---

# Subagent Claim Verification Gate & Receipt Protocol (v3 Standard)

## Purpose

Eliminate self-report vulnerability, unverified subagent claims, and unreachable remote commits. This skill enforces that **NO output, assertion, or claim** from a subagent (`research`, `self`, background task, or external agent) is presented as fact to the user or reviewer until independently verified by the primary agent through empirical runtime tools (`view_file`, `run_command` hash check, `Get-FileHash`, `git ls-remote`, or process exit code checks).

---

## Trigger

Loaded automatically on every task involving subagent delegation (`invoke_subagent`), background task management (`run_command` async, `manage_task`), external HTTP/file mutations, PR handoffs, or candidate state verification.

---

## The 3 Process Retrospective Lessons (George Audit Synthesis)

1. **Environment-Coupled Tests ≠ Fabrication**: Isolate variables (profile paths, `HERMES_HOME`, environment variables, pythonpath) before assuming test output is fabricated. Fragility across environments must be fixed in code rather than papered over.
2. **Verification Gates Must Verify Themselves**: A skill or packet that enforces verification MUST comply with its own rules prior to issuance.
3. **Exact Remote Snapshot Guard**: Every evidence packet MUST embed the exact `git ls-remote origin <branch>` snapshot captured at packet generation time to eliminate SHA staleness.

---

## The 6 Invariants of Subagent & Handoff Verification

| Invariant | Violation (Forbidden Practice) | Mandatory Correct Behavior |
| :--- | :--- | :--- |
| **1. Remote Push Pre-Check** | Generating a handoff packet or declaring work ready before pushing candidate branch to `origin`. | `PRE_PACKET_REMOTE_PUSH_CHECK`: Always execute `git push -u origin <branch>` and verify `git ls-remote origin <branch>` matches exact HEAD commit SHA before writing handoff packet. |
| **2. Zero Unverified Self-Reports** | Accepting a subagent's statement (e.g. *"file written successfully"* or *"all tests passed"*) without independent verification. | The primary agent MUST run `Test-Path`, `Get-FileHash`, `view_file`, or re-run verification commands to confirm empirical state on disk. |
| **3. Dual-Tree Git Tracking** | Keeping `.agents/` rules or skills in non-repo roots or leaving them un-tracked in Git. | `DUAL_TREE_GIT_TRACKING_CHECK`: Ensure `.agents/AGENTS.md` and `.agents/skills/<skill>/SKILL.md` are present in `git ls-tree -r HEAD .agents` inside the target repo root. |
| **4. Independent SHA-256 Hash Proof** | Reporting artifact file creation or test log completion using unverified or stale hash strings. | The primary agent MUST recompute the SHA-256 digest of fresh test log streams immediately after execution using `Get-FileHash` or Python `hashlib`. |
| **5. Process Exit Code Authority** | Assuming a command succeeded because stdout contains text, while ignoring exit code or missing markers. | Always verify `ExitCode == 0` AND the presence of expected completion markers (e.g. `PUBLIC_LAUNCH_SMOKE_OK`). |
| **6. Cross-Environment Portability** | Testing only under default user profiles without validating non-standard profile environments. | Verify tests dynamically evaluate environment variables (`HERMES_HOME`, `HERMES_PROFILE`) without import-time module caching, guaranteeing 100% cross-profile portability. |

---

## 5-Step "One-Shot" Handoff Execution Workflow

```text
 1. LOCAL TDD & FIXES     ─────▶ Run tests & fix platform edge cases (fcntl/pwd/paths)
           │
           ▼
 2. DUAL-TREE SYNC        ─────▶ Track .agents/ & skills/ in repository Git root
           │
           ▼
 3. REMOTE PUSH           ─────▶ Push branch to origin FIRST (git push -u origin <branch>)
           │
           ▼
 4. VERIFY REMOTE REF     ─────▶ Prove git ls-remote origin <branch> returns exact HEAD SHA
           │
           ▼
 5. GENERATE LOG & PACKET ─────▶ Run verification, capture log SHA-256, write packet
```
