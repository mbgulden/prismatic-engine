---
name: prismatic-agent-closeout-contract
description: Shared machine-enforced closeout reporting contract for AGY tasks in Prismatic Engine (v0.2 Standard Spec), requiring dual synchronized RESULT.md and result-packet.json artifacts with fail-closed CLI validation.
category: agent-governance
---

# Prismatic Agent Closeout Contract Skill (v0.2 Standard Spec)

This skill defines the unified, machine-enforced closeout and reporting contract for all **AGY** execution tasks within the **Prismatic Engine**.

---

## 1. Core Objectives & Invariants

1. **Dual Synchronized Artifacts**: Every task run MUST produce both:
   - `RESULT.md` — Human-readable markdown explanation for Michael and reviewers.
   - `result-packet.json` — Strict machine-readable JSON schema packet for Prismatic engine ingestion.
2. **Producer Claim vs Acceptance Decision**:
   - `agent`: MUST BE EXACTLY `"agy"`.
   - `PRODUCER_STATUS` (`PASS` | `PARTIAL` | `BLOCKED` | `ERROR`) is claimed by the authoring agent.
   - `ACCEPTANCE_DECISION` (always `"PENDING"` at closeout) can ONLY be upgraded to `"CLEAN"` by an independent Review Factory judge or reviewer.
3. **Exact-Head & Safe Provenance**: Candidates MUST provide exact 40-character commit SHA (`CANDIDATE_HEAD`), git tree SHA (`CANDIDATE_TREE`), sha256 checksum digest of execution log (`LOG_SHA256`), and safe artifact paths (`result_artifacts`). Unsafe `/tmp/...` paths are rejected.
4. **Standard Raw Marker**:
   - `MARKER`: MUST BE EXACTLY `"AGY_TASK_RESULT_PACKET_OK"`.
5. **Bidirectional Fail-Closed Validation**: Prismatic workflow ingestion MUST refuse completion (`STATUS=BLOCKED`, `REASON=INVALID_CLOSEOUT_PACKET`) if:
   - Any required fields are missing or unknown properties are present (`additionalProperties=false`).
   - `TASK_ID` does not match standard Linear GRO task regex `^GRO-[0-9]+$`.
   - `STATUS` is `"PASS"` while `risk_level` is `"high"` or `merge_lane` is `"manual-review"`.
   - `STATUS` is `"BLOCKED"` or `"ERROR"` but `BLOCKERS` list is empty.
   - Log sha256 checksum mismatches.

---

## 2. 7-Step Iterative Loop Binding

This closeout contract binds directly into the canonical 7-step iterative loop ([seven-step-loop.md](file:///c:/Users/Michael%20Gulden/Github/prismatic-engine/docs/seven-step-loop.md)):

- **Step 3 (EXECUTE — `agent:agy`)**: Builder agent executes code changes and writes `RESULT.md` + `result-packet.json` at task attempt closeout.
- **Step 4 (REVIEW — `agent:jules`)**: Reviewer validates exact-head commit/tree SHAs, checks safe provenance, and runs verification commands.
- **Step 5 (FEEDBACK — `agent:fred`)**: If verification fails, feedback payloads extract unresolved blockers from `BLOCKERS`.

---

## 3. Required Contract Field Specification

The dual closeout artifacts (`RESULT.md` and `result-packet.json`) must strictly contain the following 25 required fields:

| Field Name | Type / Enum | Description |
| :--- | :--- | :--- |
| `agent` | `"agy"` | Provider discriminator |
| `STATUS` | `PASS` \| `PARTIAL` \| `BLOCKED` \| `ERROR` | Overall task attempt status |
| `PRODUCER_STATUS` | `PASS` \| `PARTIAL` \| `BLOCKED` \| `ERROR` | Producer agent claim |
| `ACCEPTANCE_DECISION` | `PENDING` \| `CLEAN` \| `REJECTED` | Reviewer acceptance state (always `PENDING` from producer) |
| `TASK_ID` | String matching `^GRO-[0-9]+$` | Standard Linear task identifier |
| `ATTEMPT_ID` | Non-empty String | Unique execution attempt ID |
| `BASE_HEAD` | 40-char Hex SHA | Git commit SHA of base state |
| `CANDIDATE_HEAD` | 40-char Hex SHA | Git commit SHA of candidate state |
| `CANDIDATE_TREE` | 40-char Hex SHA | Git tree SHA of candidate state |
| `CHANGED_PATHS` | Array of Strings | Exact repository paths modified |
| `COMMAND` | Array of Strings | Exact verification command lines executed |
| `RESULT` | `PASS` \| `FAIL` \| `BLOCKED` | Automated verification check result |
| `LOG` | File Path | Relative path to execution log file |
| `LOG_SHA256` | 64-char Hex SHA256 | Digest of execution log file |
| `result_artifacts` | Array of Safe Paths | Non-empty list of safe provenance artifact paths |
| `SCOPE` | String | Precise summary of verified behavior |
| `merge_lane` | `dashboard-ui` \| `backend-api` \| `docs` \| `research` \| `mixed` \| `manual-review` | Standard execution merge lane |
| `risk_level` | `low` \| `medium` \| `high` | Risk classification |
| `AD_HOC_OR_CANONICAL` | `ad-hoc targeted` \| `canonical suite` | Suite scope classification |
| `PROOF_CLASSES` | Array of Enums | Verified proof tiers (`focused`, `lint`, `format`, `build`, `browser`, `production`) |
| `SIDE_EFFECTS` | Object (Booleans) | Booleans for `push`, `pr`, `merge`, `deploy`, `linear_updated` |
| `BLOCKERS` | Array of Strings | Unresolved blockers (`[]` if none) |
| `NOT_CLAIMING` | Array of Strings | Explicit negative boundaries |
| `NEXT_ACTION` | `merge-ready` \| `needs-fred-cleanup` \| `needs-human-review` \| `blocked` \| `superseded` | Standard next gate action |
| `MARKER` | `"AGY_TASK_RESULT_PACKET_OK"` | Standard raw AGY marker |

---

## 4. Single Source of Truth & Dual-Tree Synchronization

Per ADR-0001 (`governance/source-of-truth-order.md`), the custom agent tree (`.agents/skills/prismatic-agent-closeout-contract`) is the canonical source.

The built-in engine tree (`prismatic/skills/prismatic-agent-closeout-contract`) is generated deterministically using the tree sync script:

```bash
python .agents/skills/prismatic-agent-closeout-contract/scripts/sync_skill_trees.py
```

---

## 5. Machine Validation & Fixture Harness

Run full fixture validation against shipped PASS/BLOCKED/ERROR reference packets with SHA verification enabled:

```bash
python .agents/skills/prismatic-agent-closeout-contract/scripts/validate_closeout_packet.py .agents/skills/prismatic-agent-closeout-contract/examples
```

---

## 6. Reference Links

- **Appendix Template**: [AGY_TASK_APPENDIX.md](https://prismatic.growthwebdev.com/workspaces?file=prismatic/skills/prismatic-agent-closeout-contract/templates/AGY_TASK_APPENDIX.md)
- **JSON Schema**: [result-packet.schema.json](https://prismatic.growthwebdev.com/workspaces?file=prismatic/skills/prismatic-agent-closeout-contract/schemas/result-packet.schema.json)
- **CLI Validator**: [validate_closeout_packet.py](https://prismatic.growthwebdev.com/workspaces?file=prismatic/skills/prismatic-agent-closeout-contract/scripts/validate_closeout_packet.py)
- **PASS Example**: [RESULT.pass.md](https://prismatic.growthwebdev.com/workspaces?file=prismatic/skills/prismatic-agent-closeout-contract/examples/RESULT.pass.md) & [result-packet.pass.json](https://prismatic.growthwebdev.com/workspaces?file=prismatic/skills/prismatic-agent-closeout-contract/examples/result-packet.pass.json)
- **BLOCKED Example**: [RESULT.blocked.md](https://prismatic.growthwebdev.com/workspaces?file=prismatic/skills/prismatic-agent-closeout-contract/examples/RESULT.blocked.md) & [result-packet.blocked.json](https://prismatic.growthwebdev.com/workspaces?file=prismatic/skills/prismatic-agent-closeout-contract/examples/result-packet.blocked.json)
- **ERROR Example**: [RESULT.error.md](https://prismatic.growthwebdev.com/workspaces?file=prismatic/skills/prismatic-agent-closeout-contract/examples/RESULT.error.md) & [result-packet.error.json](https://prismatic.growthwebdev.com/workspaces?file=prismatic/skills/prismatic-agent-closeout-contract/examples/result-packet.error.json)
