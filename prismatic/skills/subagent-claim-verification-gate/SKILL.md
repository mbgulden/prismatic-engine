---
name: subagent-claim-verification-gate
description: "Mandatory Subagent & Task Claim Verification Protocol: Enforces zero unverified subagent claims, empirical handle checks (file stat, process exit code, SHA256 digest), and evidence receipt generation for all agent/subagent executions."
category: agent-governance
---

# Subagent Claim Verification Gate & Receipt Protocol

## Purpose

Eliminate self-report vulnerability and unverified subagent claims. This skill enforces that **NO output, assertion, or claim** from a subagent (`research`, `self`, background task, or external agent) is presented as fact to the user until independently verified by the primary agent through empirical runtime tools (`view_file`, `run_command` hash check, `Get-FileHash`, or process exit code checks).

---

## Trigger

Loaded automatically on every task involving subagent delegation (`invoke_subagent`), background task management (`run_command` async, `manage_task`), external HTTP/file mutations, PR handoffs, or candidate state verification.

---

## The 5 Invariants of Subagent Verification

| Invariant | Violation (Forbidden Practice) | Mandatory Correct Behavior |
| :--- | :--- | :--- |
| **1. Zero Unverified Self-Reports** | Accepting a subagent's statement (e.g. *"file written successfully"* or *"all tests passed"*) without independent verification. | The primary agent MUST run `Test-Path`, `Get-FileHash`, `view_file`, or re-run verification commands to confirm empirical state on disk. |
| **2. Verifiable Handle Requirement** | Allowing subagents or tasks to return vague summaries without concrete handles (file path, line range, commit SHA, HTTP status, process exit code). | Subagents MUST return verifiable handles (absolute path, commit SHA, 64-char log SHA-256 digest). Outputs lacking handles are flagged as `PRODUCER_CLAIM_UNVERIFIED`. |
| **3. Independent SHA-256 Hash Proof** | Reporting artifact file creation or test log completion using unverified hash strings. | The primary agent MUST compute the SHA-256 digest of created/modified files or test log streams using `Get-FileHash` or Python `hashlib`. |
| **4. Process Exit Code Authority** | Assuming a command succeeded because stdout contains text, while ignoring exit code or missing markers. | Always verify `ExitCode == 0` AND the presence of the standard completion marker (e.g. `PUBLIC_LAUNCH_SMOKE_OK`). |
| **5. Working Tree Side-Effect Fencing** | Assuming a subagent or script ran cleanly without verifying working tree mutations. | Compute pre-execution and post-execution status byte-hashes (`git status --porcelain=v2 -z --untracked-files=all`) to prove zero unintended side effects. |

---

## Verification Protocol Workflow

Before reporting any subagent or background task result to the user:

```text
[Subagent Executed / Task Completed]
               │
               ▼
 1. Extract Verifiable Handles (Paths, SHAs, Exit Codes)
               │
               ▼
 2. Run Independent Verification (Stat file, calculate SHA-256, verify Exit Code 0)
               │
               ▼
 3. Generate Evidence Ledger & Append Log Digest
               │
               ▼
 4. Output Machine-Verified Report to User
```

---

## Standardized Subagent Verification Ledger

```text
=== SUBAGENT CLAIM VERIFICATION LEDGER ===
SUBAGENT_TYPE:       <research|self|task>
SUBAGENT_ID:         <conversation_id|task_id>
CLAIMED_RESULT:      <PASS|FAIL|BLOCKED>
VERIFIABLE_HANDLE:   <absolute path | URL | commit SHA>
INDEPENDENT_STAT:    EXISTS (Size: <N> bytes)
SHA256_DIGEST:       <64-character hex SHA-256 string>
PROCESS_EXIT_CODE:   0
SIDE_EFFECTS:        False (Pre/Post status hash match)
VERIFICATION_STATUS: VERIFIED_GROUND_TRUTH
```
