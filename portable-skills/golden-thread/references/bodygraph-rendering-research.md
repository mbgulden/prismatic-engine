# Bodygraph Rendering — Open-Source Research

Research date: 2026-05-30

## Findings

### 🥇 Gonzih/hd-bodygraph (TypeScript) — RECOMMENDED FOUNDATION
- **Repo**: github.com/Gonzih/hd-bodygraph
- **License**: No explicit license listed (MIT-compatible, check before commercial use)
- **Stars**: 0 (new project, not a quality signal)
- **Updated**: 2026-05-10 — active development
- **Description**: Human Design bodygraph SVG renderer with autonomous visual refinement loop

**What it provides:**
- Complete `renderToSVG(chartData)` → returns full SVG markup string
- All geometry pre-mapped in `geometry.ts`:
  - 9 center shapes with exact x,y positions (triangle-up for Head, diamonds for Ajna/G/Ego, rectangles for Throat/Sacral/Root, triangle-left for SolarPlexus, triangle-right for Spleen)
  - All 64 gate positions with exact x,y coordinates for pill placement
  - All 36 channel paths as SVG path strings
  - Body silhouette path, spine lines
  - ViewBox: 820x900
- `types.ts`: Complete TypeScript types — ChartData, CenterName, GateColoring, GateActivation, Activations, BodyGraphOptions, ThemePreset
- Full theming system:
  - Canonical HD.OS per-center colors (Head=pink, Ajna=red, Throat=blue, G=green, Ego=green, Sacral=orange, SolarPlexus/Sp=golden, Root=dark red)
  - Circuit colors: integration=teal, individual=brown, tribal=copper, collectiveLogic=navy, collectiveSensing=purple
  - Per-channel circuit mapping for all 36 channels
  - `default` and `canonical` theme presets
- Renders:
  - Body silhouette (low opacity background)
  - Spine (configurable line count)
  - Defined/undefined channels with circuit colors and glow effect
  - Center shapes with fill/stroke based on definition
  - Gate pills: rounded rectangles with design (red/left), personality (black/right), both (purple), inactive (light gray) coloring
  - Activation columns: design (left) and personality (right) with planet symbols and gate.line values
  - Center labels
- React component: `<BodyGraph>` wrapper
- AI-driven refinement loop: Playwright screenshots + pixelmatch diff + Claude vision → coordinate patches (sophisticated but optional)
- Exports: `renderToSVG`, `BodyGraph` (React), types

**Key advantage**: The geometry is already solved — 64 gate positions, 9 center shapes, 36 channel paths. This is the hardest part of bodygraph rendering.

### 🥈 jdempcy/hdkit (JavaScript/Ruby) — DATA UTILITIES
- **Repo**: github.com/jdempcy/hdkit
- **License**: MIT
- **Stars**: 169
- **Updated**: 2026-05-29 — recently updated
- **Description**: Open source Human Design programming toolkit (since 2016)

**What it provides:**
- `BodygraphScreen.tsx`: React component that loads an `empty-bodygraph.svg` template and mutates it via `react-svgmt`
- Data files: `constants.js` (gate orders, harmonic orders, planet glyphs, I-Ching hexagram glyphs, godheads), `gates.js`, `planets.js`, `signs.js`, `substructure.js`
- `bodygraph-data.js`: Computes type, authority, definition, incarnation cross from activation data (noted as "ChatGPT conversion of Ruby with known errors")
- Sample apps: Rails bodygraph generator, Node/React PDF maker, SVG Rave Mandala
- Full gate-by-gate modifier map in `BodygraphScreen.tsx` for visual positioning

**Caveats:**
- bodygraph-data.js has known ChatGPT-conversion errors (per author's note)
- SVG template approach (edit existing SVG) is less flexible than programmatic generation

### 🥉 ProsperousHeart/HumanDesign (Python)
- **Repo**: github.com/ProsperousHeart/HumanDesign
- **License**: MIT
- **Stars**: 27
- Python chart calculator — less relevant since OpenHumanDesignMCP already uses pyswisseph

### Other notable repos
- **robxcodes/gethdchart-web** (2★, TS/React): Website to generate HD chart
- **geodetheseeker/human-design-py** (3★, Python): Single-file chart calculator
- **reffan/bodygraph-api-php** (12★, PHP): Bodygraph calculation library

## Recommended Approach

1. **Fork Gonzih/hd-bodygraph** as the SVG rendering foundation (all geometry solved)
2. **Use hdkit** for data/text knowledge (gate descriptions, channel narratives, I-Ching references)
3. **Integrate with OpenHumanDesignMCP**: natal computation → Gonzih `renderToSVG()` → interactive bodygraph
4. **Add interactivity**: hover states, click to highlight, zoom/pan, responsive sizing
5. **Do NOT build bodygraph geometry from scratch** — Gonzih already mapped all 64 gate positions, 9 center shapes, and 36 channel paths

## License Check Required

Gonzih/hd-bodygraph has no explicit LICENSE file. Check with author or add MIT license before commercial use. hdkit is MIT — safe to use.
