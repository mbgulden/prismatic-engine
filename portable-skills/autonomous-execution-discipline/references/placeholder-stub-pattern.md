# Placeholder Stub Pattern for Multi-Agent Pipelines

## When to Use
You discover that a system references modules/files that don't exist on disk, but the agents tasked with building them are still queued/working. Rather than letting the system break with 404s or runtime errors, create minimal working stubs.

## The Pattern

### 1. Audit: What does the system expect?
Grep for all references (script tags, imports, function calls) against the disk to find the gap.

```bash
# Example: find all script tags and check disk
grep -n 'script src=' index.html > /tmp/expected.txt
while read f; do ls "$f" 2>/dev/null || echo "MISSING: $f"; done < /tmp/expected.txt
```

### 2. Extract the interface contract
Read the calling code to determine what functions/properties the system expects from the missing module. Look for patterns like:
- `ModuleName.method()` calls
- `window.ModuleName` checks (inline fallback patterns)
- Property accesses like `ModuleName.count`, `ModuleName.players`

### 3. Build a stub that satisfies the contract
- Expose the expected global(s) via `window.XXX`
- Implement every method the calling code references — even if just as no-ops
- Return sensible defaults (empty arrays, nulls, base values)
- Add a console.log identifying it as a placeholder with a link to the Linear issue

### 4. Log what it connects to
```javascript
console.log('[PLACEHOLDER] module.js loaded — full implementation pending Jules GRO-XXXX');
```

### 5. Place correctly and deploy immediately
Put the stub where the system expects it. Commit and deploy so the system stops breaking NOW — don't wait for the real implementation.

## Real Example: Darius Star (June 2026)

**Problem:** `index.html` referenced 8 .js files via `<script>` tags. Only 3 existed on disk. The game 404'd on 5 modules every page load.

**Gap:** `save_system.js`, `economy.js`, `multiplayer.js`, `ngplus.js`, `leaderboard.js`

**Solution:** Created 5 placeholder stubs in `js/` with:
- Full interface contract from AGY's audit (exact function signatures, globals)
- Working localStorage for save/leaderboard (critical path)
- No-op stubs for non-critical paths (multiplayer joins, paradox rolls)
- Console.log identifiers linking to GRO-1071 through GRO-1075

**Jules sessions** were queued to build real implementations. The placeholders unblocked the game while Jules worked.

## Why This Works
- **Unblocks dependent agents**: Extraction sessions can proceed knowing the module interface exists
- **Prevents regression**: The game actually loads without errors
- **Self-documenting**: Each stub is a spec for the real implementation
- **Graceful degradation**: Non-critical features fail silently rather than crashing

## Pitfalls
- ❌ Don't build the real implementation in the stub — that's the agent's job
- ❌ Don't leave stubs unlabeled — the console.log identifier is critical for tracking
- ❌ Don't deploy complex logic in stubs — they should be throwaway code
- ✅ Do reference the Linear issue number in the stub — creates a traceable link
