# Production Bodygraph Bridge Reference

## Architecture

```
MCP Engine (cosmic_calculator.py)
  → calculate_natal_chart()
  → chart dict with defined_centers, defined_channels, personality_planets, design_planets, variables, incarnation_cross
  → Bridge (server.py /api/public/bodygraph)
    → Extract full render data with gate/line/color/tone/base per planet
    → subprocess node render-pro.mjs <tmp.json>
    → SVG returned to client
```

## Primary Renderer: render-pro.mjs

`$PRISMATIC_HOME/work/hd-bodygraph/render-pro.mjs` — custom production SVG renderer built on Gonzih geometry.

**Does NOT call Gonzih's `renderToSVG()`.** Uses Gonzih geometry constants (inlined) but performs all SVG generation itself. This gives full control over every pixel.

## Bridge Data Mapping

### Planet Key Normalization

MCP engine uses Title Case keys; renderer expects lowercase. Ascendant and MC are excluded.

```
"Sun"        → "sun"
"Moon"       → "moon"
"Mercury"    → "mercury"
"Venus"      → "venus"
"Mars"       → "mars"
"Jupiter"    → "jupiter"
"Saturn"     → "saturn"
"Uranus"     → "uranus"
"Neptune"    → "neptune"
"Pluto"      → "pluto"
"True Node"  → "northnode"
"Earth"      → "earth"
"South Node" → "southnode"
"Ascendant"  → excluded
"MC"         → excluded
```

### Full render_data shape

```json
{
  "definedCenters": ["G", "Ego", "Spleen", "Throat"],
  "personalityGates": [1, 7, 8, 13, 14, 23, 25, 26, 38, 45, 52, 58, 60],
  "designGates": [6, 10, 22, 29, 30, 38, 44, 47, 48, 50, 52, 58],
  "bothGates": [26],
  "channels": [[1, 8], [26, 44]],
  "type": "Projector",
  "profile": "3/5",
  "definition": "Split",
  "authority": "Splenic",
  "strategy": "To Wait for Invitation",
  "incarnationCross": "Right Angle Cross of Rulership 4",
  "variables": "PRR DLR",
  "activations": {
    "design": {
      "sun":       {"gate": 47, "line": 5, "color": 2, "tone": 3, "base": 2},
      "earth":     {"gate": 22, "line": 5, "color": 2, "tone": 3, "base": 2},
      "northnode": {"gate": 30, "line": 2, "color": 2, "tone": 5, "base": 2},
      "southnode": {"gate": 29, "line": 2, "color": 2, "tone": 5, "base": 2},
      "moon":      {"gate": 30, "line": 4, "color": 3, "tone": 2, "base": 1},
      "mercury":   {"gate": 48, "line": 1, "color": 6, "tone": 4, "base": 1},
      "venus":     {"gate": 50, "line": 6, "color": 3, "tone": 5, "base": 4},
      "mars":      {"gate": 6,  "line": 4, "color": 6, "tone": 2, "base": 2},
      "jupiter":   {"gate": 52, "line": 5, "color": 1, "tone": 4, "base": 4},
      "saturn":    {"gate": 58, "line": 4, "color": 4, "tone": 6, "base": 4},
      "uranus":    {"gate": 10, "line": 4, "color": 2, "tone": 5, "base": 4},
      "neptune":   {"gate": 38, "line": 1, "color": 1, "tone": 5, "base": 4},
      "pluto":     {"gate": 44, "line": 6, "color": 6, "tone": 2, "base": 3}
    },
    "personality": {
      "sun":       {"gate": 26, "line": 3, "color": 1, "tone": 4, "base": 3},
      "earth":     {"gate": 45, "line": 3, "color": 1, "tone": 4, "base": 3},
      "northnode": {"gate": 13, "line": 6, "color": 3, "tone": 4, "base": 2},
      "southnode": {"gate": 7,  "line": 6, "color": 3, "tone": 4, "base": 2},
      "moon":      {"gate": 8,  "line": 2, "color": 5, "tone": 5, "base": 2},
      "mercury":   {"gate": 58, "line": 2, "color": 3, "tone": 1, "base": 5},
      "venus":     {"gate": 60, "line": 5, "color": 3, "tone": 3, "base": 3},
      "mars":      {"gate": 14, "line": 1, "color": 4, "tone": 3, "base": 4},
      "jupiter":   {"gate": 52, "line": 5, "color": 3, "tone": 2, "base": 5},
      "saturn":    {"gate": 38, "line": 4, "color": 6, "tone": 4, "base": 1},
      "uranus":    {"gate": 58, "line": 1, "color": 5, "tone": 1, "base": 1},
      "neptune":   {"gate": 38, "line": 2, "color": 6, "tone": 1, "base": 3},
      "pluto":     {"gate": 1,  "line": 4, "color": 3, "tone": 2, "base": 3}
    }
  }
}
```

## Python Bridge Code

```python
_PLANET_MAP = {
    "Sun": "sun", "Moon": "moon", "Mercury": "mercury", "Venus": "venus",
    "Mars": "mars", "Jupiter": "jupiter", "Saturn": "saturn",
    "Uranus": "uranus", "Neptune": "neptune", "Pluto": "pluto",
    "True Node": "northnode", "Earth": "earth", "South Node": "southnode",
}

def _act_pro(planets_dict):
    result = {}
    for planet, data in planets_dict.items():
        key = _PLANET_MAP.get(planet)
        if key and isinstance(data, dict) and data.get("gate"):
            result[key] = {
                "gate": data.get("gate", ""),
                "line": data.get("line", ""),
                "color": data.get("color", ""),
                "tone": data.get("tone", ""),
                "base": data.get("base", ""),
            }
    return result

cross = chart.get("incarnation_cross", {})
cross_name = cross.get("name", "") if isinstance(cross, dict) else str(cross)

center_map = {"Heart": "Ego", "Heart/Ego": "Ego"}

render_data = {
    "definedCenters": [center_map.get(c, c) for c in chart.get("defined_centers", [])],
    "personalityGates": pers_only,
    "designGates": des_only,
    "bothGates": both_gates,
    "channels": [ch["gates"] for ch in chart.get("defined_channels", [])
                 if len(ch.get("gates", ())) == 2],
    "type": chart.get("hd_type", ""),
    "profile": str(chart.get("profile", "")),
    "definition": chart.get("definition", ""),
    "authority": chart.get("authority", ""),
    "strategy": chart.get("strategy", ""),
    "incarnationCross": cross_name,
    "variables": chart.get("variables", ""),
    "activations": {
        "design": _act_pro(chart.get("design_planets", {})),
        "personality": _act_pro(chart.get("personality_planets", {})),
    },
}

# Render
with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
    json.dump(render_data, f)
    tmp = f.name
result = subprocess.run(
    ["node", os.environ.get("PRISMATIC_HOME", "/home/ubuntu") + "/work/hd-bodygraph/render-pro.mjs", tmp],
    capture_output=True, text=True, timeout=15,
    cwd=os.environ.get("PRISMATIC_HOME", "/home/ubuntu") + "/work/hd-bodygraph",
)
svg = result.stdout
```

## Channel Coloring Logic (render-pro.mjs, current as of 2026-05-30)

For each channel `[a, b]`:
- Both gates active, Design only → Red (#c62828), solid 2px line
- Both gates active, Personality only → Black (#1a1a1a), solid 2px line
- One gate Design, one Personality (mixed) → **Parallel offset lines**: red +1.0px perpendicular, black -1.0px perpendicular
- Both gates active in both Design and Personality (both) → Parallel red+black as above
- Inactive → Hollow style: 2.5px #ddd outline + 1.5px white core (NOT dashed, NOT solid gray)
- Hanging (one side active): active half colored, inactive half hollow

All channels are **straight lines only** (M→L SVG paths, zero bezier curves). Channels drawn BEFORE centers.

### Parallel offset implementation (current values)

```javascript
function offsetPath(x1, y1, x2, y2, offset) {
  const dx = x2 - x1, dy = y2 - y1;
  const len = Math.sqrt(dx * dx + dy * dy);
  if (len < 0.01) return { x1, y1, x2, y2 };
  const px = -dy / len * offset, py = dx / len * offset;
  return {
    x1: x1 + px, y1: y1 + py,
    x2: x2 + px, y2: y2 + py,
  };
}
// Red line: offsetPath(x1, y1, x2, y2, +1.0)
// Black line: offsetPath(x1, y1, x2, y2, -1.0)
```

## Center Colors (current as of 2026-05-30)

| Center | Color |
|--------|-------|
| Head | #f9e076 |
| Ajna | #4fc3f7 |
| Throat | #a5d6a7 |
| G | #f9df80 |
| Ego | #c34d4d |
| Sacral | #ef5350 |
| SolarPlexus | #ce93d8 |
| Spleen | #906265 |
| Root | #a1887f |

Undefined centers: white fill (`#ffffff`), NO border (`stroke="none"`). Defined centers: colored fill, 1px `#1a1a1a` border.

## Testing

```bash
# Local test (GET with query params — preferred for Cloudflare Pages widgets)
curl -s "http://localhost:8081/api/public/bodygraph?name=Michael&year=1989&month=12&day=10&hour=17&minute=7&timezone=America/Los_Angeles&lat=34.2694&lon=-118.7815" > /tmp/test.svg

# Local test (POST — same endpoint, body-based)
curl -s -X POST http://localhost:8081/api/public/bodygraph \
  -H "Content-Type: application/json" \
  -d '{"name":"Michael","year":1989,"month":12,"day":10,"hour":17,"minute":7,"timezone":"America/Los_Angeles","lat":34.2694,"lon":-118.7815}'

# Live test
curl -s "https://reports.humandesignengine.com/api/public/bodygraph?name=Becca&year=1987&month=12&day=14&hour=4&minute=18&lat=47.2529&lon=-122.4443&timezone=America/Los_Angeles" > /tmp/live.svg

# Render to PNG for visual inspection
rsvg-convert -w 410 /tmp/live.svg -o /tmp/live.png
```

## Parallel Offset Verification

Verify mixed channels have properly offset parallel lines (not dash overlay):

```bash
python3 -c "
import xml.etree.ElementTree as ET
tree = ET.parse('/tmp/chart.svg')
root = tree.getroot()
ns = '{http://www.w3.org/2000/svg}'

paths = root.findall(f'.//{ns}path')
design_offsets = [p for p in paths if p.get('stroke') == '#b71c1c' and p.get('stroke-width') == '2.5']
pers_offsets = [p for p in paths if p.get('stroke') == '#1a1a1a' and p.get('stroke-width') == '2.5']
print(f'Design offset lines: {len(design_offsets)}, Personality offset lines: {len(pers_offsets)}')

# Should be equal counts — one red + one black per mixed channel
# Zero dash-overlay paths (old approach used stroke-dasharray)
dash_overlays = [p for p in paths if p.get('stroke-dasharray') == '8,8']
print(f'Dash overlay paths (should be 0): {len(dash_overlays)}')

curves = [p for p in paths if 'C' in (p.get('d') or '')]
print(f'Curved paths (should be 0): {len(curves)}')
"
```

```bash
# Parse the SVG to verify all production features
python3 -c "
import xml.etree.ElementTree as ET
tree = ET.parse('/tmp/chart.svg')
root = tree.getroot()
ns = '{http://www.w3.org/2000/svg}'

# Activation cards (rect with rx=4 that aren't center shapes)
rects = root.findall(f'.//{ns}rect')
cards = [r for r in rects if r.get('rx') == '4']
print(f'Activation cards: {len(cards)} (expect 26)')

# Gate circles
circles = root.findall(f'.//{ns}circle')
print(f'Gate circles: {len(circles)} (expect ~28)')

# Striped channels
paths = root.findall(f'.//{ns}path')
striped = [p for p in paths if p.get('stroke-dasharray') == '8,8']
print(f'Striped channels: {len(striped)}')

# Variable arrows
polygons = root.findall(f'.//{ns}polygon')
arrows = [p for p in polygons if p.get('fill') == '#5a3e28']
print(f'Variable arrows: {len(arrows)}')
"
```

## Pitfalls

- **CRITICAL — Mixed channels use parallel offset, not dash overlay**: The user explicitly rejected the dash-overlay approach. Draw two separate lines offset perpendicular to the channel direction. See implementation above.
- `defined_channels` uses Python tuples `(2, 14)` which become JSON arrays `[2, 14]` — the renderer handles both arrays and `"2-14"` strings
- Center names: `"Heart"` or `"Heart/Ego"` must map to `"Ego"` for Gonzih geometry compatibility
- Planet key mapping is critical — MCP engine uses Title Case, renderer expects lowercase normalized names
- **Activations must be full objects** (`{"gate":47,"line":5,"color":2,"tone":3,"base":2}`), NOT strings like `"47.5"`. This is the key difference from the old `render-hde.mjs` bridge
- `incarnation_cross` from the engine is a dict `{"name":"...","angle_type":"...","gates":{...}}` — extract `cross["name"]` for the renderer
- Widget HTML must NOT have `data-api` attribute pointing to old tunnel URLs — let the widget JS default to `reports.humandesignengine.com`
- The Gonzih dist (`dist/index.mjs`) does NOT export geometry constants — they are inlined directly in `render-pro.mjs`
- `render-pro.mjs` is a **complete rewrite** — it does not import or call Gonzih's `renderToSVG()`. All SVG generation is custom
