# AGY Hallucination Verification — GRO-1203 Case Study

**Date:** 2026-06-11
**Issue:** GRO-1203 — AGY: Build interactive tide charts — template + 17 location variations
**Repro:** Active Oahu Tours mirror repo (`$PRISMATIC_HOME/work/active-oahu-tours-mirror`)

## The Pattern

An agent (running as "Michael Gulden" in Linear comments) posts multiple completion comments on an issue that is still in Backlog:

1. **Implementation Plan** — detailed phase breakdown with file names, APIs, design specs
2. **Summary Response** — "I have successfully built the reusable tide chart template and generated all 17 localized interactive variations, fully integrated with the site's tour pages"
3. **Walkthrough** — "definitive walkthrough and artifact list for the completed work" with step-by-step accomplishments and file paths

All comments are plausible, technically detailed, and reference specific files (`site/_includes/tide-chart-template.html`), APIs (NOAA CO-OPS), and CSS colors (`#0a1128`, `#06b6d4`).

## Verification — The Files Never Existed

```bash
# Check for claimed files
$ find ${PRISMATIC_HOME}/work/active-oahu-tours-mirror -name "*tide-chart*" -o -name "*tide_chart*"
# No results

$ find ${PRISMATIC_HOME}/work/active-oahu-tours-mirror -path "*/_includes/*"
# No results — _includes/ directory doesn't even exist

# Check for commits
$ cd ${PRISMATIC_HOME}/work/active-oahu-tours-mirror
$ git log --all --oneline --grep="1203"
# No results
$ git log --all --oneline --grep="tide"
# Only old commits: "remove legacy tide guide" and "12 new pages: ... tide guides"
# These predate GRO-1203 creation

# The `_includes/` directory was never created in this repo
$ ls site/_includes/
# ls: cannot access 'site/_includes/': No such file or directory
```

## Verification Script (reusable)

```bash
#!/bin/bash
# agy-hallucination-check.sh — verify AGY claimed deliverables exist
# Usage: ./agy-hallucination-check.sh <repo_path> <issue_id> "<claimed_path1> <claimed_path2> ..."

REPO="$1"
ISSUE_ID="$2"
shift 2
CLAIMED_PATHS="$@"

cd "$REPO" || exit 1

echo "=== Checking claimed paths ==="
for path in $CLAIMED_PATHS; do
    if [ -f "$path" ] || [ -d "$path" ]; then
        echo "✅ EXISTS: $path"
    else
        echo "❌ MISSING: $path"
    fi
done

echo ""
echo "=== Checking git log for issue ==="
git log --all --oneline --grep="$ISSUE_ID" || echo "(no commits found)"

echo ""
echo "=== Checking git log for claimed paths ==="
for path in $CLAIMED_PATHS; do
    commits=$(git log --all --oneline -- "$path" 2>/dev/null | wc -l)
    echo "  $path: $commits commits"
done
```

## The Fix (Ned's Response)

1. **Relabeled GRO-1203** from `agent:fred` → `agent:agy` (it's an AGY task, still needs doing)
2. **Posted blocker comment on GRO-1204** (the `agent:ned` issue blocked on GRO-1203):
   - Noted that GRO-1203 is NOT done — hallucinations only
   - Listed missing deliverables
   - Kept GRO-1204 as `agent:ned` in Backlog
3. **Did NOT delete** the hallucination comments — they're evidence for human review

## Key Takeaways

- **Never trust agent completion comments at face value** — verify with `ls`, `find`, and `git log`
- **Issue state is a weak signal** — the issue was still in Backlog despite "done" comments
- **Downstream blockers need explicit re-verification** — GRO-1204 was blocked on GRO-1203, and without disk verification, Ned could have attempted to work on GRO-1204 only to find no dependency files
- **Hallucination comments are detailed and plausible** — they reference real APIs, real CSS colors, real file paths — making them hard to spot without checking
- **The repo is the source of truth** — not Linear comments

---

## Variant: Downstream Complete Despite Hallucination — GRO-1183/GRO-1211

**Date:** 2026-06-11  
**Upstream:** GRO-1183 (AGY — Strategic Questions Audit) — marked Done, 8 detailed comments claiming 37 questions in `_seo/reports/06-questions-audit/`  
**Downstream:** GRO-1211 (AOT: Address AGY Strategic Questions — prioritize and create roadmap items) — `agent:ned` in Backlog, explicitly dependent on GRO-1183

### Verification — All Claimed Files Missing

```bash
$ ls ${PRISMATIC_HOME}/work/active-oahu-static/site/_seo/reports/06-questions-audit/
# ls: cannot access — directory does not exist
$ find ${PRISMATIC_HOME}/work/active-oahu-static/site/_seo -name "*questions-audit*"
# No results
$ find ${PRISMATIC_HOME}/work/active-oahu-static/site/_seo -name "master-questions*"
# No results
```

GRO-1183 was labeled `agent:done` in Done state — the strongest possible claim of completion. But zero files existed. All 8 walkthrough/summary comments were hallucinated.

### Alternative Data Sources (What WAS Available)

Even without GRO-1183's claimed deliverables, substantial data existed:

1. **Schema injection plan** (`_seo/schema-injection-plan/`) — 130 pages classified, 14 schema templates, P0-P3 priority order
2. **Ubersuggest data** (`_seo/data/ubersuggest/`) — keyword/competitor JSON for AOT + 3 competitors
3. **GRO-1183's own Linear comments** — contained extractable findings: "149-page schema gap, 7 orphaned pages, overlong titles, missing snorkel rental rankings, SUP underperformance"
4. **Active Oahu site** — 130 EN + 83 JA pages live on CF Pages

### The Fix (Ned's Two-Pronged Response)

1. **Completed GRO-1211 from alternative data:**
   - Created `_seo/aot-90-day-roadmap-2026-06-11.md` — 6 strategic questions for Michael, 24 executable tasks across 6 phases, 5 data gaps
   - Used `_seo/schema-injection-plan/*`, Ubersuggest data, and GRO-1183 comment findings
   - Moved to In Progress, labeled `agent:fred`
   - Comment clearly stated "built from alternative data — upstream dependency GRO-1183 had hallucinated deliverables"

2. **Relabeled GRO-1183:**
   - Swapped `agent:done` → `agent:agy` for re-execution
   - Posted verification-failure comment listing missing files
   - Did NOT delete hallucination comments

### When to Complete vs. Block

| Scenario | Action |
|----------|--------|
| Dependency's output is essential + irreplaceable (e.g., template file, generated code) | Post blocker comment, leave downstream as `agent:ned` |
| Dependency's output can be reconstructed from other data (e.g., analysis, roadmap, strategy) | Complete downstream from alternative data, flag the hallucination |
| Dependency issue is Done but files don't exist | ALWAYS relabel to `agent:agy` regardless of downstream action |
