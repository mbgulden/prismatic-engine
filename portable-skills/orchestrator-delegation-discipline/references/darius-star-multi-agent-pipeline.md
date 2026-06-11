# Darius Star Multi-Agent Pipeline (Jun 2026)

Proven end-to-end pattern for bootstrapping a project across 4 agent lanes in a single session.

## The Pipeline

```
AGY (audit) → Fred (scaffold) → Ned (build modules) → Jules (tools/docs)
                 ↓
            AGY audit doc becomes source of truth for all downstream agents
```

## Agent Roles

| Agent | Task | Deliverable |
|-------|------|-------------|
| AGY | Read codebase, map dependency graph, spec missing modules | `docs/foundational-structure-audit.md` (260 lines) |
| Fred | Create directories (`js/`, `tools/`, `tests/`), move files, update script tags, create placeholder stubs | Scaffolded repo + 5 placeholder modules |
| Ned | Read AGY audit, build real implementations from specs | `js/save_system.js` (279 lines), `js/player.js` (753 lines), etc. |
| Jules | Build tools (sprite slicer, asset pipeline) and docs (GDD, wave designer, perf audit) | `tools/generate_boss_sprite.py`, `docs/enemy-wave-designer.md`, etc. |

## Key Learnings

### 1. AGY produces specs, Ned implements
AGY's read-only audit produced exact function signatures (`CampaignSave.save(slotIndex, saveObj)`, `Economy.shouldDrop(enemyId)`, etc.). Ned read that audit and built matching implementations without needing to discover the contracts himself. This is faster than Ned reading the 8000-line `index.html` and reverse-engineering the API surface.

### 2. Jules patches go stale when codebase moves
AGY's scaffolding (file moves, script tag updates) shifted line numbers in `index.html`. 9 Jules extraction sessions completed but patches failed to apply. Ned re-extracted 7 modules in 2 cron ticks working directly against current HEAD.

### 3. Placeholder stubs prevent 404s during build
5 missing `.js` files caused the game to 404 on every load. ~50-line placeholder stubs with the correct global namespace (`window.CampaignSave`, `window.Economy`, etc.) let the game load cleanly while real implementations were being built.

### 4. OAuth exhaustion from cron job fallback
Ned's cron job (every 5 min) had no model override, so when DeepSeek was slow it fell through to `openai-codex`, exhausting both OAuth tokens. Fix: pin cron jobs to a specific provider and reset exhausted tokens with `hermes auth reset <provider>`.

### 5. Cron delivery routing matters
Ned's output was cluttering Fred's Telegram chat. Fix: set `deliver: local` and have Ned post results as Linear comments instead.

## Linear Task Structure
- `agent:ned` → Ned picks up (primary executor)
- `agent:fred` → Fred reviews (after Ned swaps label on completion)
- `agent:jules` → Jules builds tools/docs
- `agent:agy` → AGY researches/audits
- Ned's label swap on completion: `agent:ned` → `agent:fred` signals "done, needs review"

## Module Status After Pipeline (3 hours)

| Module | Agent | Lines | Status |
|--------|-------|-------|--------|
| save_system.js | Ned | 279 | ✅ Full impl |
| player.js | Ned | 753 | ✅ Extracted |
| enemies.js | Ned | 650 | ✅ Extracted |
| combat.js | Ned | 160 | ✅ Extracted |
| renderer.js | Ned | 1355 | ✅ Extracted |
| sprites.js | Ned | — | 🔄 Pending |
| audio.js | Ned | — | 🔄 Pending |
| ui.js | Ned | — | 🔄 Pending |
| economy.js | Ned | — | 🔄 Pending |
| multiplayer.js | Ned | — | 🔄 Pending |
| ngplus.js | Ned | — | 🔄 Pending |
| leaderboard.js | Ned | — | 🔄 Pending |
| boss_0.png | Jules→Fred | 4KB | ✅ Recovered |
| generate_boss_sprite.py | Jules→Fred | 130 | ✅ Recovered |
