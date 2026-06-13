# Center Shape Rendering — Equilaterals, Squares, Diamonds

## Geometry Standards (2026-05-30)

| Shape | Centers | w | h | Rationale |
|---|---|---|---|---|
| Triangle (up) | Head | 100 | 87 | h = w × √3/2 ≈ 86.6, rounded to 87 |
| Triangle (down) | Ajna | 100 | 87 | Same equilateral as Head |
| Triangle (left) | Ego, Solar Plexus | 100 | 87 | Same equilateral, rotated |
| Triangle (right) | Spleen | 100 | 87 | Same equilateral, rotated |
| Rounded rect | Throat, Sacral, Root | 100 | 100 | Square |
| Diamond (45°) | G | 110 | 110 | Square rotated 45°, slightly larger |

**Key rule**: Same-shape centers MUST have identical dimensions. All triangles = 100×87. All rects = 100×100.

## SVG Path Templates

### Triangle-Up (Head)
```javascript
const r = 4; // corner rounding radius
s = `<path d="M${cx},${cy - hh + r} L${cx + hw - r},${cy + hh - r} Q${cx + hw},${cy + hh} ${cx + hw - r},${cy + hh} L${cx - hw + r},${cy + hh} Q${cx - hw},${cy + hh} ${cx - hw + r},${cy + hh - r} Z`
  + `" fill="${fill}" stroke="${stroke}" stroke-width="${sw}" stroke-linejoin="round"/>`;
```
- Top point → bottom-right corner (with Q rounding) → bottom edge → bottom-left corner (with Q rounding) → close
- Clean equilateral triangle, no curved bottom

### Triangle-Down (Ajna)
```javascript
s = `<path d="M${cx - hw + r},${cy - hh + r} Q${cx - hw},${cy - hh} ${cx - hw + r},${cy - hh} L${cx + hw - r},${cy - hh} Q${cx + hw},${cy - hh} ${cx + hw - r},${cy - hh + r} L${cx},${cy + hh} Z`
  + `" fill="${fill}" stroke="${stroke}" stroke-width="${sw}" stroke-linejoin="round"/>`;
```

### Triangle-Left (Ego, Solar Plexus)
```javascript
s = `<path d="M${cx + hw - r},${cy - hh + r} Q${cx + hw},${cy - hh} ${cx + hw},${cy - hh + r} L${cx + hw},${cy + hh - r} Q${cx + hw},${cy + hh} ${cx + hw - r},${cy + hh} L${cx - hw},${cy} Z`
  + `" fill="${fill}" stroke="${stroke}" stroke-width="${sw}" stroke-linejoin="round"/>`;
```

### Triangle-Right (Spleen)
```javascript
s = `<path d="M${cx - hw + r},${cy - hh + r} Q${cx - hw},${cy - hh} ${cx - hw},${cy - hh + r} L${cx - hw},${cy + hh - r} Q${cx - hw},${cy + hh} ${cx - hw + r},${cy + hh} L${cx + hw},${cy} Z`
  + `" fill="${fill}" stroke="${stroke}" stroke-width="${sw}" stroke-linejoin="round"/>`;
```

### Diamond (G Center)
```javascript
const t = 0.12; // corner rounding factor
const dx = hw * t, dy = hh * t;
s = `<path d="M${cx + dx},${cy - hh + dy} L${cx + hw - dx},${cy - dy} Q${cx + hw},${cy} ${cx + hw - dx},${cy + dy} L${cx + dx},${cy + hh - dy} Q${cx},${cy + hh} ${cx - dx},${cy + hh - dy} L${cx - hw + dx},${cy + dy} Q${cx - hw},${cy} ${cx - hw + dx},${cy - dy} L${cx - dx},${cy - hh + dy} Q${cx},${cy - hh} ${cx + dx},${cy - hh + dy} Z`
  + `" fill="${fill}" stroke="${stroke}" stroke-width="${sw}" stroke-linejoin="round"/>`;
```

### Rounded Rect (Throat, Sacral, Root)
```javascript
let rx = 8;
if (c.id === 'Sacral') rx = 28;
else if (c.id === 'Throat') rx = 16;
else if (c.id === 'Root') rx = 20;
s = `<rect x="${cx - hw}" y="${cy - hh}" width="${w}" height="${h}" rx="${rx}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
```

## Common Mistakes

1. **Using w=h for triangles** — w=100, h=100 produces a triangle that's too tall/narrow, not equilateral. Must use h = w × √3/2.
2. **Curved bottom on Head triangle** — the old path had multiple Q curves on the bottom edge creating a curved/bulging bottom. User: "the triangles on the top are definitely not equilateral. One on the top has a curved bottom, weird."
3. **Different sizes for same shapes** — all triangles must be identical size, all rects identical, regardless of which center they represent.
4. **String template quoting** — when using template literals with `d="..."`, ensure the Z command is NOT followed by `"` then `"` again (creates `Z""` syntax error). Put Z inside the path data and the closing `"` in the next template segment.
