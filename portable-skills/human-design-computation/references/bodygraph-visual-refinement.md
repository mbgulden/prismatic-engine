# Bodygraph Visual Refinement Pipeline

## Core Principle: Never Iterate Blind

Editing SVG generation code without rendering to PNG and visually comparing against a reference causes each fix to improve one thing while breaking another. Michael called this out explicitly: **"It just feels like you are guessing now. And you pretty much are… what's a better way to go about this? Every fix gets better in one way and worse in others."**

## The AGY Visual Refinement Workflow

### Step 1: Render current output to PNG
```bash
node render-pro.mjs becca-data.json > test.svg
rsvg-convert -w 820 test.svg -o current.png
```
Requires `librsvg2-bin`: `sudo apt-get install -y librsvg2-bin`

### Step 2: Launch AGY with reference + code + test command
```bash
agy --print "Compare our output at /tmp/current.png with the Neutrino reference at /path/to/ref.jpg.
List every visual difference. Then read ${PRISMATIC_HOME}/work/hd-bodygraph/render-pro.mjs
and fix ALL differences. Test: node render-pro.mjs becca-data.json > test.svg && rsvg-convert -w 820 test.svg -o test.png"
--print-timeout 300s --add-dir ${PRISMATIC_HOME}/work/hd-bodygraph --dangerously-skip-permissions
```

### Step 3: AGY iterates internally
AGY can see images, read code, make edits, render output, and re-compare — all within one session. This avoids the blind-guessing cycle entirely.

### Step 4: Deploy only after visual verification
Only restart the server when the PNG output visually matches the reference.
Server restart: `pkill -f 'python3 server.py'; cd ~/work/hd-platform/reports && python3 server.py &`

## Common AGY Pitfall: Center Colors

AGY tends to make all centers white (`#ffffff`) when told to "match the target." To prevent this, always include explicit center colors in the AGY prompt:

```
CRITICAL: Do NOT remove center activation colors. Becca has 3 defined centers:
- G center: warm yellow/gold fill (#fef3c7)
- Ego/Heart: red-pink fill (#E9A8A5)
- Spleen: red-pink fill (#E9A8A5)
ALL OTHER centers: white/off-white (undefined)
```

## Geometry Lessons (May 30, 2026 session)

### Shape orientations (Neutrino-correct)
| Center | Shape | Notes |
|--------|-------|-------|
| Head | triangle-up | Apex at top |
| Ajna | triangle-down | Apex at bottom — flipped 180° from Head |
| Throat | rect-rounded | Square, significantly rounded corners |
| G | diamond | Rotated square |
| Ego | triangle-down | Apex at bottom, overlaps upper-right of G |
| Sacral | rect-rounded | Largest center, deeply rounded (squircle) |
| SolarPlexus | triangle-up | Below-right of Sacral |
| Spleen | triangle-down | Below-left of G |
| Root | rect-rounded | Bottom, smaller than Sacral |

### Proportions (balanced, not stretched)
```
Head:    64×64 (square-ish)
Ajna:    80×64 (slightly wide)
Throat:  90×80
G:      110×110 (equal diamond)
Ego:     70×60
Sacral: 120×110 (large but balanced)
Spleen:  90×65
SP:     110×90
Root:   110×70
```

### Drawing order
```
1. Background
2. CHANNELS (straight lines, gate-to-gate)
3. CENTERS (fills cover channel mid-sections)
4. GATE TEXT (inactive gates, small gray inside centers)
5. GATE PILLS (activated gates, on top)
6. ACTIVATION COLUMNS + ARROWS + METADATA
```

### Channel striping (parallel offset)
For mixed channels (one Design gate + one Personality gate):
```javascript
// Compute perpendicular unit vector from channel direction
const dx = x2 - x1, dy = y2 - y1;
const len = Math.sqrt(dx*dx + dy*dy);
const px = -dy/len * offset, py = dx/len * offset;
// Red line at +offset, Black line at -offset
```

### Site screenshot capture (for AGY site audits)
```bash
cd ${PRISMATIC_HOME}/work/hd-bodygraph && LD_LIBRARY_PATH=/usr/lib/x86_64-linux-gnu:$LD_LIBRARY_PATH node -e "
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('https://humandesignengine.com/bodygraph', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.screenshot({ path: '/tmp/screenshot.png', fullPage: true });
  await browser.close();
})();
"
```
Note: Playwright needs Chromium installed: `npx playwright install chromium`
And may need `LD_LIBRARY_PATH` for `libasound`.
