# Parallel Research Audit Pattern

## When to Use

When a task requires cross-referencing two LARGE independent information sets to produce a gap analysis — typically "audit agent skills against research output" or "compare capability inventory against requirements."

Use `delegate_task` with 2+ parallel subagents to collect the datasets simultaneously, then synthesize in the parent.

## Pattern

1. **Define the two datasets** — e.g., "all AGY research docs" and "all agent skills across 4 profiles"
2. **Launch parallel subagents** via `delegate_task(tasks=[...])`:
   - **Subagent A**: Find and inventory dataset A (search + read + summarize)
     - toolsets: `["terminal", "file"]` — filesystem access for search/read
     - Deliverable: structured summary with paths, descriptions, and key findings
   - **Subagent B**: Find and inventory dataset B (search + read + summarize)
     - toolsets: `["terminal", "file"]`
     - Deliverable: structured inventory with names, descriptions, counts
3. **Parent synthesizes**: cross-reference A against B, identify gaps, flag redundancies
4. **Deliver findings**: post as Linear comment or write to file

## Why It Works

- **Zero shared state**: subagents access different directories/files — no conflicts
- **Parallel I/O**: both subagents scan filesystems simultaneously
- **Parent does the reasoning**: synthesis is the hard part; data collection is mechanical
- **~2x speedup vs serial**: 3-4 minutes wall time vs 6-8 minutes sequentially

## Example: GRO-897 (Jun 2026)

Task: "Do Ned/Fred/Kai/Autobot need new skills from AGY research batch?"

```
Subagent A: Find all 30 AGY/Jules research docs across 3 locations
  - ~/work/agentic-swarm-ops/docs/ (10 docs)
  - ~/work/prismatic-engine/reports/ (18 docs)  
  - Synology Hub (3 docs)
  - Output: structured table with paths + summaries

Subagent B: Inventory 155 skills across 4 Hermes profiles
  - Ned: 90 skills from ~/.hermes/profiles/ned/skills/
  - Fred: 44 skills from ~/.hermes/profiles/fred/skills/
  - Kai: 20 skills from ~/.hermes/profiles/kai/skills/
  - Autobot: 1 skill from ~/.hermes/profiles/autobot/skills/
  - Output: per-agent tables with skill names + descriptions

Parent: Cross-reference → 4 gaps found → 4 new skills recommended
```

## Contrast with `parallel-delegate-synthesis.md`

| Aspect | parallel-delegate-synthesis | parallel-research-audit |
|--------|---------------------------|------------------------|
| Subagent role | Produce deliverables | Collect data |
| Source | Same source docs | Different datasets |
| Subagent writes | Output files (deliverables) | Summaries (intermediate) |
| Parent role | Verify output files exist | Synthesize cross-reference |
| Output | N independent files | 1 gap analysis |

## Pitfalls

- **Don't ask subagents to cross-reference**: subagents only collect THEIR dataset. The cross-reference happens in the parent. Asking a subagent to reference the OTHER dataset means duplicating context across both — defeats the purpose.
- **Be specific about what to summarize**: "read and inventory all skills" works. "Analyze skills for gaps" doesn't — that requires the OTHER dataset too.
- **Subagents may miss files**: subagents search independently. If subagent A misses a research doc that's relevant to subagent B's inventory, the parent won't catch it. Use broad search patterns (`search_files` with multiple keywords, full-tree scans).
- **Cap at 3 subagents**: `max_concurrent_children` limits parallelism. For most audits, 2 subagents is optimal — datasets are naturally bipartite.
- **Subagent summaries are self-reports — verify critical findings**: if a subagent claims "0 skills found for Autobot," verify by checking the profile directory yourself. Self-reported counts can be wrong.
