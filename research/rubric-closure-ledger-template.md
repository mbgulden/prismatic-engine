# Rubric Closure Ledger Template

This template is a reusable ledger/checklist for documenting and closing rubric items within the **Prismatic Engine**. Use it to prove a rubric capability has reached its target score, providing verifiable evidence.

---

## Part 1: Scoring Scheme Reference
*Do not ask Michael or other operators for the scoring scheme. Use the reference below.*

### Core Maturity Scale (0 to 5)
Defined in `okf/standards/enterprise-rubric.md`:

| Score | Grade | Definition | Required Evidence |
| :---: | :--- | :--- | :--- |
| **5** | **Production-grade** | Fully implemented, automated, covered by unit/integration tests, documented in OKF, and used in production daily. | Standard full test suite passes, zero blockers, API/dashboard output matches spec. |
| **4** | **Operational** | Fully functional but with minor operational overhead (e.g., manual overrides, lack of self-healing, minor documentation gaps). | Verify commands run, basic tests pass, but some manual/operator steps remain. |
| **3** | **Emerging** | Partially implemented; functional in specific lanes or test contexts but lacks general coverage or robust error handling. | Local test script passes; not yet integrated into gateway or main loops. |
| **2** | **Designed** | Formally specified and architected in a checked-in design doc or schema, but code is not yet written. | Check-in design specs, manifests, or type files. No functional execution exists. |
| **1** | **Identified** | A known product/engineering gap with high-level consensus, but no formal design doc or task exists. | Reference to a Linear issue or meeting note. |
| **0** | **Blind Spot** | Capability is not considered, planned, or designed. | None. |

### Swarm Scoring Rules (0 to 10 Scale)
Defined in `research/rubric-inventory-matrix.md`:

| Score | Status | Definition / Meaning | Required Action |
| :---: | :--- | :--- | :--- |
| **9 - 10** | **Green** | Production-grade/standard. Implemented, automated, and verified under a CLI/API/dashboard proof surface. | Attach proof artifact or command output. No blocker may remain. |
| **7 - 8** | **Green/Yellow** | Development-complete but with minor UX, edge-case, documentation, portability, or repeatability gaps. | Record the gap and create/attach a concrete follow-up child task. |
| **5 - 6** | **Yellow** | Partial implementation. Works in local script/CLI but lacks full Gateway/dashboard/operator integration. | Record the integration gap, blocker, owner, and next action. |
| **3 - 4** | **Yellow/Red** | Blueprint/design only. Specs or manifests exist but no loadable/enforced implementation is proven. | Create implementation child task before claiming readiness. |
| **1 - 2** | **Red** | Acknowledged/backlogged. Requirement exists in spec or ticket but no execution proof. | Create scoped implementation and evidence tasks. |
| **0** | **Red** | Blind spot. Capability not represented in rubric or evidence surface. | Add missing rubric row and owner before scoring. |

---

## Part 2: Rubric Closure Ledger Template

> Dispatch success is not output acceptance. Do not mark the issue Done until the PR/artifact is reviewed, accepted, and backed by the evidence below.
*Copy the section below into the Linear issue description, PR description, or a dedicated Markdown report in the `reports/` folder (e.g., `reports/rubric-closure-gro-xxxx.md`).*

```markdown
# Rubric Closure Ledger: [Rubric ID] — [Rubric Item Title]

### 1. General Metadata
- **Issue Link:** [Insert Linear issue link here, e.g., https://linear.app/team/issue/GRO-XXXX]
- **Rubric ID / Category:** [e.g., A1 - Local Installation]
- **Owner:** [e.g., agent:fred, agent:ned, agent:agy]
- **Target Score:** [e.g., 10 on the accepted 0–10 rubric]
- **Current Score:** [e.g., 10 on the accepted 0–10 rubric]

### 2. PR & Branch Info
- **Branch Name:** [e.g., feature/GRO-XXXX-local-install]
- **Pull Request URL/Number:** [e.g., PR #123 or https://github.com/mbgulden/prismatic-engine/pull/123]

### 3. Acceptance Proof & Verification
Provide explicit details demonstrating how the acceptance criteria are met:
- **Description of Changes:** [Detail what was created or changed to satisfy the rubric dimension]
- **Files Created/Modified:**
  - `path/to/file_name.py`
  - `path/to/test_file.py`

#### A. Verifier Command & Output
- **Command:** `[Insert exact CLI/validation command here, e.g., PYTHONPATH=. python3 scripts/public_launch_smoke.py]`
- **Exit Code:** [e.g., 0]
- **Output Marker / Excerpt:**
  ```text
  [Insert output excerpt here showing passing verdict or status code, e.g., PUBLIC_LAUNCH_SMOKE_OK]
  ```

#### B. Dashboard / API Proof
- **API Endpoint:** [e.g., GET /api/plugins/governance]
- **Payload Response / UI Proof:**
  ```json
  [Insert API JSON response or dashboard render verification details here]
  ```

### 4. Canonical Evidence Contract Alignment
Must align with `docs/execution-evidence-contract.md`:
- **Verification Status:** `verified`
- **Verification Scope:** `canonical_full_suite` | `ad_hoc_targeted` | `live_integration`
- **Failure Category:** `none`
- **Cleanup Status:** [e.g., temp files removed, lock file released]
- **Done Gate Result:** `done`

### 5. Final Reviewer Signoff
- **Reviewer:** [e.g., agent:agy, agent:fred, or manual review]
- **Signoff Date:** [YYYY-MM-DD]
- **Verdict:** APPROVED
- **Review Notes:** [Optional notes from the reviewer validating the evidence]
```
