# AGY Research Fallback — Direct Execution Pipeline

When the daily Golden Thread Research→Strategy→Execution pipeline hits AGY timeout on web research, pivot to direct execution. Documented from the June 5, 2026 session where all 3 parallel AGY calls timed out after 10+ minutes with zero output.

## The Pattern

1. **AGY research fails** → all `agy --print` calls with web search time out (10+ min, no output)
2. **Do NOT keep waiting** → kill AGY processes, pivot immediately
3. **Read existing artifacts** → outreach lists, strategy docs, analysis files in project research dir
4. **Synthesize from artifacts** → build the assumption analysis and strategy matrix from what's already in Linear + registry + research files
5. **Create tasks directly** → use Linear API to create 3-5 high-impact tasks
6. **Execute top task** → write the deliverable (emails, configs, pages) directly
7. **Add Linear comments** → annotation each task with file paths, next actions, and revenue potential

## Why It Works

The AI Consulting pipeline had extensive preparation (outreach lists, LinkedIn rewrites, email templates, lead magnets) — the bottleneck wasn't research, it was EXECUTION. The Hawaii outreach list had 50 companies with hooks but no personalized emails. Writing those emails directly moved the needle more than another round of research would have.

## Key Heuristics

- **Artifacts ARE the research** — if files exist in `$PRISMATIC_HOME/work/research/<project>/`, read them first before launching AGY
- **Revenue-first task selection** — pick the task that generates revenue conversations fastest (outreach emails > LinkedIn publishing > Cal.com setup)
- **Clear Michael handoffs** — when the next step requires Michael (finding contact names, publishing a profile), make the 🫵 action item impossible to misunderstand
- **Linear comments as status** — each task gets a comment with file path, next action, and revenue potential estimate
