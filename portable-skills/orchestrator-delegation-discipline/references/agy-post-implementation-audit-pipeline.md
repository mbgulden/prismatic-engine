### AGY Post-Implementation Audit Pipeline (PROVEN Jun 2026)

Proven pattern from Darius Star (June 11, 2026). After Ned completes 10+ commits across multiple modules, use this pipeline to verify quality and generate a prioritized action list.

## When to Use
- Ned has made 80+ commits across 20 modules
- User is reporting live bugs in production
- Multiple AGY audits exist that need cross-referencing
- Need a fresh "state of the game" assessment

## The Pipeline

### Step 1: Prepare AGY's prompt
Give AGY the git log, prior audit docs, and the repo. Ask for 4 reports:
1. **Bug verification matrix** — every bug cross-referenced against commits
2. **Module health report** — all modules assessed for quality, size, integration
3. **Game state assessment** — does it actually work? Be brutally honest.
4. **Priority action list** — ordered by impact with file paths and effort estimates

### Step 2: Launch AGY
- `--print` mode, foreground, 600s timeout
- `--add-dir` to the repo
- Expect 8-12 minutes. AGY reads code, doesn't skim.

### Step 3: Create Ned's task
- One Linear issue referencing all 4 report paths
- Priority 1, agent:ned
- Ned reads reports → executes priority list in order

### Step 4: Create missing tasks from audit
After the audit, extract every actionable item that doesn't have a Linear issue yet. AGY's Report 4 (priority action list) is the gold standard.

## Real Example (Darius Star, June 11)
- 84 commits by Ned over 2 days
- AGY produced 4 reports (309 lines total)
- Found: 8/12 bugs fixed, critical boss loop NOT fixed (GRO-1157 falsely marked done)
- Created 6 new tasks from audit gaps
- Ned immediately executed priority fixes
