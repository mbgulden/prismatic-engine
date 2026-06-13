# Adding a New Screen to Darius Star — Integration Checklist

When adding a new screen (e.g., BRIEFING, LOAD_GAME) to the darius-star game,
there are **6 integration points** across 3 files. Missing any one breaks the screen.

## 6-Point Integration Checklist

### 1. SCREENS enum (`js/ui.js`)
Add the screen constant:
```js
BRIEFING: 'briefing',  // GRO-XXX: description
```

### 2. Transition wiring (`js/ui.js` → `handleMenuConfirm()`)
Wire the transition TO the screen from the screen that leads into it:
```js
transitionToScreen(SCREENS.BRIEFING);
startBriefing(biomeLevel, () => {
    transitionToScreen(SCREENS.PLAYING);
});
```

### 3. Drawing (`js/ui.js` → `drawMenuScreens()`)
Add an `else if` block in the if/else chain. **CRITICAL**: replace the preceding `}` 
with `} else if (...) {`, don't add after it (see `agent-ned` pitfall).
```js
} else if (currentScreen === SCREENS.BRIEFING) {
    drawBriefing();
}
```

### 4. Click handling (`js/game_loop.js` → click event listener)
Add a case in the click handler's if/else chain:
```js
} else if (currentScreen === SCREENS.BRIEFING) {
    handleBriefingClick();
}
```

### 5. Keyboard handling (`js/ui.js` → keydown event listener)
Add cases in ALL relevant key branches:
- **Enter/Space**: advance/confirm
- **Escape**: back/skip
- **ArrowLeft/Right**: navigation (if choices exist)
```js
} else if (currentScreen === SCREENS.BRIEFING) {
    handleBriefingKey(e.key);
}
```

### 6. Update loop (`js/game_loop.js` → `update()`)
Add an `else if` in the non-PLAYING update section (line ~280):
```js
} else if (currentScreen === SCREENS.BRIEFING) {
    updateBriefing(dt);
}
```

### 7. Script tag (`index.html`)
Add the module script tag in the correct load order:
```html
<script src="js/ui/briefing.js"></script>
```

## Module Pattern

Each screen module exports 4 functions consumed by the integration points:
- `start<Screen>(...)` — initialize state and transition in
- `update<Screen>(dt)` — per-frame logic (typewriter, animation)
- `draw<Screen>()` — render to canvas
- `handle<Screen>Click()` / `handle<Screen>Key(key)` — input handling

## Verification
```bash
# Syntax check all modified files
for f in js/ui/briefing.js js/ui.js js/game_loop.js; do
    timeout 5 node --check "$f" && echo "OK: $f" || echo "FAIL: $f"
done
# Verify script tag in index.html
grep 'briefing' index.html
```

## Worked Example: GRO-936 (Jun 2026)
- New file: `js/ui/briefing.js` (236 lines)
- `js/ui.js`: +22/-1 across 5 insertion points
- `js/game_loop.js`: +6 across 2 insertion points
- `index.html`: +1 script tag
- All 4 files passed `node --check` on first verification pass
