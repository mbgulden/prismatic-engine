# Partially-Implemented Linear Issue Pattern

## Detection

An issue whose title says "Add X" or "Implement Y" but code for X/Y already exists in the repo. The skeleton or partial implementation was done by a prior session without updating Linear state.

**Symptoms:**
- Issue is in Todo/Backlog but `git log` shows no commit referencing its identifier
- Code references exist for the feature (search for keywords from the title)
- `grep -rn '<feature-keyword>' <project>/` returns results
- The issue sits while the code was built ad-hoc

## Investigation Steps

1. **Read the issue description** — extract all requirements as a checklist
2. **Search for existing code** — `search_files(pattern='<keyword>', path='<repo>/')` for every keyword in the title
3. **Read the existing code** — trace the full flow end-to-end
4. **Map requirements to code** — check off each requirement against actual implementation
5. **Identify gaps** — anything not checked off is the real work

## GRO-969 Canonical Example (Darius Star, Jun 2026)

**Issue:** "Continue/Save Flow in Main Menu" — Add CONTINUE option, load game screen with 3 slots, checkpoint resume, delete save.

**Pre-existing code found:**
- `menuOptions` already had `'CONTINUE'` at index 0
- `loadGameScreen()` function existed with save-check logic
- `LOAD_GAME` screen rendered 3 slots with biome/wave/scrap/ship summaries
- `confirmLoadGame()` handled load
- `deleteSaveSlot()` handled deletion
- `save_system.js` had `CampaignSave` with checkpoint support
- `resetGame()` restored from campaign save on launch

**Actual gaps (what was implemented):**
1. CONTINUE always shown even with zero saves → added grayed-out state + indicator text
2. No checkpoint resume on continue → read `lastCheckpoint` directly, rewrite slot (no life penalty)
3. CONTINUE with no saves redirected to new game → now stays on menu with click sound
4. Deleting last save left empty LOAD_GAME screen → auto-bounce back to menu

**What was NOT changed (already working):**
- 3-slot LOAD_GAME screen rendering
- `CampaignSave` API (`loadAll`, `load`, `save`, `delete`, `summarize`)
- `ship_select.html?continue=SLOT` passthrough
- `resetGame()` save restoration
- DEL/ENTER/ESC keyboard handling

## Rule

**Never rebuild what already works.** Read the code, map requirements to implementation, and implement only the gaps. The issue title may say "Add" but the real deliverable is "finish what's already started."

## Pitfall: Over-Implementing

When you find partial code, the temptation is to refactor or "clean up while you're here." Resist. Only fill the requirements gaps. A 5-line targeted patch is better than a 50-line rewrite that risks breaking existing functionality. The existing code may have been battle-tested; your refactor hasn't been.
