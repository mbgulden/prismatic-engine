---
name: human-design-computation
description: >
  Compute and render Human Design charts, transits, and astrocartography.
  Covers the OpenHumanDesignMCP server (Swiss Ephemeris backend) and the
  Next Step Telegram bot chart routing. Use this when calculating natal
  charts, rendering bodygraphs, generating cartography maps, or debugging
  chart output (wrong profile, missing channels).
triggers:
  - calculate chart or human design or bodygraph or natal chart
  - cartography or astro map or world lines
  - Jamie chart or Jamie map (Next Step bot)
  - wrong profile or missing channels in chart output
  - Swiss Ephemeris error or missing ephemeris file
  - transit overlay or transit conditioning
always-delegate: false
---

# Human Design Computation

## Architecture

Two repos under ~/work/:

| Repo | Role |
|---|---|
| OpenHumanDesignMCP | Swiss Ephemeris backend: chart calc, transits, cartography, image rendering |
| next-step-bot | Telegram bot (Jamie): AI classification, birth data routing, /chart, /map |

Source path for imports: /home/ubuntu/work/OpenHumanDesignMCP/hd-mcp-server/src/

## SEO Page Generation

For programmatic generation of gate, channel, center, and type reference pages with full schema.org JSON-LD, Open Graph, and Twitter Cards. See `references/seo-page-generation.md` for the template, design system, and generator script pattern. Existing pages at `docs/human-design/{gates,channels,centers,types}/` in the hd-platform repo.

## Critical Pitfalls

### 0. NEVER Default to Noon for Unknown Birth Times

The Design Sun line shifts every few hours via the 88° solar arc. A wrong birth time produces a completely different chart: wrong Profile, flipped defined/undefined centers, missing/new channels, and potentially wrong Type. A report generated with the wrong birth time is FUNDAMENTALLY INACCURATE.

**Protocol:**
1. Always ask for exact birth time (HH:MM, AM/PM).
2. If unknown, ask for time window: morning, afternoon, evening, night.
3. When reference app data exists (Neutrino Design), sweep the time window to find matching Profile + Type, then use the midpoint.
4. If you cannot get a birth time from the user, do NOT generate a report.

**Reproduction case (Ella, Sept 17 2003):** 8:46am → 2/4 MG, 7 defined centers. Noon → 2/5 MG, 5 defined centers. 1pm+ → 3/5. See `references/birth-time-sensitivity.md` in `hd-individual-deep-dive` skill.

### 1. Timezone: calculate_natal_chart expects UTC

cosmic_calculator.calculate_natal_chart(birth_dt=...) expects **UTC** datetime.
Passing local time produces wrong profile, wrong sun line, missing channels.

**Danger zones — code paths that commonly skip conversion:**
- Direct engine calls in scripts and tests
### 1. Timezone: calculate_natal_chart expects UTC — TWO code paths to audit

cosmic_calculator.calculate_natal_chart(birth_dt=...) expects **UTC** datetime.
Passing local time produces wrong profile, wrong sun line, missing channels.

**Verification**: Always test a non-UTC birth (e.g. Michael, Dec 10 1989 17:07 PST) through the path you're using. Expected: 3/5 Projector. Broken: 2/4 Projector. An 8-hour shift = wrong profile.

**Pitfall — `dict.get("lat", 0)` returns None when key exists**: If a dict has `"lat": None`, then `d.get("lat", 0)` returns `None` (not `0`!) because the key EXISTS. This leaks None into `calculate_natal_chart(lat=None)` → `TypeError` in Swiss Ephemeris. Use `d.get("lat") or 0.0` instead. This was the root cause of the synastry 500 error (June 2026). Fix: `hd-platform/shared/mcp_client.py` — all three functions (`compute_natal_chart`, `compute_transits`, `compute_synastry`) had this bug.

**Pitfall — `lat=0.0` sentinel overrides location name**: `resolve_location(location, lat=0.0, lon=0.0)` treats (0,0) as REAL coordinates (Atlantic Ocean) and skips the city name lookup. When you don't have real coordinates, pass `lat=None, lon=None` so the location string is used. Fix in `mcp_client.py` synastry: resolve coordinates from location FIRST, then pass them as separate arguments.

**Pitfall — pytz + timezonefinder are OPTIONAL deps that crash silently**: `_get_pytz()` had no try/except around `import pytz` → `ModuleNotFoundError` at runtime. Unlike `_get_tz_finder()` which handles the missing import gracefully. Fixed June 2026: added try/except matching `_get_tz_finder()` pattern. Install both in any venv that uses the engine: `pip install pytz timezonefinder`.

### 2. Verify against reference chart - no mocks

Reference: Michael Gulden, Dec 10 1989 17:07 PST, Simi Valley CA
Expected output:
- Type: Projector
- Profile: 3/5 (NOT 2/4)
- Authority: Splenic
- Cross: Right Angle Cross of Rulership 4
- Channels: 1-8 (Inspiration) + 44-26 (Surrender)
- Centers: G, Heart/Ego, Spleen, Throat
- Variables: PRR DLR
- Definition: Split

If any field differs, there is a bug. Never ship an unverified chart.

### 3. Motivation Can Differ From Engine Output — Verify With User

The Variable subsystem (Personality Sun Color/Tone → Motivation) is sensitive
to planetary position precision. The engine computes Color/Tone from
longitude → gate position → line → color → tone, and rounding at any step
can shift the result. The Motivation map keys on `(p_sun_color, p_sun_tone)`:

```
Color 1: {1,3,5=Fear, 2,4,6=Hope}
Color 2: {1,3,5=Innocence, 2,4,6=Desire}
Color 3: {1,3,5=Need, 2,4,6=Guilt}
Color 4: {1,3,5=Truth, 2,4,6=Falsehood}
Color 5: {1,3,5=Leader, 2,4,6=Follower}
Color 6: {1,3,5=Individual, 2,4,6=Collective}
```

A 0.02° precision difference in the P-Sun longitude can shift Tone by 1,
flipping the Motivation (e.g., Hope ↔ Fear). The matrix_mapper uses
`WHEEL_ANCHOR = Decimal("302.000")` and `TONE_SIZE = Decimal("0.0260416666666667")`.
A boundary-rounding error between Tropical/Sidereal conversion or between
swisseph precision levels can produce the wrong Motivation.

**Protocol:**
1. When presenting Variable data (Motivation, Cognition, etc.) to the user,
   note that the engine output is computed but may differ from the reference
   app or from the person's lived experience.
2. If the user corrects the Motivation, accept their correction immediately.
   The user knows their own internal experience.
3. Do NOT argue with the user about Variable values. The engine is an
   approximation; the reference app may use a different calculation school.
4. Common disagreements: Motivation (Fear/Hope boundary), Cognition (Tone
   sensitivity), Perspective (Node position precision).

**Reproduction case (Michael Gulden, May 30 2026):** Engine output `Motivation:
Hope` (P-Sun Color 1 Tone 4). User corrected to `Fear` — likely Color 1 with
Tone 3 or 5 (odd Tone on same Color). Difference is one tone level, ~0.026°.

### 4. Variables Arrows Use TONE, Not Color

The Variable string (e.g., `PRR DLR`) is computed from planet **Tone**, not Color:

- Top-Left (Digestion): **Design Sun Tone**
- Bottom-Left (Environment): **Design North Node Tone**
- Top-Right (Motivation): **Personality Sun Tone**
- Bottom-Right (Perspective): **Personality North Node Tone**

Tone 1-3 = "L" (Left), Tone 4-6 = "R" (Right). Using Color produces wrong
Variables even when gate/line/color/tone values match the reference app exactly.
This was verified against Neutrino Design app planet-level exports for all 5
family members.

Also: `collect_gates()` MUST copy `color`, `tone`, and `base` from
`longitude_to_gate_line()` into planet dicts. Without these, `compute_variables()`
sees defaults (color=1, tone=1) for every planet → identical Variables for everyone.

### 4. Closed-loop center definition

Centers are defined ONLY by completed channels, not isolated gate activations.

**CRITICAL — Asc/MC/IC/Dsc gates must NOT participate in channel formation.** The `compute_defined_centers()` function in `cosmic_calculator.py` correctly uses `if g.get("gate") and g.get("circuit", True)` to filter out Ascendant, MC, IC, and Descendant gates from channel/center detection. These positions are still COMPUTED (valid AstroHD data in `astro_hd` dict), but only the 13 planet activations per side (Sun, Earth, Moon, Mercury, Venus, Mars, Jupiter, Saturn, Uranus, Neptune, Pluto, North Node, South Node) form channels.

**Bug history (May 30–31, 2026 — FIXED):** The `circuit` filter was incorrectly removed on May 30, causing Asc/MC gates to participate in channel formation. This was a regression. The filter was restored on May 31. Reproduction case: William Gulden — including his P MC (Gate 6) created false channel 6-59 with planet Gate 59, defining Solar Plexus and flipping Authority from Sacral to Emotional. Fix confirmed against Neutrino Design for all 7 audited people. See `references/asc-mc-channel-formation.md` for full details and reproduction cases.

### 5. Verify birth data first - include reference screenshots

Archive Neutrino Design screenshots per person at `~/work/hd-reports/reference-screenshots/{person}/`. Each person needs 8 standard views: General, Advanced, Activations, Additional, Cycles, Birth Data, AstroHD Design, AstroHD Personality. OCR-extract with tesseract, deduplicate, and name files descriptively (e.g. `{person}_01_general.jpg`). Never git — these are reference data.

Cross-reference every chart against these archived references. See `references/chart-verification-methodology.md`.

`family.json` birth data was provably wrong for 4/5 family members (different
years, months, and locations than the app). Always verify against the reference
app before assuming engine bugs.

For engine validation, see the `hd-engine-validation` skill and `tests/comprehensive_validate.py`.

### 4. Cross-Reference Verification Against External Apps

When a user sends screenshots from a reference HD app (Co-Star, Neutrino Design, etc.):

**Extracting data when vision API is down:**
```bash
sudo apt-get install -y tesseract-ocr
pip install --break-system-packages pytesseract Pillow
python3 -c "
from PIL import Image
import pytesseract
img = Image.open('screenshot.jpg')
print(pytesseract.image_to_string(img))
"
```

**Comparison methodology:**
1. OCR extract all visible fields from each screenshot
2. Build a side-by-side comparison table: Type, Authority, Profile, Cross, Centers, Channels, Definition, ALL Variables (Motivation/Cognition/Environment/Perspective/Trajectory), Design Date
3. Sort discrepancies by severity — Profile/Type errors are BLOCKING; Variable errors may indicate calculation system differences
4. Trace dependencies: Design Date → Design Sun position → Design Profile line → Type classification
5. When Design Date differs by >1 minute: the 88° solar arc binary search + secant refinement is the prime suspect
6. When Profile differs (e.g., 2/4 vs 2/5): the Design Sun line is off
7. When Type differs (Generator vs MG): verify ALL channel→Throat connections from motor centers
8. When Cross names missing (raw gate numbers): incarnation-cross catalog needs populating

**Verification status — May 31, 2026** (see `references/chart-verification-methodology.md`):

| Person | Profile | Type | Authority | Centers | Angles | Status |
|--------|---------|------|-----------|---------|--------|--------|
| Michael | 3/5 ✅ | Proj ✅ | Splenic ✅ | 4 ✅ | 8/8 ✅ | VERIFIED |
| Becca | 6/2 ✅ | Proj ✅ | Splenic ✅ | 3 ✅ | 8/8 ✅ | VERIFIED |
| Ella | 2/4 ✅ | MG ✅ | Emotional ✅ | 5/4 ✅ | 8/8 ✅ | VERIFIED |
| William | 3/6 ✅ | Gen ✅ | Sacral ✅ | 4 ✅ | — | VERIFIED |
| JT | 4/6 ✅ | MG ✅ | Sacral ✅ | — | 8/8 ✅ | VERIFIED |
| Jonathan | 2/5 ✅ | Proj ✅ | Emotional ✅ | — | 8/8 ✅ | VERIFIED |
| Benjamin | 5/1 ✅ | MG ✅ | Emotional ✅ | 7 | 8/8 ✅ | VERIFIED |
| Victoria | 4/1 | Gen | Sacral | — | TBD | PENDING |

**Fixed:** Circuit filter regression (Asc/MC in channels — restored `g.get("circuit", True)`, May 31), timezone-to-UTC conversion, birth-time defaulting.

**Remaining:** Design Date ~2hr early (88° solar arc tolerance), Variables mismatch (calculation school), Cross name catalog incomplete (Benjamin: engine says "RAX of the Unexpected 4", Neutrino says "LAX of Dominion 2" — same gates but Design Date shift flips RAX↔LAX).

**Pitfall — birth time in screenshots:** When a reference app screenshot contains the birth time (e.g., "September 17th, 2003 - 08:46 AM"), OCR-extract and use it. Never default to noon. The D Sun line shifts every few hours → wrong Profile.

**Known discrepancies** (May 2026 audit) — see `references/cross-reference-verification.md`):\n- ✅ JT Belnap (Jun 16 1982, Orem UT): Profile 3/5→4/6, Type Generator→MG — **FIXED May 31, 2026** (circuit filter restored, Design Date still ~20min off)\n- ✅ Jonathan Belnap (Nov 29 2012, Boise ID): Profile 2/4→2/5 — **FIXED May 31, 2026** (circuit filter restored)\n- ✅ William Gulden (Sep 23 2017, Kailua HI): Authority Sacral→Emotional — **FIXED May 31, 2026** (circuit filter restored, see `references/william-gulden-neutrino-reference.md`)\n- ✅ Michael Gulden (Dec 10 1989, Simi Valley CA): Type Projector→MG — **FIXED May 31, 2026** (circuit filter restored)\n- ⚠️ Variables (Motivation/Perspective/Environment/Trajectory) still differ systematically — may indicate different calculation school or missing ephemeris data\n- ⚠️ Design Date universally ~2hr earlier than Neutrino Design — 88° solar arc binary search needs tighter tolerance\n- ⚠️ AstroHD angles for Michael are completely wrong (different gates) — `longitude_to_gate_line()` anchor may be incorrect in `matrix_mapper.py` (current WHEEL_ANCHOR=302°, may need different value)

## Bodygraph Rendering (Neutrino-Style)

**CRITICAL — Never iterate blind on SVG rendering.** You cannot see the output. Every one-attribute fix becomes a trade-off. Render to PDF (preferred, vector) or PNG with `rsvg-convert` and compare against a reference image using Gemini API before shipping.

**Output format: PDF is preferred over PNG.** PDF preserves vector crispness at any zoom. Use `rsvg-convert -f pdf input.svg -o output.pdf`. PDFs are ~44KB. The live API (`/api/public/bodygraph`) defaults to `application/pdf`; add `?format=svg` for raw SVG.

**AGY workflow for bodygraph layout:** See `antigravity-cli-operating-playbook` for full operating details. Quick reference:
- Clean before launch: `pkill -f agy-bin; sleep 2`
- Foreground only (background = SIGKILL): `agy --dangerously-skip-permissions --print '...'`
- Recommended: 500-1,200 chars per call (2-5 centers)
- Prompt limit: ~2,200 chars passes, ~3,000 times out
- AGY cannot compare two images — use Gemini API for reference-vs-output comparison
- Critical: when AGY moves centers, explicitly tell it to move gates too: "If you move a center, move ALL its gates by the same dx,dy offset. Gates must stay inside their centers."

**Primary renderer:** `/home/ubuntu/work/hd-bodygraph/render-pro.mjs` — custom Neutrino-style production SVG renderer (~40KB SVG output).

**Design principles (matching Neutrino Design app, current as of 2026-05-30):**

1. **ALL channel paths are straight lines** — no bezier curves. Each channel is a simple `M x1,y1 L x2,y2` between gate positions.

2. **Drawing order: Channels → Centers → Gate text → Pills.** Channels FIRST so center fills cover mid-sections of lines that cross behind centers.

3. **Undefined centers have `#BEBEBE` borders** (0.8px) and **white fill** (`fill="#ffffff"`). Defined centers get a 1px `#202020` border and colored fill. Background: `#F0F0F0`.

4. **Color constants (exact, from Neutrino reference):**
   - DESIGN (red): `#EB5757`, PERSONALITY (black): `#000000`
   - BG: `#F0F0F0`, INACTIVE/BORDER: `#BEBEBE`
   - G: `#F9D43C`, Ego: `#D03E3E`, Spleen: `#8B5755`
   - Other defined centers: Head `#f9e076`, Ajna `#4fc3f7`, Throat `#a5d6a7`, Sacral `#ef5350`, SolarPlexus `#ce93d8`, Root `#a1887f`

5. **Channels: unified 6px tubes with side-by-side polygon halves.** Each channel half is drawn as polygons covering the channel width. Colors expand to fill width:
   - 0 colors → full-width white polygon
   - 1 color → full-width colored polygon (black or red)
   - 2 colors → TWO side-by-side polygons: left half black (offset -1.5, width 3.5), right half red (offset +1.5, width 3.5). The 0.5px extra width per side creates overlap at centerline — no visible seams.
   - Outline: thin `#ccc` stroke around full channel polygon.
   - **Rejected approaches**: SVG gradients (angle looks wrong on diagonal channels), overlay polygons (red extends beyond channel), separate parallel lines (visible gaps). The side-by-side with overlap is the only approach that works for all channel angles.
   - See `references/channel-unified-gradient-architecture.md` for the evolution of channel rendering approaches.

6. **Active gate markers: filled circles** (r=8px, white number, 1px black stroke). Red fill = Design, black fill = Personality.

7. **Inactive gate numbers: small text** (6.5px, `#333`) inside center shapes. No circle, no outline.

8. **Activation columns: side-colored blocks** — Design=red, Personality=black, both with white text. 13 planets per column.

9. **Variable arrows**: gray body (`#999`) + red tip (`#EB5757`), at x=215 (Design) and x=605 (Personality).

10. **Body silhouette**: `#e0e0e0` at opacity 0.25 on `#F0F0F0` background.

11. **Metadata footer**: Profile in bold red (22px), TYPE · AUTHORITY below (10px), incarnation cross italic, strategy light weight.

12. **Center geometry — standardized (2026-05-30):**
    - All triangles (Head, Ajna, Ego, Spleen, SolarPlexus): **equilateral**, w=100, h=87 (h = w × √3/2 ≈ 86.6, rounded to 87)
    - All rounded rects (Throat, Sacral, Root): **squares**, w=100, h=100
    - G center diamond: **square rotated 45°**, w=110, h=110
    - Same-shape centers MUST be identical in size
    - Triangle paths use clean geometric shapes with 4px corner rounding — no curved bottoms, no asymmetric shapes
    - Full SVG path templates in `references/center-shape-geometry.md`

13. **Gates go INSIDE centers, ~8px from edges** (user-confirmed 2026-05-30). Compute inset positions: top gates at `cy - h/2 + 8`, bottom at `cy + h/2 - 8`, sides at `cx ± w/2 ∓ 8`. The reference Neutrino Design app places gates inside center borders, not flush against them.

## Visual Verification Workflow (CRITICAL — DO NOT ITERATE BLIND)

**PITFALL — Blind SVG editing destroys progress**: Editing SVG generation code without rendering and visually comparing the output causes each fix to improve one thing while breaking another. The renderer is visual — you MUST render to PNG and compare against a reference before shipping changes.

**Correct workflow:**

1. **Clean AGY state (before any AGY launch):**
   ```bash
   pkill -f agy-bin; sleep 2
   ```

2. **Render SVG to PNG:**
   ```bash
   rsvg-convert -w 820 /tmp/chart.svg -o /tmp/chart.png
   ```

3. **Compare against reference using Gemini API** (AGY CANNOT compare two images — it hangs):
   ```python
   # Use gemini-2.5-flash via generativelanguage.googleapis.com
   # Send both images + "List every visual difference" prompt
   ```

4. **Apply fixes via AGY** (AGY works for single-image + file edit with `--dangerously-skip-permissions`):
   ```bash
   pkill -f agy-bin; sleep 2
   agy --dangerously-skip-permissions --print 'Look at <reference>. Edit <file>: <specific changes>'
   ```
   Recommended: 500-1,200 chars per call (2-5 centers). See `antigravity-cli-operating-playbook` for verified limits.

5. **Re-render and re-compare** until all differences are resolved.

6. **Only then deploy to the live server.**

This replaces the old pattern of guessing at SVG attributes blind.

**PITFALLS (from May 30, 2026 bodygraph refinement session):**

1. **Never edit SVG code without rendering to PNG and comparing against a reference.** Use `rsvg-convert -w 820 in.svg -o out.png` then Gemini API (model `gemini-2.5-flash`) for comparison. Blind editing produces trade-offs, not progress.

2. **Parallel channel lines, NOT dash overlay.** For mixed channels: compute perpendicular offset from direction vector, draw red line +1.0px and black line -1.0px.

3. **Three data-mapping bugs between engine and renderer** (all produce silent failures — empty activations, all-gray channels, wrong center names):
   - Engine key is `design_planets` / `personality_planets` — NOT `design_activations` (that key doesn't exist, produces `None`)
   - Engine returns `Heart/Ego` as center name → renderer expects `Ego` (map with `.replace('Heart/Ego', 'Ego')`)
   - Engine returns channels as `[{'gates': (25,51), 'name':'...'}, ...]` → renderer expects `[[25,51], ...]` (map with `[list(ch['gates']) for ch in channels]`)

4. **Center colors must be VISIBLE.** Extract exact hex values from the Neutrino reference using Gemini — don't guess. Reference colors (2026-05-30): DESIGN `#EB5757`, PERSONALITY `#000000`, BG `#F0F0F0`, BORDER `#BEBEBE`, G `#F9D43C`, Ego `#D03E3E`, Spleen `#8B5755`, defined border `#202020`.

5. **AGY tends to make all centers white.** When using AGY for bodygraph rendering, specify exact hex colors AND which centers to color. "Match the target" without explicit center list → AGY makes everything `#ffffff`.

6. **Channels use side-by-side polygon halves, NOT gradients or separate lines.** Every channel half is one polygon covering the full width (0 or 1 color active) or two overlapping polygons (2 colors active: left=black at offset -1.5 width 3.5, right=red at offset +1.5 width 3.5 — the 0.5px extra width creates overlap at centerline). Gradient approaches were rejected because the split line appears at a weird angle on diagonal channels. Overlay approaches were rejected because red extends beyond the channel outline. Separate parallel lines were rejected because of visible gaps.

7. **Ego faces LEFT, Spleen faces RIGHT, Solar Plexus faces LEFT, Head points UP, Ajna points DOWN.**

8. **Center geometry is standardized:** All triangles (Head, Ajna, Ego, Spleen, SolarPlexus) are equilateral with w=100, h=87 (h = w × √3/2). All rounded rects (Throat, Sacral, Root) are squares with w=100, h=100. The G center diamond is w=110, h=110 (a rotated square). Same-shape centers MUST be the same size. Triangle paths use clean geometric shapes with 4px corner rounding — no curved bottoms, no asymmetric shapes.

9. **Gates go INSIDE centers, ~8px from edges** (user-confirmed 2026-05-30). The reference app places gate markers inside center borders, not flush against them. For each center, compute inset positions: top at `cy - h/2 + 8`, bottom at `cy + h/2 - 8`, sides at `cx ± w/2 ∓ 8`. Telling AGY "gates on edges" will produce wrong results.

10. **Reference image is cached — don't ask user to re-send.** Once the user sends a reference screenshot, use the cached copy at `~/.hermes/profiles/orchestrator/image_cache/img_<hash>.jpg`. The latest reference is authoritative. The user should not need to keep posting the same reference image.

11. **React-pdf HD reports CANNOT be used for coordinate extraction.** react-pdf renders all text as glyph paths with no `<text>` elements. `pdftocairo` can't distinguish bodygraph paths from font glyphs. Use JPEG screenshots for pixel extraction or AGY for visual comparison.

12. **PDF is the preferred output format.** Always render bodygraph charts as PDF (`rsvg-convert -f pdf in.svg -o out.pdf`). PDFs are ~44KB and vector-crisp. The live API defaults to `application/pdf`; use `?format=svg` for raw SVG.

10. **Drawing order is critical for clean channel appearance.** Channels → Centers → Gate text → Pills. Drawing centers AFTER channels means center fills (colored for defined, transparent for undefined) cover the mid-sections of straight lines that would otherwise visibly intersect through centers.

11. **Undefined centers: white fill (`fill="#ffffff"`), stroke="#BEBEBE" (0.8px).** They appear as white-filled shapes with gray borders on the `#F0F0F0` chart background. All centers get white fill — defined centers overlay their colored fill on top. The old approach of `fill="none"` was wrong — it created transparent windows to the background. The old approach of `fill="#ffffff"` was also wrong for a gray background — it created white blocks on gray. With the current `#F0F0F0` background, white fill looks correct.

## Bodygraph Rendering (render-pro.mjs) — Architecture Details

Full architecture and geometry constants in `references/neutrino-bodygraph-renderer.md`. Channel-specific rendering architecture (4-quadrant unified tube system, color expansion rules) is in `references/channel-rendering-architecture.md`.

**Bridge data (server.py → render-pro.mjs):**

```json
{
  "definedCenters": ["G", "Ego", "Spleen"],
  "personalityGates": [1, 8, 13, ...],
  "designGates": [10, 44, 58, ...],
  "bothGates": [26],
  "channels": [[1, 8], [26, 44]],
  "type": "Projector", "profile": "3/5",
  "authority": "Splenic", "strategy": "To Wait for Invitation",
  "incarnationCross": "Right Angle Cross of Rulership 4",
  "variables": "PRR DLR",
  "variablesAdvanced": {"design_color": 2, "design_tone": 3, "personality_color": 1, "personality_tone": 4},
  "activations": {
    "design": { "sun": {"gate":47,"line":5,"color":2,"tone":3,"base":2}, ... },
    "personality": { "sun": {"gate":26,"line":3,"color":1,"tone":4,"base":3}, ... }
  }
}
```

**Planet key mapping** (MCP engine → renderer):
Engine uses `chart['design_planets']` and `chart['personality_planets']` (NOT `design_activations` — that key does not exist). Planet key mapping: `Sun`→`sun`, `Earth`→`earth`, `True Node`→`northnode`, `South Node`→`southnode`, `Moon`→`moon`, `Mercury`→`mercury`, `Venus`→`venus`, `Mars`→`mars`, `Jupiter`→`jupiter`, `Saturn`→`saturn`, `Uranus`→`uranus`, `Neptune`→`neptune`, `Pluto`→`pluto`. Ascendant/MC are in the planet dict but excluded from activation cards.

**Center name mapping:** Engine returns `Heart/Ego` → renderer expects `Ego`. Map with `c.replace('Heart/Ego', 'Ego')`.

**Channel format:** Engine returns `[{'gates': (25, 51), 'name': 'Initiation'}, ...]` → renderer expects `[[25, 51], ...]`. Map with `[list(ch['gates']) for ch in channels if 'gates' in ch]`.

Bridge in `server.py` (port 8081):
1. `calculate_natal_chart()` → extract gates, channels, centers, activations (with color/tone/base)
2. Build render data dict → `subprocess.run(["node", "render-pro.mjs", tmp_json])`
3. Return SVG with CORS `*`, 1hr cache

**Pitfalls:**
- Never use curves for channel paths — Neutrino Design app uses ONLY straight lines. Bezier curves (`C` commands) are a dead giveaway of the old Gonzih renderer.
- Channels MUST be drawn before centers. Drawing centers first hides the straight lines behind them.
- Gate text must be INSIDE center shapes (small gray text near edges). Old renderer placed all gates as perimeter pills.
- Do NOT use `<circle>` for activated gates — use `<rect rx="5.5">` (pill capsules).
- The Gonzih `dist/index.mjs` does NOT export geometry constants — they must be inlined in the renderer.
- Always pass `variablesAdvanced` (with design_color, design_tone, personality_color, personality_tone) for hexagonal arrow numbers. The variables string alone only gives arrow directions.

**Legacy renderer** (`render-hde.mjs`): Gonzih wrapper — uses curves, perimeter pills, no variable arrows, no activation card triangles. Do not use for production charts.

## Production Report Pipeline (Primary Path)

**For any production pipeline — REST APIs, PDF reports, payment flows — use the REST bridge pattern, NOT MCP transport.** Import the engine modules directly for speed, reliability, and simplicity.

Support file: `references/rest-bridge-local-engine.md` — covers direct engine import, endpoint design (public vs auth), HTML/CSS → wkhtmltopdf pipeline, payment webhook integration, and embeddable widget pattern. Reference implementation at `/home/ubuntu/work/hd-platform/reports/server.py`.

Quick start for any new chart-serving endpoint:
```python
import sys; sys.path.insert(0, "/home/ubuntu/work/OpenHumanDesignMCP/hd-mcp-server/src")
from cosmic_calculator import calculate_natal_chart
from ephemeris_engine import init_ephemeris
init_ephemeris()
chart = calculate_natal_chart(name="...", birth_dt=datetime(...), lat=..., lon=..., timezone="...")
```

### HD Platform API Server Dependencies

The FastAPI server in `hd-platform/api/` requires these additional packages beyond the MCP engine:
```bash
/home/ubuntu/.local/share/pipx/venvs/hermes-agent/bin/python -m pip install sqlalchemy redis asyncpg psycopg2-binary
```

**Symptom if missing**: `ModuleNotFoundError` for `sqlalchemy`, `redis`, or `asyncpg`. The API starts and immediately exits with exit code 1.

**Start command**:
```bash
cd /home/ubuntu/work/hd-platform
/home/ubuntu/.local/share/pipx/venvs/hermes-agent/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

### Timezone Pitfall in API Wrappers

`shared/mcp_client.py` and `reports/server.py` both constructed `datetime(year, month, day, hour, minute)` from local birth time and passed it directly to `calculate_natal_chart()` which expects UTC. The engine does NOT auto-convert. Always use `from geo_resolver import local_to_utc` before constructing the datetime. For Michael (Dec 10 1989 17:07 PST), failing to convert produced 2/4 Projector instead of 3/5.

## LLM-Powered Relationship Deep Dives

Support files: `references/relationship-report-prompts.md` (Michael's full prompt series for generating comprehensive, jargon-free HD reports for individuals and pairs).

### Computing Relationship Composites

Two approaches:

**A. Via family.json profiles (mcp_server wrapper):**
```python
from mcp_server import get_relationship_composite
result = get_relationship_composite("michael", "becca")
```

Requires profiles in family.json. Returns full composite matrix.

**B. Direct engine call (for ad-hoc people not in family.json):**
```python
from mcp_server import calculate_chart_with_transits
from synastry_engine import calculate_composite
import re

# Get both charts
jt = calculate_chart_with_transits(name="JT", year=1982, month=6, day=16,
    hour=18.0, lat=40.2969, lon=-111.6946, location="UTC-6")
jon = calculate_chart_with_transits(name="Jonathan", year=2012, month=11,
    day=29, hour=1.0, lat=43.6150, lon=-116.2023, location="UTC-7")

jt_chart = jt["natal"]
jon_chart = jon["natal"]

# Run composite
composite = calculate_composite(
    set(jt_chart["all_active_gates"]),
    set(jon_chart["all_active_gates"]),
    "JT", "Jonathan"
)
```

### Channel String Parsing

`calculate_chart_with_transits()` returns channels as strings like `"10-34 (Exploration (Individual))"`. Parse with:
```python
def parse_chan(s):
    m = re.match(r'(\d+)-(\d+)\s*\((.+?)(?:\s*\(.+\))?\)', s)
    if m:
        return {"gates": [int(m.group(1)), int(m.group(2))], "name": m.group(3).strip()}
    return {"gates": [], "name": s}
```

### Composite Analysis Outputs

- **shared_gates**: Gates active in BOTH charts
- **unique_a / unique_b**: Gates only in one chart
- **electromagnetic_channels**: One gate from each person → intense attraction/friction
- **dominance_channels**: One person defines the full channel, other doesn't
- **compromise_channels**: Both have one gate but neither defines the channel
- **companion_channels**: Both define the same full channel
- **combined_defined_centers**: Union of both sets

### Report Generation

Michael has a comprehensive prompt series for generating HD reports. See `references/relationship-report-prompts.md` for the full templates. The series produces:

- **Part 1**: Individual deep-dive (strengths, weaknesses, inner/outer self, situational examples)
- **Part 2A**: Foundational relationship dynamics summary with 3 real-world scenarios
- **Part 2B**: Person A's perspective — user manual for Person B with conversational scripts
- **Part 2C**: Person B's perspective — user manual for Person A with conversational scripts
- **Part 2D**: Deep synthesis, anomalies, collaboration framework, 12-month transit weather

Key requirements from the prompt series:
- **ZERO** esoteric HD jargon (no "Sacral," "Not-Self," "Lines," "Authority" without plain-English translation)
- Visceral, opinionated real-world examples
- Exact conversational scripts for both people
- Grounded in actual life dynamics, not horoscope theory

### Report Delivery

Support files: `references/relationship-report-prompts.md` (Michael's full prompt series + client-facing adaptation notes), `references/stripe-payment-pipeline.md` (end-to-end payment + PDF + email flow), `references/cloudflare-pages-deployment.md` (static site deployment on Cloudflare Pages).

**Output formats:**
1. **Markdown** → `~/work/next-step-bot/reports/<name>-report.md` — delivered via `MEDIA:` path in Telegram
2. **PDF** → `pandoc input.md -o output.pdf --pdf-engine=wkhtmltopdf --metadata title="Title"` (requires `sudo apt-get install -y pandoc wkhtmltopdf`)
3. **Google Docs** — requires write-scope OAuth (separate from read-only GDrive MCP; see `next-step-bot` skill for auth flow)

**Client-facing adaptation**: When the recipient has zero HD familiarity, apply the adaptations in `references/relationship-report-prompts.md` (no-belief-required intro, drop anchor table, isolate one core tension, type-matched closing question, keep under 6 pages).

**Telegram formatting**: Never use pipe tables or horizontal rules — Telegram has no table syntax. Use bullet lists, bold labels, and emoji spacing. Write reports in Telegram-safe format before delivery.

## Transit Computation

Module: `transit_engine.py` in MCP src. Uses Swiss Ephemeris for current planet positions.

### Computing transit gate activations

```python
from transit_engine import calculate_transit_positions
from ephemeris_engine import julday, init_ephemeris
from datetime import datetime

init_ephemeris()

# Get JD for target date
dt = datetime(2026, 5, 30, 12, 0)
jd = julday(dt.year, dt.month, dt.day, dt.hour + dt.minute/60.0)

transits = calculate_transit_positions(target_jd=jd)
# Returns dict: planet_name → {gate, line, color, tone, base, gate_name, longitude, sign, degree, ...}

transit_gates = {data['gate'] for planet, data in transits.items() if isinstance(data, dict) and 'gate' in data}
```

### Channel activation detection

```python
for g1, g2 in natal_channels:
    g1_in = g1 in transit_gates
    g2_in = g2 in transit_gates
    if g1_in and g2_in:
        print(f"{g1}-{g2}: FULLY LIT — both gates activated by transit")
    elif g1_in:
        print(f"{g1}-{g2}: transit activates gate {g1}")
    elif g2_in:
        print(f"{g1}-{g2}: transit activates gate {g2}")
```

### 12-month forecast pattern

Sample 7 snapshots at [0, 30, 60, 90, 180, 270, 365] days from now. For each snapshot:
- Compute transit gates
- Intersect with natal gates → `hits`
- Check which natal channels get fully or partially activated
- Identify Phantom Costume gates lit by transit (angles on undefined centers)

**PITFALLS:**
- Import from `transit_engine`, NOT `cosmic_calculator` — `calculate_transits` doesn't exist in cosmic_calculator
- Import `julday` from `ephemeris_engine`, NOT `astropy` — astropy isn't installed
- `calculate_transit_positions()` takes `target_jd` as a float, not a datetime

## Cartography Geometry

Two line types, fundamentally different:

### MC/IC - Vertical Meridians
find_mc_ic_line(jd, planet_lon, angle) returns [[lon, -90], [lon, 90]]
Searches terrestrial longitudes at lat=0 for planet_lon matching MC cusp.
Output: vertical LineString pole-to-pole.

### ASC/DSC - Sweeping Sinusoidal Curves
trace_asc_dsc_curve(jd, planet_lon, angle) returns [lon, lat] points
Scans longitude 0-360 step 1 deg, latitude -70 to +70 step 0.5 deg.
Finds where planet_lon matches ASC/DSC cusp. Threshold 0.5 deg.
Output: 100-150 point LineString forming a smooth sinusoidal curve.

### Map Rendering
render_cartography_map(lines_data, output_path, title) uses matplotlib+cartopy.
Planet colors: Sun #FFD700, Moon #C0C0C0, Mars #FF0000, Pluto #8B0000.
Line styles: MC solid 2pt, IC dotted 1.2pt, ASC solid 1.5pt, DSC dashed 1.2pt.
Border labels at map edges: "Sun MC", "Pluto ASC", etc.

## Location Scoring & World Scan\n\nModule: location_scorer.py in MCP src.\n\n### score_location(jd, lat, lon) -> dict\nScores a geographic point across 5 life areas (career, love, family,\ncreativity, general) using Gaussian falloff of planetary conjunctions\nto the 4 angle cusps (ASC, DSC, MC, IC). Returns raw + normalized (0-10)\nscores, per-planet breakdown.\n\nPlanet-to-category weights matrix:\n- Sun: career=10, general=10 | Moon: family=10 | Venus: love=10\n- Jupiter: career=9, love=8, family=9 | Saturn: love=-3, family=-2\n- Mars: career=8 | Uranus: creativity=10, family=-5\n- Full matrix in the module docstring.\n\nAngle weights: MC=1.5 (career), ASC=1.3 (self), DSC=1.2 (love), IC=1.0 (family)\n\nGaussian falloff: weight = exp(-dist²/(2*σ²)), σ=2°\n\n### scan_world(jd, step=5.0) -> list[dict]\nGrid-sweeps globe in lat/lon steps, scores each, returns top N sorted\nby any category. Progress logging every 200 points.\n\n### top_locations(jd, category, step, top_n, exclude_antarctica=True)\nConvenience wrapper: filters Antarctica (lat < -55), returns ranked results.\n\n### family_composite(people, step) -> list[dict]\nEach person dict: {name, year, month, day, hour, lat, lon, timezone}\nComputes JD for each, scan_world for each, averages normalized scores\nacross all members. Returns top composite locations.\n\n### find_nearby_lines(lines_data, lon, lat, radius_km)\nFilters GeoJSON cartography lines to haversine-distance proximity.\n\n## Zoomed Cartography\n\nrender_cartography_zoom(lines_data, output_path, center_lon, center_lat,\n                        radius_deg=20, title=\"\") -> str\nLike render_cartography_map but uses cartopy.set_extent() for regional views.\nAt 150 DPI with rivers, lakes, states. Red star marker at center point.\n\n## Telegram Bot Routing

Jamie classifies messages via DeepSeek API and routes chart queries.
Birth data detection via _regex_extract_birth(text) handles:
- 12/10/1989 @17:07 Simi Valley, CA
- March 5 1992, 2:30 PM in Austin TX
- 1999-06-15 08:45 London UK
Falls back to DeepSeek AI extraction for ambiguous input.

When classified as "birth_query": extract, local_to_utc, calculate_natal_chart,
render_bodygraph, send_photo to Telegram.

## Swiss Ephemeris

Init: init_ephemeris() looks for files in
~/work/OpenHumanDesignMCP/hd-mcp-server/ephemeris/

Chiron (AST_OFFSET + 2060) requires se02060s.se1 which may be missing.
Wrap get_planet_position() in try/except and skip gracefully.

## Computation Engine Internals

### Implementation Architecture

```
geo_resolver.py     → lat/lon/timezone + local→UTC conversion
ephemeris_engine.py → thread-safe pyswisseph wrapper + get_houses() for Ascendant/MC
matrix_mapper.py    → longitude→gate/line mapping with 302.00° anchor
cosmic_calculator.py → full chart calculation with closed-loop logic
synastry_engine.py  → composite + Penta (12 correct Penta gates)
schemas.py          → Pydantic v2 data contracts
mcp_server.py       → FastMCP gateway with batch CSV
```

### The 26-Position Mandate

Standard Human Design requires exactly 26 activation positions:
- **13 planetary bodies for Personality (birth)**: Sun, Earth, North/True Node, South Node, Moon, Mercury, Venus, Mars, Jupiter, Saturn, Uranus, Neptune, Pluto
- **13 planetary bodies for Design (88° solar arc retrograde)**: same bodies computed at the historical Design timestamp

NEVER compute fewer than 26 positions. Missing Design planets (Moon through Pluto) is a common cause of missing gates.

### Engine Verification & Debugging

**Core principle: "Don't trust, verify."** Never assume the engine is correct without comparing against verified app output. Every fix must be followed by full batch re-validation.

#### Common Failure Modes

| Symptom | Likely Cause |
|---|---|
| All 5 family members have identical Variables | `collect_gates` dropping color/tone — all defaulting to 1 |
| Variable strings slightly off (1-2 positions) | Using Color instead of Tone for arrows |
| Extra center/channel in a chart | Gate from Ascendant/MC not filtered (check `circuit: False`) |
| All charts fail type/profile | Wrong birth data (check family.json against app export) |
| Wrong Profile (e.g., 3/5→4/6) | Timezone/DST conversion error — check `local_to_utc()` |
| Generator instead of MG | Missing indirect motor→Throat detection via BFS |
| Wrong Definition (Triple Split→Single) | Definition counted by center count, not BFS groups |
| Cross shows raw gate string | Missing entry in RAX_MAP |
| Variables all wrong | Swapped Personality/Design planet→arrow assignments |
| Variables uniformity across charts | Upstream data loss — planet dicts missing `color`/`tone`/`base` |

#### Validation Workflow

1. **Cross-check input data first**: Extract birth date, time, and location from app screenshots/PDF and reconcile against input sources
2. **Correct birth data first** — `family.json` may be wrong
3. **Extract app planet-level data** — PDF exports include Gate/Line/Color/Tone/Base per planet
4. **Compare engine vs app** — run `calculate_natal_chart()` with verified birth data in UTC
5. Compute all 26 positions first — verify gate/line for each
6. Check centers are defined ONLY by complete channels
7. Verify Type → Authority → Profile → Cross in that dependency order
8. Run comprehensive validation after every change — checks 11 fields per person

#### Test Scripts

| Script | Purpose |
|---|---|
| `tests/batch_validate.py` | Quick batch: Type, Profile, Authority, Centers, Channels — **gitignored** |
| `tests/comprehensive_validate.py` | Full 11-field validation against app ground truth — **gitignored** |
| `tests/test_engine.py` | 28 pytest tests verifying wheel anchor, channel detection, chart structure, geo resolver, synastry — uses synthetic data only |

**CRITICAL — PII in source files**: Real birth data (names, dates, times, locations) must NEVER appear in source code, `__main__` blocks, test files, or docstrings. Real verification data goes in gitignored files only (`family.json`, `tests/local/`).

#### Known Bug Fixes (May 2026 Session)

**Fix 1 — MG Detection**: Bug: `if has_sacral and has_solar_plexus: return "MG"`. Fix: MG = Sacral defined AND any motor connected to Throat via defined channel circuit. Added `_motor_connected_to_throat()` with BFS graph traversal for indirect connections. Applied to both `cosmic_calculator.py` and `matrix_mapper.py`.

**Fix 2 — Timezone/DST**: Bug: Unknown locations defaulted to UTC+0, corrupting entire charts. Fix: Added Mountain timezone locations with DST auto-detection.

**Fix 3 — Definition Counting**: Bug: Used center COUNT instead of connected components. Fix: Added `_compute_definition_groups()` using BFS on channel adjacency graph.

**Fix 4 — Cross Name Map**: Bug: RAX_MAP had only 5 entries. Fix: Expanded to 84 entries.

**Fix 5 — Variables Arrow Mapping**: Bug: Arrow-to-planet assignments swapped. Fix: Corrected mapping to Design Sun→Digestion, Design Node→Environment, Personality Sun→Motivation, Personality Node→Perspective.

**Fix 6 — Variables Uniformity**: Bug: All charts returned identical Variables. Root cause: `collect_gates` dropping `color`, `tone`, `base` fields. Fix: Added all three to all 5 gate dict-building sites.

**Fix 7 — Arrow Direction Uses Tone**: Bug: `compute_variables` used Color for arrow direction, but app uses **Tone** (Tone 1-3=L, 4-6=R). Fix: Changed `arrow_from_color()` → `arrow_from_tone()`. All 5 family members now produce correct Variable strings.

**Design Date Precision**: Use 60-iteration binary search + secant refinement to nanodegree precision (tolerance < 0.0000001°). A loose tolerance introduces 2.4-hour timestamp errors that can shift slow planets across gate boundaries.

#### Variables: Label Maps Are App-Specific

The text labels for Digestion, Environment, Motivation, and Perspective are (Color, Tone) → name lookups that vary between apps. There is no single correct naming. What IS standardized and verifiable: Gate, Line, Color, Tone, Base values; Variable string (PRR DLR etc.); Centers, Channels, Type, Profile, Authority. Don't waste time reverse-engineering label names unless the user specifically requires a particular app's naming.

### Engine Documentation

The OpenHumanDesignMCP project includes:
- `docs/SYSTEM_PROMPT.md` — LLM-ready system prompt with HD primer, tool selection guide, plain-English translation
- `docs/API.md` — Complete 15-tool reference with parameters, return values, examples
- `docs/TUTORIAL.md` — Beginner walkthrough: install, verify, first chart, transits, synastry
- `docs/playground/index.html` — Zero-install web chart computer (single HTML file, calls `--host` API)

## MCP Server Architecture & Building

This section covers building and deploying the OpenHumanDesignMCP server — patterns apply to any domain calculation engine exposed via MCP.

### Project Structure

```
project-name/
├── src/
│   ├── __init__.py
│   ├── setup_assets.py      # Downloads required data files
│   ├── engine_core.py       # Thread-safe core computation
│   ├── mapper.py            # Domain-specific mapping/hashing
│   ├── calculator.py        # High-level calculation orchestration
│   ├── schemas.py           # Pydantic v2 data contracts
│   └── mcp_server.py        # FastMCP gateway
├── data/                    # Downloaded/serialized data assets
├── requirements.txt
└── README.md
```

### pyswisseph (Swiss Ephemeris)

Installation: `pip install pyswisseph` (works on Python 3.12, v2.10.3.2+). If it times out at 60s, it's compiling — give it 2-3 minutes.

**CRITICAL — API return format**: `swe.calc_ut(jd, planet_id, flags)` returns a **tuple of (position_tuple, flags_int)** — NOT a flat tuple. Unpack with `result[0]` for the position.

```python
result = swe.calc_ut(jd, planet_id, swe.FLG_SWIEPH | swe.FLG_SPEED)
pos = result[0]  # (longitude, latitude, distance, speed_long, speed_lat, speed_dist)
longitude = pos[0]
retrograde = pos[3] < 0
```

**Thread safety**: pyswisseph's C library is NOT thread-safe. Wrap EVERY call in a global `threading.Lock()`.

**MCP stdio firewall**: MCP communicates over stdio. ALL prints, logs, warnings MUST go to `sys.stderr`. Any `print()` to stdout will corrupt the MCP protocol stream.

**Planet IDs**: Sun=0, Moon=1, Mercury=2, Venus=3, Mars=4, Jupiter=5, Saturn=6, Uranus=7, Neptune=8, Pluto=9, TRUE_NODE=11 (NOT Mean Node), Earth = Sun + 180°, South Node = True Node + 180°.

### Ephemeris Data Files

Download from AstroDienst GitHub mirror: `https://github.com/aloistr/swisseph/raw/master/ephe/`

Core files for 1900-2100: `seas_18.se1`, `semo_18.se1`, `sepl_18.se1`, `seorbel.txt`

Place in `./ephemeris/` and point `swe.set_ephe_path(str(ephe_dir))`.

#### Auto-Download Pattern

```python
def init_ephemeris(ephe_path=None, auto_setup=True):
    if ephe_path is None:
        ephe_path = str(Path(__file__).parent.parent / "ephemeris")
    if auto_setup:
        core_file = os.path.join(ephe_path, "sepl_18.se1")
        if not os.path.exists(core_file):
            from setup_ephemeris import setup_ephemeris
            setup_ephemeris()
```

This eliminates the manual download step — first run auto-downloads.

### CLI Flags Pattern

Every MCP server should support these standard flags (argparse in `__main__`):

- **`--verify`** — Compute a SYNTHETIC reference chart and check engine health. Checks: ephemeris loaded, type detection works, profile format valid, authority computed, cross has name, centers computed, variables generated. Exit code 0 on pass, 1 on fail.
- **`--print-config`** — Output ready-to-paste MCP client config JSON with absolute paths. Use `os.path.abspath(__file__)`.
- **`--license`** — Print AGPLv3 license information, copyright, Section 13 obligations, and verification fingerprint.
- **`--host host:port`** — Run as HTTP server (SSE transport) instead of stdio. WARNING: no built-in auth.

```python
if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--verify", action="store_true")
    p.add_argument("--print-config", action="store_true")
    p.add_argument("--host", type=str)
    args = p.parse_args()

    if args.print_config:  # ... output config JSON
    if args.verify:        # ... run known test case
    if args.host:          # ... HTTP server mode
    mcp.run()              # default: stdio MCP
```

### FastMCP Gateway

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ServerName")

@mcp.tool()
def calculate_something(params) -> dict:
    """Tool description visible to LLM."""
    return result

if __name__ == "__main__":
    mcp.run()
```

Expose calculation functions as `@mcp.tool()` decorated functions. Keep return values serializable. Run via `python src/mcp_server.py` — MCP transport is stdio.

### Geo Resolver — Production Pattern

Replace hardcoded LOCATIONS dict with timezonefinder + pytz:

```python
from timezonefinder import TimezoneFinder
import pytz

def resolve_location(location_str, lat=None, lon=None):
    # Accept "lat,lon" string format
    # Use TimezoneFinder for coordinate → timezone
    # Use pytz for accurate timezone + DST handling
```

- Lazy-load timezonefinder (downloads timezone data on first use)
- Keep a KNOWN_CITIES dict for city → (lat, lon) mapping
- Unknown locations should warn explicitly, not silently default to UTC+0

### Package Name Mismatch Pitfall

If the project directory is `hd-mcp-server` (hyphens) but pyproject.toml uses `hd_mcp_server` (underscores), `pip install -e .` fails. Use direct script paths in configs:
```json
{
  "command": "python3",
  "args": ["hd-mcp-server/src/mcp_server.py"],
  "env": {"PYTHONPATH": "hd-mcp-server/src"},
  "cwd": "/path/to/repo"
}
```

### Dockerfile Pattern

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[render]"
COPY hd-mcp-server/ hd-mcp-server/
COPY README.md LICENSE CHANGELOG.md ./
VOLUME ["/app/ephemeris"]
RUN python -c "from ... import init_ephemeris; init_ephemeris()" \
    && python hd-mcp-server/src/mcp_server.py --verify
EXPOSE 8765
CMD ["python3", "hd-mcp-server/src/mcp_server.py", "--host", "0.0.0.0:8765"]
```

Key: `COPY pyproject.toml` first → `pip install` → THEN `COPY src/` for layer caching. `RUN --verify` catches broken builds at build time. `VOLUME` for ephemeris persistence.

### Open Source PII Hygiene

- **Birth data leaks**: Most common in `__main__` verification blocks, test files, README examples, docstrings. Replace ALL with synthetic data (Jan 1, 2000, 12:00 UTC, equator).
- **Hardcoded paths**: Never commit `/home/username/work/...`. Use environment variables.
- **Git history**: If PII was ever committed, force-push a clean history with `git checkout --orphan clean-main`.
- **.gitignore**: Add `*birth*`, `*family*`, `*friends*`, `*personal*`, `tests/local/`, `.env`, `*.secret`, `secrets/`, `credentials/`.
- **Test data**: Real verification data in gitignored files only. Committed tests use synthetic data. Templates show format with example data.

### AGPLv3 Compliance Watermarking

1. **`ping()` tool** — Return AGPLv3 notice + repo URL + Section 13 obligations
2. **`--license` CLI flag** — Print full copyright and verification fingerprint
3. **Mathematical DNA marker** — The 302.000° Rave Mandala anchor is a verifiable fingerprint. Charts from this engine are provably distinct from engines using the common 301.875° hallucination.

### Package Installation (PEP 668)

On Debian/Ubuntu with PEP 668, use the hermes-agent venv:
```bash
/home/ubuntu/.local/share/pipx/venvs/hermes-agent/bin/python -m pip install <package>
```

## CRITICAL: read_file Output Format — Never Pipe to write_file

`read_file` returns `LINE_NUM|CONTENT` format (e.g., `    42|  body { ...`).
If you pass this output directly to `write_file`, `cp`, `patch`, or any other
file-creation tool, the line number prefixes get BAKED INTO the file. This
breaks HTML (corrupts `<style>` tags), Python (syntax errors), and any format
where exact content matters.

**This happened in production**: all 4 landing pages on humandesignengine.com
were served with `1|<!DOCTYPE html>` — line numbers in the HTML output. CSS
broke. Site looked unstyled. Root cause: `read_file` output was used as input
to `cp landing-index.html index.html`.

**Fix corrupted files with:**
```python
import re
content = re.sub(r'^\s*\d+\|', '', content, flags=re.MULTILINE)
```

**Prevention**: NEVER pipe `read_file` output to `write_file`. Always use the
original source. If you need to copy a file, use `cp` in terminal — never
read_file → write_file as a content relay.
