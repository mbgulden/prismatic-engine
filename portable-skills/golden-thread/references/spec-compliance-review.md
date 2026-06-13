# Spec-Compliance Review (Second Witness Pattern)

When a project has a formal architecture blueprint or design spec, run a compliance audit of all related Linear issues. This is the "Second Witness" review — independent verification that implementation matches specification.

## Trigger
- Project has a formal architecture doc (e.g., `specs/core-architecture-v1.md`)
- Issues are in "In Review" state waiting for verification
- Cron-driven review terminal (every 30 min)

## Protocol

### Step 1: Load the Blueprint
Read the architecture spec file. Note every section that defines required structure, interfaces, directories, or behavior. This is your compliance checklist.

### Step 2: Pull All Issues in Scope
Query Linear for every issue in the epic/project range. Use `team(id:...) { issues(first: N) }` and filter client-side by number range. Don't rely on state filters — you need to see all states.

### Step 3: Verify Claims Against Disk
For each issue, cross-reference what the issue CLAIMS against what actually EXISTS:
- **Directories**: `ls` the expected paths. `prismatic/interface/` claimed but doesn't exist? That's a gap.
- **Symlinks**: `readlink -f` and `ls -la`. Broken symlink = broken deliverable.
- **Files**: `search_files` for expected deliverables. Design spec exists but zero implementation code? That's design-only.
- **Configuration**: `cat` the config files. Does the config match the blueprint's required sections?
- **Hardcoded paths**: `grep -rl '$PRISMATIC_HOME'` against the target directory. Count remaining hardcoded refs.
- **Build artifacts**: Check `dist/`, `build/`, wheel files. Packaging config exists but no build was run?

### Step 4: Rate Each Issue
Three verdicts:
- **APPROVED** — deliverable matches blueprint spec, no gaps found
- **NEEDS_CHANGES** — partial delivery, missing pieces, or conflicts with blueprint
- **BLOCKED** — cannot proceed due to dependency on another incomplete issue

### Step 5: Identify Critical-Path Blocker
Among all NEEDS_CHANGES issues, find the one that blocks everything else. Common pattern: path parameterization issues block all other issues from being correct. Flag this explicitly in the report.

### Step 6: Create Fix Issues
For each NEEDS_CHANGES or BLOCKED item, create a child Linear issue with:
- Title: `[FIX] <parent-identifier>: <one-line description>`
- Description structured as:
  ```
  ## Found by Second Witness
  **Review:** <ISO timestamp>
  **Issue:** <specific gap>
  **Required fix:** <numbered list of concrete actions>
  Parent: <parent-identifier>
  ```
- Assign to `agent:fred` for implementation gaps or `agent:agy` for design/spec gaps
- Set parent issue to the issue being fixed

### Step 7: Produce Timestamped Report
```
## SECOND WITNESS REVIEW — <ISO timestamp>

### Issues Reviewed: N
| Issue | State | Verdict | Key Findings |
|-------|-------|---------|--------------|

### Project Health
- Tasks complete: X/N
- Tasks in progress: Y
- Blockers: [list]

### Fix Tasks Created
| Fix Issue | Parent | Assignee |
|-----------|--------|----------|

### Cross-Cutting Concern
[Critical-path blocker analysis]
```

## Common Gap Patterns
- **Design-only**: Spec exists, zero code. `prismatic/interface/` and `prismatic/core/` directories missing despite detailed design doc.
- **Symlink broken**: `active` symlink points to nonexistent target. `versions/` directory empty — no builds promoted.
- **Hardcoded paths survive**: Migration script analyzed but never executed. 277 `$PRISMATIC_HOME` refs remain.
- **Partial export**: 7 of 15 skills exported. The export script exists but wasn't run to completion.
- **Analysis without action**: Security scanner analysis identifies all patterns but zero sanitized versions produced.

## Fix Task Assignment Rules
- Implementation gaps (missing files, broken symlinks, unexecuted scripts): `agent:fred`
- Design/spec gaps (wrong format, missing fields, bad architecture): `agent:agy`
- Orchestration gaps (pipeline step missing, dependency deadlock): `agent:fred`
