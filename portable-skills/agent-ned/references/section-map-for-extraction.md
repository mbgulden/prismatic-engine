# Section Map Technique — Prep for Large File Extraction

When a large file (1,000+ lines) needs to be split into multiple modules but the extraction
modifies a tightly interleaved if/else chain (render + input + audio + state all mixed),
create a **section map** as prep work for the interactive session.

## When to Use

- Target file is 1,000+ lines with an if/else or switch chain driving screen/state rendering
- Extraction is flagged for interactive (needs browser verification)
- You want to maximize what the interactive session can accomplish in one pass

## Steps

### 1. Identify the dispatch chain
```bash
# Find the main dispatch function and all screen/state branches
grep -n 'SCREENS\.' path/to/file.js
```

### 2. Build the line-range table
For each branch in the chain, note:
- Start and end line numbers
- Line count
- Planned destination module
- Dependencies (shared state vars it reads/modifies)

### 3. Create skeleton files
Write empty/comment-only skeleton files at the planned destinations. These have ZERO
runtime impact (not loaded in index.html) but serve as extraction targets and document
the plan for the next agent/session.

```javascript
// js/ui/settings.js — Pause menu with volume sliders, difficulty, toggles
// EXTRACTION TARGET (GRO-XXXX): Extract from js/ui.js drawMenuScreens()
//   - SCREENS.SETTINGS: ui.js L739-807
// DO NOT LOAD until extraction is complete and verified interactively.
```

### 4. Post the section map as a Linear comment
Include:
- Table of line ranges → modules
- Which sections stay in the original file (shared state, input dispatch)
- Recommended extraction order (simplest first)
- Why interactive is needed (rendering, input, audio interleaving)

### 5. Identify shared state
Note which variables defined at the top of the file are read/modified by multiple branches.
These must stay in the original file or become shared imports.

## Example Output Format (from GRO-1062, June 2026)

A real section map should include: file structure overview (tree), done/pending table,
dependency analysis, extraction patterns, and verification procedures.

```
# UI Extraction Map — GRO-1062
Source: js/ui.js (1,870 lines post-dialogue-extraction)

## File Structure
js/ui.js (1870 lines)
├── L1-359:     Top-level vars, initAudio, transitionToScreen
├── L360-391:   drawTitleBackground(), drawTitleLogo()
├── L462-591:   Input handling: menu navigation, screen transitions
├── L593-1522:  drawMenuScreens() — MASSIVE if/else chain (930 lines)
│   ├── L596-675:   SCREENS.MENU
│   ├── L676-738:   SCREENS.SHIP_SELECT
│   ├── L739-807:   SCREENS.SETTINGS
│   └── ...
├── L1523-1646: Key handling (playing, pause, gameOver)
└── L1770-1870: Touch controls, DOM handlers

## Done vs Pending
| Step | File | Screen(s) | Lines | Risk |
|------|------|-----------|-------|------|
| ✅ DONE | js/ui/dialogue.js | Dialogue | - | - |
| ⚠️ PEND | js/ui/menus.js | MENU+CREDITS+CINEMATIC | 3 blocks | HIGH |
| ⚠️ PEND | js/ui/ship-select.js | SHIP_SELECT | L676-738 | MED |
| ⚠️ PEND | js/ui/settings.js | SETTINGS | L739-807 | MED |
| ⚠️ PEND | js/ui/hud.js | PLAYING HUD | L1551-1612 | LOW |
| ⚠️ PEND | js/ui/game-over.js | Input only | L1629-1645 | LOW |

## Shared Scope Problem
All screen blocks share ctx, canvas from drawMenuScreens() scope.
Extraction pattern: replace each if-block with a function call passing ctx.

## Recommended Order
1. game-over.js (input dispatching, no if/else chain mods)
2. hud.js (mostly DOM-based)
3. ship-select.js → settings.js → menus.js (inside if/else chain)
```

Always include the full tree, a done-vs-pending table with risk levels, the shared
scope/dependency analysis, recommended extraction order, and verification steps.

## Pitfalls

- **Don't add script tags for skeleton files** — they're empty/comment-only and would
  break the game if loaded. Let the interactive session add tags after extraction.
- **Don't try to extract sections yourself** unless the file is pure code deletion
  (removing a confirmed duplicate). The if/else chain modifications need browser
  verification.
- **Node --check core dumps on skeleton files** are expected — small files with
  comments-only trigger a Node.js parser bug. Ignore these lint failures.
