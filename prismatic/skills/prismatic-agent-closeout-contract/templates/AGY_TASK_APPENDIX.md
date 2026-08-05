
## Mandatory Prismatic Closeout Contract Requirement (v0.2 Standard Spec)

> [!IMPORTANT]
> Your task output MUST produce two synchronized closeout artifacts upon completion:
> 1. `RESULT.md` — Human-readable markdown closeout report.
> 2. `result-packet.json` — Strict machine-readable JSON schema packet.

### 7-Step Iterative Loop Integration
- **Step 3 (EXECUTE — `agent:agy`)**: Worker agent generates `RESULT.md` and `result-packet.json` upon attempt completion.
- **Step 4 (REVIEW — `agent:jules`)**: Reviewer audits exact-head commit/tree SHAs and runs verification commands.
- **Step 5 (FEEDBACK — `agent:fred`)**: If verification fails, feedback payloads extract unresolved blockers from `BLOCKERS`.

### Producer Claim vs Acceptance Decision
- As the authoring worker agent, you set `agent: "agy"` and `PRODUCER_STATUS: "PASS"` (or `"BLOCKED"` / `"ERROR"` / `"PARTIAL"`).
- `ACCEPTANCE_DECISION` MUST default to `"PENDING"` in your submission. Only an independent reviewer or automated Review Factory judge can upgrade `ACCEPTANCE_DECISION` to `"CLEAN"`.

### Required Closeout Fields
Both artifacts must declare and synchronize the following fields:

- `agent`: Agent discriminator (`"agy"`)
- `STATUS`: Task attempt status (`PASS` | `PARTIAL` | `BLOCKED` | `ERROR`)
- `PRODUCER_STATUS`: Producer claim status (`PASS` | `PARTIAL` | `BLOCKED` | `ERROR`)
- `ACCEPTANCE_DECISION`: Peer acceptance state (always `"PENDING"` from producer)
- `TASK_ID`: Linear task identifier (must match `^GRO-[0-9]+$`, e.g. `GRO-4457`)
- `ATTEMPT_ID`: Unique attempt identifier (e.g. `attempt-20260804-01`)
- `BASE_HEAD`: 40-character Git SHA of base commit
- `CANDIDATE_HEAD`: 40-character Git SHA of candidate commit
- `CANDIDATE_TREE`: 40-character Git tree SHA of candidate commit
- `CHANGED_PATHS`: List of exact repository file paths modified
- `COMMAND`: Array of verification command lines executed
- `RESULT`: Verification check status (`PASS` | `FAIL` | `BLOCKED`)
- `LOG`: Relative filepath to execution log artifact
- `LOG_SHA256`: Hex sha256 checksum of log file
- `result_artifacts`: Array of non-empty safe provenance artifact paths
- `SCOPE`: Precise description of verified behavior
- `merge_lane`: Execution lane classification (`dashboard-ui` | `backend-api` | `docs` | `research` | `mixed` | `manual-review`)
- `risk_level`: Change risk assessment (`low` | `medium` | `high`)
- `AD_HOC_OR_CANONICAL`: Suite tier (`ad-hoc targeted` | `canonical suite`)
- `PROOF_CLASSES`: List of verified proof levels (`focused`, `lint`, `format`, `build`, `browser`, `production`)
- `SIDE_EFFECTS`: Map of side-effect booleans (`push`, `pr`, `merge`, `deploy`, `linear_updated`)
- `BLOCKERS`: Array of explicit unresolved blockers (`[]` if none)
- `NOT_CLAIMING`: Array of explicit out-of-scope boundaries
- `NEXT_ACTION`: Next gate action (`merge-ready` | `needs-fred-cleanup` | `needs-human-review` | `blocked` | `superseded`)
- `MARKER`: Standard raw AGY marker (MUST BE EXACTLY `"AGY_TASK_RESULT_PACKET_OK"`)

### Fail-Closed Safety Invariants
Prismatic result ingestion WILL REJECT your completion and set `STATUS=BLOCKED` (`REASON=INVALID_CLOSEOUT_PACKET`) if:
- Any required fields are missing or unknown fields are included.
- Candidate SHA/tree does not match git state.
- `MARKER` is not `"AGY_TASK_RESULT_PACKET_OK"`.
- Artifact paths use unsafe `/tmp/...` locations instead of operator home / repo paths.
- `STATUS` is `"PASS"` while `risk_level` is `"high"` or `merge_lane` is `"manual-review"`.
- `STATUS` is `"BLOCKED"` or `"ERROR"` but `BLOCKERS` list is empty.
