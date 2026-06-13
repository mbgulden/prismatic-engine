# Browser Game Init Cascade Failure Pattern

## The Problem

When multiple initialization functions are called sequentially in a single event handler
(e.g., canvas `click`), an **unhandled exception in ANY step silently kills ALL subsequent steps**.
The browser catches the error but the remaining code in that handler never executes.

This creates confusing symptoms: a bug in audio initialization manifests as "sprites not
loading" or "level manager not initialized" — because the audio init throws before those
functions are reached.

## Real Example (GRO-1177 → GRO-1176, Jun 2026)

Canvas click handler in `game_loop.js`:
```javascript
canvas.addEventListener('click', e => {
    setBiomeBackgrounds(biomeLevel);  // Step 1
    initAudio();                       // Step 2 — THROWS here
    loadPlayerSprites();               // Step 3 — NEVER RUNS
    loadPortraitSprites();             // Step 4 — NEVER RUNS
    loadEnemySprites();                // Step 5 — NEVER RUNS
    loadVFXSprites();                  // Step 6 — NEVER RUNS
    if (window.LevelManager && !LevelManager.initialized) {
        LevelManager.init();           // Step 7 — NEVER RUNS
    }
    startMenuMusic();                  // Step 8 — NEVER RUNS
});
```

`initAudio()` called `audioCtx.resume()` without try/catch. In some browsers (audio policy,
context limits), `resume()` throws. The exception propagates up, the handler exits, and
steps 3-8 never execute.

**Result:** Player reports "no audio" AND "sprites not loading" — but the root cause is
only in the audio code.

## Detection

1. Check the call order in initialization handlers
2. Check whether each init function has try/catch around potentially-throwing calls
3. If one symptom (e.g., "no sprites") appears alongside another (e.g., "no audio"),
   suspect cascade failure

## Fix Pattern

Every init function that calls browser APIs (AudioContext, WebGL, localStorage, etc.)
should wrap potentially-throwing calls in try/catch and log warnings:

```javascript
function initAudio() {
    if (!audioCtx) {
        try {
            audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        } catch(e) {
            console.warn('[Game] AudioContext creation failed:', e.message);
            return;  // Graceful exit — don't crash the handler
        }
    }
    if (audioCtx.state === 'suspended') {
        try {
            audioCtx.resume();
        } catch(e) {
            console.warn('[Game] AudioContext resume failed:', e.message);
        }
    }
}
```

The key: `return` on creation failure so the function exits cleanly, and try/catch on
resume so the error is logged but doesn't propagate.

## Prevention Checklist

When writing or reviewing browser game init handlers:
- [ ] Are all init functions wrapped in try/catch for browser API calls?
- [ ] Does each function have a graceful fallback (early return, default value)?
- [ ] Are errors logged to console so they're visible during debugging?
- [ ] Is the init order documented so cascade failures are diagnosable?
