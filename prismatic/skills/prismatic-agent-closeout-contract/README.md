# Prismatic Agent Closeout Contract Skill (`prismatic-agent-closeout-contract`)

**Version**: 0.2.0
**Author**: Prismatic Engine
**Category**: agent-governance
**Target Agent**: `agent:agy`
**Standard Raw Marker**: `AGY_TASK_RESULT_PACKET_OK`

---

## Overview

The `prismatic-agent-closeout-contract` skill defines the machine-enforced closeout and reporting contract for all **AGY** execution tasks within the **Prismatic Engine**.

It guarantees proof of work by enforcing a dual synchronized artifact requirement (`RESULT.md` + `result-packet.json`) with strict schema validation, log sha256 verification, and safe artifact provenance checks.

---

## Core Operational Invariants

1. **Dual Synchronized Artifacts**:
   - `RESULT.md`: Human-readable closeout markdown report.
   - `result-packet.json`: Machine-readable JSON schema packet.

2. **Producer Claim vs Acceptance Decision**:
   - The authoring worker agent (`agent: "agy"`) sets `PRODUCER_STATUS: "PASS"` (or `"BLOCKED"` / `"ERROR"` / `"PARTIAL"`).
   - `ACCEPTANCE_DECISION` MUST default to `"PENDING"` in worker closeout packets. Only an independent reviewer or automated Review Factory judge can upgrade `ACCEPTANCE_DECISION` to `"CLEAN"`.

3. **25 Mandatory Schema Fields**:
   `agent`, `STATUS`, `PRODUCER_STATUS`, `ACCEPTANCE_DECISION`, `TASK_ID` (`^GRO-[0-9]+$`), `ATTEMPT_ID`, `BASE_HEAD`, `CANDIDATE_HEAD`, `CANDIDATE_TREE`, `CHANGED_PATHS`, `COMMAND`, `RESULT`, `LOG`, `LOG_SHA256`, `result_artifacts`, `SCOPE`, `merge_lane`, `risk_level`, `AD_HOC_OR_CANONICAL`, `PROOF_CLASSES`, `SIDE_EFFECTS`, `BLOCKERS`, `NOT_CLAIMING`, `NEXT_ACTION`, `MARKER`.

4. **Fail-Closed Execution Gate**:
   Validation failures emit `STATUS=BLOCKED` and `REASON=INVALID_CLOSEOUT_PACKET`. `agent.completed = true` does NOT grant merge readiness while `ACCEPTANCE_DECISION == "PENDING"` (`pending_substitution_policy: hold_merging_only`).

---

## Key Artifact References

- **Prompt Appendix Template**: `templates/AGY_TASK_APPENDIX.md`
- **JSON Schema**: `schemas/result-packet.schema.json`
- **CLI Validator**: `scripts/validate_closeout_packet.py`
- **Tree Sync Script**: `scripts/sync_skill_trees.py`
- **Reference Fixtures**: `examples/` (`RESULT.pass.md`, `result-packet.pass.json`, etc.)

---

## Validation Commands

```bash
# Validate closeout packet in target folder
python3 scripts/validate_closeout_packet.py /path/to/closeout/output

# Run full fixture test harness with log SHA verification
python3 scripts/validate_closeout_packet.py --test-fixtures examples
```
