# Pre-Existing Work Detection Pattern

## When to Check

Before redoing any task from a Linear issue or project registry, verify the work
wasn't already completed and committed. This is a common failure mode in agent
workflows: the work gets done and committed, but the Linear issue state lagged
behind (still in Backlog/Todo).

## Detection Protocol

### 1. Check git log for the issue ID

```bash
git log --oneline --grep="ISSUE-ID"  # e.g., --grep="GRO-1191"
git log --oneline -5 -- path/to/expected/output.md
```

### 2. Check if the expected output file already exists

If the issue says "Save to docs/biome7-audio-tunnel-spec.md" and that file
exists with substantial content, the work was likely already completed.

### 3. Audit the existing output against requirements

Read the file thoroughly. Map each deliverable from the issue description against
what's present. Create a simple status table:

| Requirement | Section in existing output | Status |
|---|---|---|
| X | §N | Complete/Incomplete/Missing |

### 4. Decide based on audit

- **Fully complete** → comment on the issue with the audit, move to In Review,
  swap the agent label for review. Do NOT recreate the work.
- **Partially complete** → identify specific gaps, document them in a Linear
  comment, fill only the gaps. Do NOT redo the whole thing.
- **Absent** → proceed with full execution.

## Example (GRO-1191, June 2026)

The issue asked for a spec at `docs/biome7-audio-tunnel-spec.md`. The file
already existed (291 lines, committed by Fred 1 hour after issue creation).
Audit showed all three deliverables present: directional synth frequencies (§3),
screen filter specs (§4), player guidance mechanics (§5). Moved to In Review
with audit comment instead of recreating.

## Pitfalls

- **Don't trust Linear state alone.** Commits happen faster than Linear updates.
- **Don't blindly overwrite.** If you write a new spec over an existing one, you
  lose the prior work and create merge conflicts.
- **Do verify commit authorship.** If Fred committed it, Fred already did the
  work — mark it for Fred's review, not recreation.
