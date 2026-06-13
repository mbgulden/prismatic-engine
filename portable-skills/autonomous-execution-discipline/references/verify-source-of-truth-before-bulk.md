# Verify Source-of-Truth Before Bulk Execution (June 2026)

## The Pattern

When a pipeline generates bulk tasks (broken link checks, SEO audits, lint fixes), ALWAYS verify the underlying data source BEFORE the tasks flood an agent.

## Case Study: Kai's 91 Phantom Broken Links

**Symptom:** Kai had 91 "Fix broken link" issues. Every week the cron would create more.

**Root cause:** The broken link checker (`aot_broken_link_check.py`) was crawling `$PRISMATIC_HOME/work/active-oahu-tours-mirror` — a DEAD DIRECTORY. The actual mirror lived at `$PRISMATIC_HOME/work/active-oahu-static`.

**Impact:** 85 of 91 issues were FALSE POSITIVES. The files existed, just not where the checker looked. Kai was drowning in phantom tasks for weeks.

**Detection pattern:**
```bash
# Before trusting any automated task generator, spot-check the data source
ls <source_dir>/<sample_path>  →  does it exist?
# If NO, the entire queue is suspect
```

## The Fix Pattern

1. **Verify the data source path** — is the directory actually populated?
2. **Cross-reference a sample** — pick 5-10 generated tasks and verify against reality
3. **Batch-close false positives** — don't make an agent work through them
4. **Fix the generator** — correct the path + add dedup to prevent re-creation
5. **Only then dispatch real work** — to the agent that actually needs it
