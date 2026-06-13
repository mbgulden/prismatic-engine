# Code-Level QA Without Human Senses

When Ned gets a QA/review task for systems that require human senses (audio, visual, UX),
execute a code-level audit. Separate what code can verify from what needs human perception.

## When to Use This Pattern

- Audio QA tasks (volume mixing, clipping, crossfade quality, loop points)
- Visual QA tasks (animation smoothness, sprite rendering, color balance)
- UX review tasks (responsiveness, layout, interaction feel)
- Any task labeled "Review X" or "QA Y" where X/Y are sensory outputs

## Workflow

### 1. Read All Relevant Source Files
```bash
# Find the files
wc -l js/audio_manager.js js/audio.js
read_file with full output for each
```

### 2. Audit Gain Staging / Math / Logic
- For audio: **trace every signal path** — compute peak gain values for each SFX type (shoot, hit, explosion, powerup, etc.) and each music layer. Sum worst-case simultaneous peaks (e.g., music + 3 explosions + hit + shoot + engine hum). Check whether peaks exceed 1.0 at default volume settings.
- **Check for master compressor/limiter**: `grep -n 'DynamicsCompressor\|createDynamicsCompressor\|compressor\|limiter' js/*.js`. If none found, the output bus has no clipping protection — flag as ⚠️.
- **Check for tab-switching / AudioContext resume**: `grep -rn 'visibilitychange\|pagehide\|document.hidden\|audioCtx.resume\|audioCtx.state' js/`. If no `visibilitychange` handler exists, the AudioContext will stay suspended after tab-switch — flag as ❌.
- For visual: check draw order, z-index, alpha blending, scaling math.
- For UX: check event handlers, debounce timing, responsive breakpoints.

### 3. Verify Integration
- Load order in HTML: `grep -n 'module_name' index.html`
- Tick/update calls: `grep -rn 'Module.tick\|Module.update' js/ index.html`
- Lifecycle hooks: init, stop, pause, resume, resize, **visibility change**
- Guard patterns: `typeof` checks on globals

### 4. Run Lint / Validation Tools
```bash
python3 tasks/lint_audio.py  # or equivalent
find assets/ -name "*.mp3" | wc -l  # count files on disk
```

### 5. Check Defaults Match Specification
```bash
grep -rn 'masterVolume\|sfxVolume\|musicVolume' js/ --include="*.js"
```
Verify default values match what the issue/spec says they should be.

### 6. Post Structured Review

Use this template structure:

```
## ✅ Ned: [Thing] QA Code Review (GRO-XXXX)

### Verdict: Code-level QA PASS/FAIL. Listening/visual-dependent checks require human senses.

### 1. [Category] — ✅/⚠️ Finding
| Metric | Value | Notes |
|--------|-------|-------|
| ... | ... | ... |

### 2. [Another Category] ...

### ⚠️ Cannot Verify (Human Senses Required)
- **Actual audio/visual quality** — code is correct, but real-world differs
- **Loop seamlessness in practice** — code uses native looping, but encoded file matters
- **Atmosphere/fit** — does it match the creative vision?

### 🎯 Recommended Human QA Steps
1. Load and play through — listen/watch for quality
2. Trigger edge cases — stress test
3. Test lifecycle — tab away/back, pause/resume, etc.
```

## Real Example: GRO-870 Audio QA (Jun 12, 2026)

Audited darius-star audio system without hearing it:

- **Gain staging**: Traced every signal path. SFX synth gains span 0.06 (shoot) to 0.20 (explosion crack) × volMultiplier (0.64) = 0.038–0.128. Music effective = masterVolume × musicVolume = 0.48. Engine hum idle = 0.02 × 0.64 = 0.013. Worst-case peak: explosion (0.35 across 3 layers) + music (0.48) = 0.83 at defaults — under 1.0 for typical play but can exceed with 3+ simultaneous explosions.
- **Compressor**: Zero hits for `DynamicsCompressor` — no master bus limiter anywhere. All signals connect directly to `audioCtx.destination`. Clipping risk at edge cases. ⚠️
- **Tab-switching**: Zero hits for `visibilitychange`/`pagehide`/`document.hidden`. `initAudio()` resumes on first interaction only; `AudioManager._wireUserInteraction()` is one-shot. AudioContext stays suspended after tab-switch. ❌
- **Crossfade**: 0.5s default, linearRampToValueAtTime on both fade-out and fade-in. `_changingTrack` guard prevents overlap. Shorter crossfades for one-shots (0.3s), longer for biomes (1.0s). ✅
- **Volume defaults**: masterVolume=0.8, sfxVolume=0.8, musicVolume=0.6 (ui.js:74-76) — exact match for spec. ✅
- **Loop points**: `AudioManager.play()` uses `source.loop = true` — sample-level zero-gap. Chiptune fallback decays each note to 0.001. ✅
- **Integration**: AudioManager.tick() called every frame with typeof guard. Stop on game over. Init+preload on user interaction.

Result: 3/5 code-level PASS. 2 issues found (no compressor, no tab-switch handler). 5 perceptual checks flagged for human ears.

## Reusable Fix Patterns

### DynamicsCompressor on Master Bus
```js
// Add in initAudio() or AudioManager._init(), after audioCtx creation:
const masterCompressor = audioCtx.createDynamicsCompressor();
masterCompressor.threshold.value = -24;  // dB — compress above this
masterCompressor.ratio.value = 12;       // 12:1 ratio
masterCompressor.attack.value = 0.003;   // 3ms attack
masterCompressor.release.value = 0.25;   // 250ms release
masterCompressor.connect(audioCtx.destination);
// Then route ALL audio through masterCompressor instead of audioCtx.destination
```

### Tab-Switching AudioContext Resume
```js
// Add in initAudio():
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && audioCtx && audioCtx.state === 'suspended') {
    audioCtx.resume();
  }
});
```
