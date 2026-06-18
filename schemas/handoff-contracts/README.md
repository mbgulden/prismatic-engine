# Handoff Contracts — JSON Schema Validation

**Location:** `schemas/handoff-contracts/`

Four JSON Schema files that define structural contracts for agent-to-agent task handoff in the Prismatic Engine. These schemas prevent malformed input contexts from causing implementation failures (GRO-1852, advancing GRO-549).

## Schema Files

| File | Validates | Handoff Direction | 
|------|-----------|-------------------|
| `agent-contract-schema.json` | **AgentContract** — worker scope, lanes, execution bounds | `SwarmPlanner → Worker Executor` |
| `research-output-schema.json` | **AGY Research Output** — structured research with findings, sources, recommendations | `AGY → Jules / Hermes` |
| `review-report-schema.json` | **Review Report** — code review outcomes, issue details, scores | `Jules / AGY → Worker` |
| `loopback-feedback-schema.json` | **Loopback Feedback** — refinement instructions for rework cycles | `Reviewer → Worker (re-enter loop)` |

## Validator

```bash
python3 schemas/handoff-contracts/validate_handoff.py <schema-type> <json-file>
```

Examples:
```bash
# Validate an AgentContract before dispatching a worker
python3 validate_handoff.py agent-contract /tmp/contract.json

# Validate AGY research output before routing to Jules
python3 validate_handoff.py research-output /tmp/agy-output.json

# Validate a review report before feeding back to worker
python3 validate_handoff.py review-report /tmp/review.json

# Validate loopback feedback before re-injecting into worker context
python3 validate_handoff.py loopback-feedback /tmp/feedback.json
```

Exit code: `0` = valid, `1` = invalid (details printed to stdout).

## Integration Points

### AGY → Jules Handoff (Primary Use Case)

When AGY completes research, it should produce output conforming to `research-output-schema.json`. This output is embedded into the AgentContract's `handoffContext` field (which conforms to `agent-contract-schema.json`). The downstream implementation agent (Jules/Hermes) reads `handoffContext.researchSummary` and `handoffContext.keyFindings` before starting work, avoiding redundant re-research.

### 7-Step Loop Integration

- **Step 1 (Decompose):** SwarmPlanner produces `AgentContract[]` — validate each contract with `agent-contract-schema.json` before proceeding to Step 2.
- **Step 4 (Review):** Jules/AGY produces a ReviewReport — validate with `review-report-schema.json` before routing to human or loopback.
- **Step 5 (Feedback):** Review system produces LoopbackFeedback — validate with `loopback-feedback-schema.json` before injecting into worker context.

## Schema Versioning

All schemas use `$id` with the full GitHub URL format. When schemas change, update the version in `agentVersion` or add a new schema file with a version suffix (e.g. `agent-contract-v2-schema.json`). The validator's `SCHEMA_FILES` dict maps short names to filenames — update it when adding new schemas.
