# Ned Completion Comment Template

Use this structure when posting completion comments on Linear issues. The format signals to Fred (reviewer) exactly what was done, what remains, and what integration hooks are needed.

## Full Execution Template (task fully done)

```
## ✅ Ned Execution Complete — [ISSUE_ID] [Short Title]

### What Was Built

| File | Lines | Purpose |
|------|-------|---------|
| `path/to/file.js` | NNN | One-line description |
| `path/to/other.js` | NNN | One-line description |

### Integration Points
- **file_A.js ↔ file_B.js**: How they connect
- **file_C.js ↔ existing_system**: How it hooks in

### Design Decisions
1. **Decision 1**: Why
2. **Decision 2**: Why

### Verification
- [x] Syntax verified (new Function() / node --check)
- [x] Committed: `<commit SHA>`
```

## Partial Execution Template (safe parts done, rest flagged)

```
## ✅ Ned Partial Execution — [ISSUE_ID] [Short Title]

### What Was Built

| File | Lines | Purpose |
|------|-------|---------|
| ... | ... | ... |

### ⚠️ Remaining (Interactive Verification Needed)

| Step | Risk | Status |
|------|------|--------|
| 1. Build core files | LOW | ✅ Done |
| 2. Syntax verify | LOW | ✅ Done |
| 3. Integration wiring | MEDIUM | ⚠️ Flagged |
| 4. Browser/gameplay verification | HIGH | ⚠️ Flagged |

### Integration Hook Locations (for interactive session)

- **Hook point 1** (~file.js line NNN): `FunctionCall(args)`
- **Hook point 2** (~file.js line NNN): `OtherCall(args)`

### Verdict
Partial execution complete. [What's done] needed interactive session with [browser/GPU/playtesting].
```

## Flag-Only Template (nothing executed, full assessment)

```
## 🚩 Flagged for Interactive — [ISSUE_ID]

### Why This Needs Interactive Attention

| Criterion | Assessment |
|-----------|------------|
| File size | ... |
| Testability | ... |
| Isolation | ... |
| Risk | ... |
| Dependencies | ... |

### Current Architecture
```
[code structure, load order, line references]
```

### Known Hazards
1. **Hazard**: Explanation
2. **Hazard**: Explanation

### Recommended Interactive Approach
1. Step 1
2. Step 2
3. ...

### Verdict
**Cannot execute autonomously.** [Why.]
```

## Rules
- Always include commit SHA when code was committed
- Always use `[x]` / `[ ]` checkboxes for verification steps
- Always list integration hook locations with approximate line numbers
- Keep the verdict to one sentence — Fred scans these
- Use the `⚠️ Remaining` table for partial executions — makes the handoff explicit
