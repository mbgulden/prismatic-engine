# Neutrino-Style Bodygraph Renderer Architecture

Reference renderer: `/home/ubuntu/work/hd-bodygraph/render-pro.mjs`

## Layout Specification (from May 30, 2026 pixel-level analysis of Neutrino reference)

### Center geometry

| Center | Shape | Direction | Proportions | Fill (defined) |
|--------|-------|-----------|-------------|----------------|
| Head | triangle | apex UP | near-square (64×64) | white |
| Ajna | triangle | apex DOWN | slightly wider (80×64) | white |
| Throat | square (rounded) | — | 90×80 | white |
| G | diamond | — | equal (110×110) | #fef3c7 yellow |
| Ego | triangle | apex DOWN | wide (70×60) | #E9A8A5 pink-red |
| Sacral | squircle (rx>10) | — | 120×110 | white (or yellow if defined) |
| Solar Plexus | triangle | apex UP | wide (110×90) | white |
| Spleen | triangle | apex DOWN | medium (90×65) | #E9A8A5 pink-red |
| Root | squircle (rx>10) | — | 110×70 | white |

### Drawing order (mandatory)

1. Background rect
2. **CHANNELS** — straight lines gate-to-gate (behind centers)
3. **CENTERS** — shapes with fills (cover channel mid-sections)
4. **GATE TEXT** — small gray numbers inside centers
5. **GATE PILLS** — capsules for activated gates

### Channel striping (parallel offset)

For mixed channels (one Design gate + one Personality gate):
```javascript
function offsetPath(x1, y1, x2, y2, offset) {
  const dx = x2 - x1, dy = y2 - y1;
  const len = Math.sqrt(dx*dx + dy*dy);
  if (len < 0.01) return { x1, y1, x2, y2 };
  const px = -dy / len * offset;
  const py = dx / len * offset;
  return {
    x1: Math.round(x1 + px), y1: Math.round(y1 + py),
    x2: Math.round(x2 + px), y2: Math.round(y2 + py),
  };
}
```

Red line: offset +3px, black line: offset -3px. Stroke-width 3, no glow.

### Center colors (visible palette)

Do NOT use near-white hex values like `#ffecee` — they're invisible on white background.
- Defined yellow: `#fef3c7` (visible warm gold)
- Defined pink-red: `#E9A8A5` (visible, matches Neutrino)
- Undefined: `#ffffff` (pure white)

### Bridge data format

Planet keys: `sun, earth, northnode, southnode, moon, mercury, venus, mars, jupiter, saturn, uranus, neptune, pluto`

Each activation: `{gate, line, color, tone, base}` (5-level precision)

Ascendant and MC are EXCLUDED from activations.
