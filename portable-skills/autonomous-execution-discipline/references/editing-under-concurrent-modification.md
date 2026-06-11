# Editing Files Under Concurrent Modification

## Problem

When an external process (another agent, cron job, or user) is modifying the same file you're editing, line numbers become stale between reads. A file that was 6091 lines on first read may be 5558, 6468, or 7317 lines moments later.

**Symptoms:**
- `grep -n` returns different line numbers for the same pattern across reads
- `sed -n 'N,Mp'` produces wrong output because the range shifted
- `read_file(offset=N, limit=M)` shows content from a different section
- `wc -l` gives different counts each time you check
- First read shows polish systems exist; second read shows they don't (content changed between reads)

## Root Causes

1. **Multiple agents pushing to the same branch** — Ned and Fred both push to the same repo
2. **Cron jobs editing files** — a scheduled task fires mid-session
3. **Cloudflare Pages rebuilds triggering file changes**
4. **The orchestrator itself** — if this is a shared profile, other sessions are concurrent

## The Solution: Content-Based Editing

Never rely on line numbers. Always use content-based matching:

### Step 1: Find unique context with `grep`

```bash
grep -n 'unique search string' path/to/file
```

Pick a string that appears exactly once in the file. Use enough surrounding context to be unique — a function signature plus its opening line, or a comment plus the next statement.

### Step 2: Verify context with `sed`

```bash
sed -n 'N,N+10p' path/to/file
```

This confirms the surrounding lines haven't changed and you're targeting the right location. If the context doesn't match what you expect, the file has been modified — re-grep.

### Step 3: Apply with `patch` (content-based, fuzzy matching)

The `patch` tool with `mode='replace'` uses 9 fuzzy-matching strategies — it handles minor whitespace/indentation differences that `sed -i` would miss.

```javascript
patch({
  mode: 'replace',
  path: '/path/to/file',
  old_string: 'exact text including surrounding context lines to ensure uniqueness',
  new_string: 'replacement text'
})
```

The `old_string` must be unique in the file. Include enough surrounding context — typically 3-5 lines before and after the change point.

## Anti-Patterns to Avoid

### ❌ Line-number-based editing
```bash
sed -i '3646,3650s/old/new/' file  # LINE NUMBERS ARE STALE
```

### ❌ Reading full files through `execute_code` + `terminal('cat')`
```python
result = terminal("cat /home/ubuntu/work/darius-star/index.html")
# Output truncated at ~50K chars for 300K+ files
```

### ❌ Reading large files through `read_file()` inside `execute_code`
```python
from hermes_tools import read_file
r1 = read_file(path, offset=1, limit=5600)
# Fails with KeyError('content') when the file is too large
```

### ❌ Using `execute_code` for API calls that need env vars
The sandbox doesn't inherit environment variables. Use `terminal()` with `$VAR` interpolation instead.

## When to Use This Pattern

- **Active shared repos** — any repo where multiple agents/sessions push
- **Large files** (>100K chars) that can't be read atomically
- **Files being generated concurrently** — sprite sheets, audio manifests, build artifacts
- **Cron job targets** — files that a scheduled task may touch during your session

## When NOT Needed

- **Files you have exclusive ownership of** — the orchestrator's own config, skill files
- **Small files** that fit in a single `read_file()` call
- **Stable repos** where you're the only committer

## Verification

After editing under concurrent modification, always verify the file is syntactically valid:

```bash
# For HTML
python3 -c "
with open('/path/to/file') as f:
    content = f.read()
# Basic structural check
if content.count('<script>') == content.count('</script>'):
    print('Script tags balanced')
"

# For Python
python3 -c "import ast; ast.parse(open('/path/to/file').read()); print('Syntax OK')"

# For JSON
python3 -c "import json; json.load(open('/path/to/file')); print('Valid JSON')"
```

## Real Example (Darius Star, Jun 2026)

The `darius-star/index.html` file (300K+, 7000+ lines) was being edited by external processes while Ned implemented polish systems. Six content-based patches were applied successfully:

1. Added state variables after `triggerScreenShake` function
2. Added player hit shake in `Player.takeDamage()`
3. Added tier-5 hit flash on boss death
4. Wired `spawnHitFlash` to bullet-enemy and bullet-boss collisions
5. Added overheat tracking, smoke particles, low-health tracking in `update()`
6. Added hit-flash, screen flash, border pulse, overheat glow, and glitch rendering in `draw()`

Zero line numbers were used in any patch. Every `old_string` was verified unique with `grep` before patching.
