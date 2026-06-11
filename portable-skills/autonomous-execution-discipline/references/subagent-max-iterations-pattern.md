# Subagent Max Iterations ≠ Failure

**Discovered:** June 9, 2026 — Darius Star session (particle systems + mission briefing)

## The Pattern

When a `delegate_task` subagent returns `status: "failed"` with `exit_reason: "max_iterations"`, the subagent may have produced SUBSTANTIAL working artifacts despite hitting the iteration limit. Checking only the status string leads to re-work and wasted sessions.

## Detection

A max_iterations subagent that actually built something will show:
- `api_calls: 50` (always 50 = the max)
- `tool_trace` with many `patch` or `write_file` entries — not just `read_file` loops
- Working output on disk (check the files!)

A max_iterations subagent that truly failed will show:
- `api_calls: 50` but mostly `read_file` / `search_files` entries in tool_trace
- No `patch` or `write_file` entries
- Zero new lines in target files

## Real Examples (June 9, 2026)

### ✅ Particle System (GRO-989)
- **Status:** failed, max_iterations
- **Actual output:** BiomeParticleSystem class (360 lines), all 10 biome types, wired into game loop in 3 phases, draw hook at line 5569
- **Particle system built:** screen flash (7 lines), hit-flash (25 lines), overheat (6 lines), low-health pulse (2 lines)
- **Verdict:** Delivered 4 working systems despite max_iterations

### ✅ Mission Briefing Display (GRO-1008)
- **Status:** failed, max_iterations
- **Actual output:** Complete briefing overlay (~330 lines), SCREENS.BRIEFING added, loadBriefings(), startBriefing(), typewriter effect, solo/duo/4P variants, drawBriefingOverlay(), integration into update()/draw()/resetGame()
- **index.html grew from 6,572 to 6,986 lines**
- **Verdict:** Fully functional despite max_iterations

## Process After Max Iterations

1. **Read the tool_trace** — look for `patch` and `write_file` entries
2. **Check file sizes** — did index.html grow? Are new files on disk?
3. **Test syntax** — `python3 -c "compile(...)"` for Python, brace-count for JS
4. **Read the changed sections** — verify what was actually built
5. **Complete if needed** — the subagent may have left 1-2 small tasks undone; finish them yourself
6. **Do NOT re-delegate the same goal** — you'll get the same result

## Pitfalls

- ❌ Treating `status: "failed"` as "nothing was done" — the most expensive mistake
- ❌ Re-delegating the same task without checking what was already built
- ❌ Not reading the tool_trace to distinguish real-failure from partial-success
- ✅ Always verify output on disk before concluding work is incomplete
