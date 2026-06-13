# Batch Delegation Patterns

Proven patterns for parallel `delegate_task` usage, based on 20+ successful invocations in one session (49 issues closed).

## The 3-at-a-Time Pattern

Fire 3 subagents simultaneously, each targeting a different workstream. This maximizes parallelism while staying within the concurrency limit.

### Structure
```python
delegate_task(tasks=[
    {"goal": "Design work (architecture doc, strategy)", "toolsets": ["terminal","file","web"]},
    {"goal": "Implementation work (script, scaffold, template)", "toolsets": ["terminal","file"]},
    {"goal": "Research/audit work (site crawl, API analysis)", "toolsets": ["terminal","web","file"]},
])
```

### Why 3 works
- **Diverse workstreams** avoid file conflicts
- **Complementary latency** — research takes longest, implementation is medium, design is fast
- **Within concurrency limit** — no rejected tasks
- **Parent can synthesize** — 3 results is digestible in one response

## Task Categorization for Batching

| Category | Typical toolsets | Typical duration | Examples |
|----------|-----------------|------------------|----------|
| Architecture/Design | terminal, file | 3-5 min | Migration plans, CI/CD setup, shop design |
| Implementation | terminal, file | 3-8 min | Scripts, scaffolds, schema generation |
| Research/Audit | terminal, web, file | 4-8 min | Site audits, API research, content analysis |
| Documentation | terminal, file | 4-6 min | Templates, runbooks, checklists |

## Context Passing Discipline

Subagents have NO memory of the parent conversation. Every task must include:
- **Absolute paths** to all relevant files/directories
- **Current state** — what's already built, what decisions were made
- **Constraints** — "do NOT connect to APIs", "no package installs"
- **Output path** — exactly where to save the result
- **Cross-references** — if Task B depends on Task A's output, mention it

### Good context example
```
The Astro scaffold is at $PRISMATIC_HOME/work/active-oahu-tours/. 
We have media inventory at docs/active-oahu/media-inventory.json (9,592 files).
Read that JSON for the file structure. Output to docs/active-oahu/media-tags.json.
Do NOT process the actual Synology mount — use the inventory JSON only.
```

### Bad context example
```
Build a tagger for the media library. The files are on the NAS. Make it fast.
```

## When NOT to Batch

- **Critical path fixes** — database crashes, auth errors, broken servers. Handle directly.
- **Sequential dependencies** — Task B must consume Task A's output. Run sequentially or wait.
- **Same file sets** — two workers touching the same files creates merge conflicts.
- **User interaction needed** — subagents can't call `clarify`.

## Timeout Recovery Pattern

When a subagent times out (600s limit, exit_reason: "timeout"):
1. Check if any partial files were created (search_files for expected output paths)
2. If the task was processing a large dataset (698GB media, 9,592 files), the problem is likely the approach, not the agent
3. **Self-implement a simpler version** — switch from exhaustive processing to heuristic/sampling
4. Example: media tagger timed out on full Synology walk → rewrote as folder-heuristic tagger using only the inventory JSON (no filesystem access) — finished in seconds

## Session Throughput Pattern

Proven cadence for maximum throughput:
1. Fire 3 subagents
2. While waiting (2-6 min), review previous results, update Linear, plan next batch
3. When subagents return, synthesize results into one response
4. Close completed issues on Linear
5. Fire next batch
6. Repeat until design ceiling is hit or user pauses

This pattern produced 66 closed issues in one session.
