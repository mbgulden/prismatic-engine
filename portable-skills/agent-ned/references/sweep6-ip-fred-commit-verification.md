# Sweep 6: In Progress agent:fred with Committed Work

**When to run:** After sweeps 1–5, every maintenance cycle. This catches issues
that are In Progress with `agent:fred` label where a prior Ned session committed
the work but never closed the Linear card.

## Detection (two-pass)

### Pass 1: git log cross-reference
For every In Progress `agent:fred` issue (excluding `requires:human-approval`):

```bash
for id in GRO-XXXX ...; do
  for repo in /home/ubuntu/work/prismatic-engine /home/ubuntu/work/agentic-swarm-ops \
              /home/ubuntu/work/active-oahu-tours-mirror /home/ubuntu/work/darius-star \
              /home/ubuntu/work/OpenHumanDesignMCP; do
    git -C "$repo" log --oneline --all --grep="$id" 2>/dev/null
  done
done
```

### Pass 2: Comment completion signals
For issues with NO git commit match, check Linear comments for completion signals:
`✅`, `verified`, `complete`, `merged`, `deployed`, `Ned:`, `implemented`, `AGY:`

## Verification before closing

For each candidate:
1. **Has git commit**: verify the commit SHA exists, check what files it touched
2. **Completion comments present**: read the last 3 substantive comments (filter dispatcher routs)
3. **Not a refactoring triage**: skip issues flagged `FLAGGED FOR INTERACTIVE` or `⚠️ Refactoring Triage` — these need human review

## Batch close pattern

Write a Python script to `/tmp/ned_batch_close.py` that:
1. Queries current labels for each issue
2. Swaps `agent:fred` → `agent:done`
3. Moves to Done state (`bbf71b3e-9a05-48ce-9418-df8b9c0b8fec`)
4. Posts a short verification comment citing the commit SHA or completion evidence

Use hardcoded API key: `$LINEAR_API_KEY`

## Refactoring exclusion

**Do NOT close** large refactoring issues (GRO-1062, GRO-1064 type) even if they have
git commits. These are partial executions — the commits are prep work or partial
extraction, and the full conversion needs interactive browser verification.
Leave them as `agent:fred` In Progress for Fred's review.

### Hardcoded Exclusion List (checked BEFORE keyword detection)

Keyword-based refactoring detection can fail when dispatcher-route comments bury triage
signals or when the exact phrasing varies. These issue IDs are **permanently excluded**
from Sweep 6 auto-close — do NOT close them under any circumstances:

```python
HARDCODED_EXCLUSION = {
    'GRO-1062',  # UI extraction — partial, needs browser verification
    'GRO-1064',  # ES module conversion — partial, needs browser verification
}
```

**When adding to this list:** the issue must be a large multi-file refactoring where
autonomous completion is impossible because (a) it modifies rendering code that needs
visual verification, (b) prior sessions only completed prep/infrastructure work, and
(c) the main extraction/conversion still requires an interactive browser session.
Do NOT add one-off fixes or small tasks.

**Detection order:**
1. Check `HARDCODED_EXCLUSION` first — skip immediately if matched, no further checks
2. Then run keyword-based detection against description + substantive comments
3. Only candidates passing both checks are eligible for batch-close

## Cross-Issue Fix Variant (stale codebase state)

Not every "already fixed" case is caught by `git log --grep="ISSUE_ID"`. When the fix
was applied as a side-effect of a DIFFERENT refactoring that predates the issue's
creation, git log returns nothing for the issue ID — the commit was for a different
issue. Detection for this variant:

1. Issue description references filenames/line numbers that don't match the current codebase
2. `git log -- <actual_path>` shows a refactoring commit predating the issue's `createdAt`
3. The current code doesn't contain the described bug pattern
4. `git log --all --grep="ISSUE_ID"` returns empty (confirming no commit was made FOR this issue)

**Example (Jun 12 2026):** GRO-1471 — issue filed against pre-extraction monolith,
but fix already shipped in GRO-1170's modular extraction. Zero GRO-1471 commits
found; all broken paths already absent from current code.

## Non-git deliverables — Hermes profiles, configs, bot scripts

**Hermes profile changes are NOT in any git-tracked repo.** Profiles live under
`~/.hermes/profiles/<name>/` — when a task's deliverables are config files,
SOUL.md, .env, cron scripts, or nudge pollers within Hermes profiles, the git
log cross-reference (Pass 1) will return nothing even if the work is fully complete.

**Detection:** the git log sweep returns empty, but the issue's most recent
substantive comment (from a prior Ned or agent session) describes specific
file paths under `~/.hermes/profiles/` with line counts and structural claims.

**Verification:** stat the claimed files directly — `ls -la`, `wc -l`, `grep`
for key content claims. If the files exist with the claimed line counts and
content, the work IS complete. Close with a verification comment citing the
file paths and sizes.

**Example (Jun 12 2026):**
- GRO-1483 — nudge poller fix in `~/.hermes/profiles/kai/scripts/nudge_poller.py`
- GRO-1482 — Kai sub-agent profiles in `~/.hermes/profiles/kai-{css,content,js}/`

Both had zero git hits across all 5 repos, but file stat confirmed the work.

## Real example (Jun 12 2026)

8 candidates found → 6 closed, 2 excluded:
- **Closed:** GRO-1318 (script refactor), GRO-1317 (decomposer, 2ed51ce), GRO-1316 (lock watcher, 81c52a9), GRO-1315 (pre-push hook, 781bbe9), GRO-1314 (swarm.js), GRO-1305 (audit report)
- **Excluded:** GRO-1062 (UI extraction, partial), GRO-1064 (ES module conversion, partial) — both flagged for interactive

## Pitfall: keyword detection missed canonical refactoring issues (Jun 12 2026)

GRO-1062 and GRO-1064 were batch-closed by Sweep 6 despite being the very examples
cited in this reference. Root cause: `is_refactoring()` scanned description + first
20 comments (ordered by `createdAt`) for keywords like `FLAGGED FOR INTERACTIVE` and
`⚠️ Refactoring Triage`. The triage comments existed but keyword matching didn't
catch them — likely buried under dispatcher-route noise or the exact phrase differed.
**Fix:** hardcoded exclusion list above ensures these can never be auto-closed again.
**Recovery:** both issues were immediately reverted to In Progress + `agent:fred`
with correction comments.
