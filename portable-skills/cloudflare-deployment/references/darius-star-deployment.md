# Darius Star Deployment Architecture

## Project Config (as of June 2026)
- **Cloudflare Pages project:** `darius-star` (id: `6cdb93ce-5fae-4474-b7fd-cb0129c2bddc`)
- **Account:** `196c1798da487413b0281ccc570f05a1`
- **Production URL:** `darius-star.pages.dev`
- **GitHub:** `mbgulden/darius-star` — connected to CF Pages
- **Production branch:** `master` (SET IN DASHBOARD) — FIXED, was `main` but changed
- **Preview branch:** `staging` (auto-deploys to `https://<hash>.darius-star.pages.dev` with alias `staging.darius-star.pages.dev`)
- **Build command:** empty (repo root deployed directly, no build step)
- **Root directory:** empty
- **Destination directory:** `/` (deploys repo root)

## Custom Domains (added Jun 2026, GRO-1025)
- `darius-star.pages.dev` — default Pages domain
- `darius-star.play.whatanadventure.games` — custom subdomain (CNAME → `darius-star.pages.dev`, zone: `whatanadventure.games`)

## Worker Routes (path-based access)
A Worker (`darius-star-router`) on the `whatanadventure.games` zone proxies sub-paths to the Pages project:
- `play.whatanadventure.games/darius-star*` → `darius-star.pages.dev` (production)
- `play.whatanadventure.games/staging/darius-star*` → staging preview

The Worker injects `<base href="/darius-star/">` into proxied HTML so relative game asset URLs resolve correctly. Requests NOT matching `/darius-star*` pass through to the main `whatanadventure-games.pages.dev` Pages project.

Worker script location: `$PRISMATIC_HOME/work/darius-star/worker-darius-star.js`

## Deploy = Repo Root, Not Dist/
CF Pages deploys from the repo root directly (no build step). The `dist/` directory in the repo is NOT what gets deployed — it's gitignored and irrelevant to CF Pages. The source `index.html` at the repo root IS the deployed game.

This means:
- `docs/mission-briefings.json` IS accessible at the deployed site (it's at the repo root)
- Any changes to `index.html` go live on next deploy (after fixing the branch)
- Audio/assets in `assets/` at the repo root are deployed directly

## Build Script
`build.py` at repo root handles:
1. Minifying `index.html` → `dist/index.html` (preserving `<script>` blocks)
2. Copying `docs/mission-briefings.json` → `dist/docs/mission-briefings.json`
3. Copying `assets/` → `dist/assets/`

The dist/ is for reference/testing, not for deployment.

## Auth Status (June 2026)
- `CLOUDFLARE_PAGES_API_TOKEN` (`cfut_...`) — VALID Bearer token. Works for Pages API (`/user/tokens/verify`, `/pages/projects`, `/pages/.../domains`).
- Pages token does NOT work for Zone/DNS/Worker endpoints (returns auth errors). Use Global Key (`cfk_...`) for those.
- `CLOUDFLARE_GROWTHWEB_API_KEY` (`cfk_...`) — Global Key, works for Zones, DNS, Workers. Uses `X-Auth-Email` + `X-Auth-Key` headers.
- Wrangler CLI fails with 9103 for both tokens. Use git push (auto-deploy) or REST API instead.
- **Token prefix:** `cfut_` = Pages-specific Bearer token. `cfk_` = Global Key. `cfat_` (seen in older docs) is a general API Token prefix.

## Sprite Preload: `spritesReady` Set Pattern (CRITICAL)

The game's render loop checks sprites every frame. Using `sprite.complete && sprite.naturalWidth > 0` creates a race condition: images load asynchronously but the renderer checks synchronously. On frame N the image hasn't loaded → fallback shape; frame N+1 it has loaded → sprite appears. This causes flickering and "some work some of the time" behavior.

**The fix (applied Jun 2026):** Track loaded images with a `Set` + `onload`/`onerror` callbacks.

```javascript
const spritesReady = new Set(); // Shared across ALL sprite loaders

function loadPlayerSprites() {
    frames.forEach(key => {
        const img = new Image();
        img.onload = () => spritesReady.add(img);  // Mark as ready
        img.onerror = () => console.warn('Failed to load sprite:', key);
        img.src = `assets/sprites/${key}.png`;
        playerSprites[key] = img;
    });
}

// In render loop — replaces sprite.complete && sprite.naturalWidth > 0:
if (sprite && spritesReady.has(sprite)) {
    ctx.drawImage(sprite, ...);
}
```

This eliminates the race condition: `onload` fires exactly once when the image is fully decoded, and `spritesReady.has()` is O(1). Apply to ALL image loaders (player, enemy, VFX, bosses).

## Ambient Audio Path Fix (Jun 2026)

The `AMBIENT_TRACKS` object referenced 10 nonexistent MP3 files. Actual ambient files are WAVs in `assets/audio/ambient/ambient_b*_atmosphere.wav`. The fix updated the map from descriptive names (`ambient_abyssal_trench.mp3`) to numbered WAV paths (`ambient/ambient_b1_atmosphere.wav`). Also added fade-in (0 → 0.3 over 2s) to avoid jarring transitions on biome switch.

## Player Invulnerability Blink Tuning (Jun 2026)

The invulnerability blink used `Math.floor(this.invulnerable * 15) % 2 === 0` — toggling at ~7.5Hz with 50% duty cycle. Combined with the 6fps sprite frame animation, ships flickered harshly for 3s after spawn. **Fix:** Changed to `Math.floor(this.invulnerable * 8) % 3 === 0` — visible 2 of 3 frames at ~5.3Hz perceived rate. Rule: invuln blink should be fast enough to signal "protected" but slow enough to not look like a rendering bug. ~5Hz with 66% duty cycle is a good default.

## Procedural Background Fallback (Jun 2026 — GRO-1068)

Background strip images (`bg_abyssal_trench_strip.png`, etc.) were missing from the CDN — returning `content-type: text/html` (SPA fallback). Rather than generate 35-42MB PNG files, added `generateBiomeBackground(biomeNum)` that creates procedural canvas backgrounds:

- **10 unique biome palettes** — deep space gradients + nebula blobs + 200-star fields + colored accent stars
- **`ParallaxLayer.draw()`** checks `img.complete && img.naturalWidth > 0` — falls back to procedural canvas
- **`biomeBgCanvases`** cache — each biome's background canvas generated once, reused every frame
- **`ParallaxLayer.update()`** uses `canvas.width` as fallback for offset modulus when image is null
- Initial bg layer keys changed from `'nebula'`/`'city'` to `'bg_1'` so procedural fallback matches immediately

Pattern: when game assets are missing, procedural generation is faster than asset creation — ship the code, generate the assets later.

## Sprite Sheet Slicing at Draw Time (Jun 2026 — GRO-1068)

`explosion_0.png` is a 1024×1024 sprite sheet with 4 frames in a 2×2 grid. The game was drawing the entire sheet as one image — all 4 frames visible at once.

**Fix in `SpriteExplosion.draw()`:**
```javascript
const fw = sprite.naturalWidth / 2;   // 512
const fh = sprite.naturalHeight / 2;  // 512
const col = this.frame % 2;
const row = Math.floor(this.frame / 2);
ctx.drawImage(sprite,
    col * fw, row * fh, fw, fh,     // Source rect (single frame)
    this.x - this.size / 2,         // Dest x
    this.y - this.size / 2,
    this.size, this.size);
```
Uses the 9-arg `drawImage` overload for sub-rectangle extraction. Frame 0 = top-left, frame 1 = top-right, frame 2 = bottom-left, frame 3 = bottom-right.

## Debug Enemy Labels (Jun 2026 — GRO-1068)

Added `window.DEBUG_LABELS` toggle (F3 key) that draws enemy type/class above each mob with dark text outline for readability on any background. Pattern:
```javascript
ctx.strokeStyle = 'rgba(0,0,0,0.8)';  // Dark outline
ctx.lineWidth = 2;
ctx.strokeText(label, x, y);
ctx.fillStyle = 'rgba(255,255,255,0.75)';  // White fill
ctx.fillText(label, x, y);
```
Useful for playtesting: player presses F3, reads enemy name, reports which sprites need fixing.

## Additive Blending for Dark Sprite Backgrounds (Jun 2026 — GRO-1068)

AGY-generated sprites are 1024×1024 PNGs with dark/black backgrounds instead of transparency. When drawn normally, a dark square surrounds every sprite — invisible on the dark game background but covering other sprites and effects.

**Fix: `globalCompositeOperation = 'lighter'`** (additive blending). Dark pixels (near 0) add nothing to the canvas — they become effectively transparent. Bright pixels glow and add to whatever is behind them. Classic technique for space shooters.

Applied to:
- **Enemy sprites** (`Enemy.draw()`): wrap `ctx.drawImage(sprite, 0, 0, size, size)` with lighter/source-over
- **Player laser blasts** (`Bullet.draw()`): same wrap around the laser sprite draw

```javascript
ctx.globalCompositeOperation = 'lighter';
ctx.drawImage(sprite, ...);
ctx.globalCompositeOperation = 'source-over';
```

**Warning:** `lighter` caps at white (255,255,255). Multiple overlapping bright sprites saturate to pure white. For pixel-art sprites with light colors, this washes out detail. Use only when the dark-background problem is the dominant issue.
