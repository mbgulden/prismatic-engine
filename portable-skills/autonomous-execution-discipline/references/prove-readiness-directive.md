# Prove-Readiness Directive (June 12, 2026)

**Michael's directive:** Reduce interaction to 1hr/day. Get 100x more output. Prove the system works before asking for more trust.

**Pattern:** When building infrastructure, execute visible build steps before proposing more architecture:
1. Create issues → 2. Build scaffolding → 3. Deploy configs → 4. Verify on disk → 5. Report what exists

Never present a plan without artifacts to back it. Michael was explicitly frustrated about his Antigravity Orchestration Hub repo + Synology NAS workspace being overlooked — the system must discover and index existing resources before building new ones.

**Quality Loop (Michael, June 12):** "There needs to be an improvement loop for each task no matter what."

Every task: Execute → Self-Review → Peer Review (AGY/Jules) → Refine → Fred Verify → Done.

No agent marks their own work Done. Only Fred sets `agent:done` after verifying artifacts exist on disk. The dispatcher's label chain enforces this — all agents route through `agent:fred` before `agent:done`.

**Verification checklist for Fred before setting Done:**
1. `ls -la <path>` — artifact exists
2. `wc -l <path>` or `stat` — has content, not empty
3. For code: syntax check passes
4. For content: completeness check against issue requirements
5. Post Linear comment with findings
6. If any fail: issue goes back to agent with specific feedback

**Idle work priority:** Revenue → leads → trust → infra → content. When queue is empty, pick SEO content generation, schema injection, outreach research, listing optimization — default to revenue work, never "nothing to do."
