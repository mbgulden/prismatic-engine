# Dependency Hotspot Analysis for Large Refactorings

When a refactoring issue touches many files with shared global state (e.g., ES module conversion,
script-tag-to-module migration, global-to-import conversion), map the dependency graph BEFORE
deciding whether to execute autonomously. This turns a vague "convert N files" issue into a
concrete heatmap of risk.

## When to Use

- Refactoring touches 10+ files that communicate via global scope
- Load order matters (script tags, IIFE wrappers, or implicit dependency chains)
- The issue asks for a module system conversion (global → import/export)
- You suspect duplicate/dead modules that could confuse the conversion

## Step-by-Step

### 1. Inventory the load order
Extract the current load order from the HTML shell or AGENTS.md module map:
```bash
grep -n '<script' index.html
```

### 2. Scan cross-module references
For every JS file, grep for class/function references that are DEFINED elsewhere:
```bash
for f in js/*.js js/**/*.js; do
  [ -f "$f" ] && echo "--- $f ---" && \
  grep -oP '(?:new |\.)?[A-Z][a-zA-Z]+(?=\()' "$f" 2>/dev/null | sort -u | head -20
done
```
This reveals which modules consume classes from which OTHER modules — the implicit dependency graph.

**Also check for broken `<script>` references**: Every `<script src="...">` tag in `index.html` should resolve to a real file on disk. A missing file produces a silent 404 in the browser — the game may appear to work but have subtle failures. Run:
```bash
while IFS='"' read -r _ src _; do
  [ -n "$src" ] && [ ! -f "$src" ] && echo "BROKEN: $src (file not found)"
done < <(grep '<script src=' index.html)
```
This is especially important after module renames, extractions, or when an agent adds a reference to a file that was never created. Real example: `index.html` referenced `js/audio_chip.js` which didn't exist — silent 404.

### 3. Identify hotspots
Modules that are called by 5+ other modules are HIGH-RISK. They must be converted first and
imported by all dependents. Common hotspots:

| Hotspot type | Example | Why risky |
|---|---|---|
| Utility hub | `game_loop.js` defines `createExplosion`, `checkCollision`, `resetGame` called by 6+ modules | Converting it breaks everything unless import order is perfect |
| Singleton state | `player_state.js` — consumed by enemies, combat, UI, story | Every consumer must import before use |
| Renderer coupling | `renderer.js` ↔ `game_loop.js` circular-ish (renderer needs canvas context from game_loop, game_loop calls renderer to draw) | Circular dependency needs careful restructuring |
| Duplicate modules | `js/systems/combo.js` duplicates `js/combo.js` — NOT loaded by index.html | Don't convert dead code; risks confusion |

### 4. Check for duplicate/dead modules
```bash
# Find files NOT referenced by any script tag
for f in js/*.js js/**/*.js; do
  if ! grep -q "$(basename $f)" index.html 2>/dev/null; then
    echo "DEAD MODULE (not loaded): $f"
  fi
done
```
These should be excluded from conversion — they're dead code that will confuse the effort.

### 5. Determine safe prep work
Even when the full conversion is too risky for autonomous execution, there's often safe prep:
- **Build infrastructure**: package.json, esbuild config, build script — config files only, no runtime impact
- **Documentation updates**: AGENTS.md module map, dependency graph docs
- **Dead code cleanup**: Remove files not loaded by any script tag (safe — they're unreachable)

### 6. Write the section map comment
The Linear comment should include:
- Current load order (script tags)
- Hotspot table with risk levels
- Dead module warnings
- Recommended bottom-up conversion order (leaf modules first)
- The step-by-step interactive approach

## Real Example: GRO-1064 (Darius Star ES Module Conversion)

**Scope**: Convert 17+ JS files from global `<script>` tags to ES modules with `import`/`export`.

**Hotspots found**:
- `game_loop.js` (1752 lines) — 8 functions called by 6+ modules. Highest risk.
- `renderer.js` (1352 lines) — circular-ish with game_loop. Second-highest risk.
- `ui.js` (2336 lines) — consumes from 8+ modules. Largest file.

**Dead modules found**: `js/systems/combo.js`, `js/systems/economy.js`, `js/systems/save_system.js`, `js/systems/upgrade_system.js` — not loaded by index.html.

**Safe prep executed**: package.json + build.js (esbuild config). Config files only, zero runtime impact.

**Conversion order recommended**: Leaf modules first (`banter_db.js`, `scrap_events.js`, `leaderboard.js`, `sprites.js`) → mid-weight (`player_state.js`, `economy.js`, `combo.js`) → heavy modules last (`game_loop.js`, `ui.js`, `renderer.js`). `js/main.js` entry point lands LAST.

**Verdict**: Flagged for interactive — 4/5 triage criteria hit. Browser verification required after each conversion.
