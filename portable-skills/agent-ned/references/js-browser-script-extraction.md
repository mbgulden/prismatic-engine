# JavaScript Browser Script Extraction Pattern

When extracting functions from one non-module `<script>` file into another that loads EARLIER in the
load order, JavaScript's late binding makes it safe — but only if you understand the mechanics.

## The Core Insight: Late Binding

In non-module browser scripts (plain `<script>` tags, no `type="module"`), variable lookups inside
function bodies happen at **call time**, not definition time. This means:

```js
// utils.js loads FIRST (module #1)
function createExplosion(x, y, color) {
    vfxExplosions.push(new SpriteExplosion(x, y));  // vfxExplosions doesn't exist yet
}

// game_loop.js loads LAST (module #18)
const vfxExplosions = [];
// ... game runs, createExplosion() is called → vfxExplosions now exists ✅
```

This works because `vfxExplosions` is looked up only when `createExplosion()` is called during
gameplay, at which point game_loop.js has already executed and defined the variable.

## What Makes This Safe

| Factor | Safe? | Why |
|--------|-------|-----|
| Functions referencing `const`/`let` variables from a later script | ✅ | Resolved at call time |
| Functions calling other functions defined in later scripts | ✅ | Also resolved at call time |
| Top-level code (outside functions) referencing later-script variables | ❌ | Executes immediately, variable doesn't exist |
| `const`/`let` at top level of non-module scripts | ✅ accessible | They're in the same realm, just not on `window` |
| `var` at top level | ✅ accessible | Actually becomes `window.variableName` |

## The Pattern (Used for GRO-1168, GRO-1169)

### Step 1: Map Call Sites
```bash
for func in resizeCanvas setNarrativeFlag createExplosion checkCollision resetGame; do
    echo "=== $func ==="
    grep -rn "\b$func\b" js/ --include='*.js' | grep -v "function $func"
done
```

### Step 2: Identify Extraction Ranges
Use `grep -n '^function \|^const \|^class ' <file>` to find all definitions.
Walk the file to find matching closing braces for each function/class.

### Step 3: Check Dependencies
For each function being extracted, identify:
- Variables it references that are defined elsewhere → MUST exist at call time
- Config objects used exclusively by the function → SHOULD be extracted with it
- Functions it calls → MUST exist at call time

### Step 4: Python Extraction Script
```python
# Read source, extract line ranges, build new module, remove from source
extractions = [
    (start_line, end_line, 'description'),
]
# Build new module with header comment
# Remove extracted ranges from source (keep shared state variables)
# Update index.html with new <script> tag
```

### Step 5: Verify
- `node --check` (may core dump for large files — use Function constructor fallback)
- Verify no duplicate functions: `grep -c 'function <name>' old.js new.js`
- Verify shared state variables are still in original file
- Verify index.html script order

## When NOT to Use This Pattern

- **ES modules** (`type="module"`): `const`/`let` are module-scoped, not realm-scoped. Variables from
  one module are NOT visible in another without `export`/`import`.
- **Top-level execution**: Code outside functions in the new module can't reference variables from
  later-loading scripts.
- **IIFE wrappers**: If either the source or target file uses an IIFE, variables are trapped inside.

## Real Example: GRO-1168

Extracted 11 utility functions from game_loop.js (module #18) into utils.js (module #1):

**Moved**: `resizeCanvas`, `setNarrativeFlag`, `getNarrativeFlag`, `determineEnding`,
`createExplosion`, `spawnHitFlash`, `checkCollision`, `triggerScrapNarrativeBeat`,
`startNGPlus`, `resetGame`, `handleDeathOrVictoryRestart`

**Kept in game_loop.js**: Variable declarations (`narrativeFlags`, `vfxExplosions`, `hitFlashes`,
`runScrap`, `scrapNarrativeMilestonesPlayed`, etc.) and event listener wirings.

**Result**: game_loop.js 1963 → 1588 lines. All call sites (enemies.js, player.js, ui.js,
dialogue.js, etc.) resolved correctly because functions are called during gameplay, after
game_loop.js has initialized all state.

## Pitfall: `initializeRendererBuffers()` Dependency (GRO-1169)

When extracting canvas setup to an early-loading module, the `initializeRendererBuffers()` call
had to stay in game_loop.js because it depends on `OffscreenBuffer` from renderer.js (module #12).
The canvas/ctx constants could move early, but the init call couldn't.

**Rule**: If a function call in extracted code depends on a class/function from a module that
loads AFTER the new module's position, defer that call to a later script.
