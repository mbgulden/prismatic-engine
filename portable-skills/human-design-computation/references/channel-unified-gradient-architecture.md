# Channel Rendering Architecture — Full Evolution

## Final Working Approach (v7, 2026-05-30)

Channels are **unified 6px tubes** with side-by-side polygon halves. Each channel half is drawn as one or two polygons covering the channel width.

### Geometry

```
Channel: gateA ───── midpoint ───── gateB
          ← 6px total width →

Perpendicular offset slots:
  Left slot:  offsetCenter = -1.5, slotWidth = 3.5 → covers approx [-3.25, +0.25]
  Right slot: offsetCenter = +1.5, slotWidth = 3.5 → covers approx [-0.25, +3.25]
  Overlap at centerline: 0.5px (eliminates visible seams)
```

### Color Rules (per channel half)

| Colors Active | Result |
|---|---|
| 0 | Single full-width white polygon (offset 0, width 6) |
| 1 | Single full-width polygon filled with active color |
| 2 | TWO overlapping polygons: left=black (offset -1.5, width 3.5), right=red (offset +1.5, width 3.5) |

### Implementation

```javascript
function drawHalf(x1, y1, x2, y2, hasPers, hasDes) {
    const active = (hasPers ? 1 : 0) + (hasDes ? 1 : 0);
    if (active === 0) {
      return `<polygon points="${slotPoly(x1,y1,x2,y2,0,CH_W)}" fill="#ffffff"/>`;
    } else if (active === 1) {
      const color = hasPers ? PERSONALITY : DESIGN;
      return `<polygon points="${slotPoly(x1,y1,x2,y2,0,CH_W)}" fill="${color}"/>`;
    } else {
      const left = slotPoly(x1, y1, x2, y2, -SLOT_W, HALF_W + 0.5);
      const right = slotPoly(x1, y1, x2, y2, SLOT_W, HALF_W + 0.5);
      return `<polygon points="${left}" fill="${PERSONALITY}"/><polygon points="${right}" fill="${DESIGN}"/>`;
    }
}
```

The 0.5px extra width per side creates intentional overlap at the centerline — no visible seams, no "bumping out" effects, and the split line runs straight perpendicular to the channel regardless of angle.

### Channel Outline

Every channel has a thin outline polygon:
```javascript
`<polygon points="${slotPoly(pA.x,pA.y,pB.x,pB.y,0,CH_W)}" fill="none" stroke="#ccc" stroke-width="0.5"/>`
```

## Examples (Michael Gulden, Dec 10 1989)

**Channel 26-44** (gate 26=personality, gate 44=design):
- Left half (26→mid): 1 active → full-width black
- Right half (mid→44): 1 active → full-width red

**Channel 9-52** (gate 9=none, gate 52=both):
- Left half (9→mid): 0 active → full-width white
- Right half (mid→52): 2 active → side-by-side black/red with overlap

**Channel 1-8** (both gates=personality):
- Full length: 1 active both halves → full-width black


## Evolution of Approaches (all rejected, for historical record)

### v1: Double Parallel Lines (offset ±1.5px)
- Creates visible gap between the two lines
- Looks like separate disconnected lanes, not a unified channel
- User: "too many lanes inside one channel"

### v2: Overlay (full-width black + half-width red overlay)
- Red polygon extends beyond channel outline due to perpendicular offset math
- User: "red showing two lanes and getting bumped out of the channel"

### v3: SVG LinearGradient (objectBoundingBox)
- Single polygon with gradient fill, zero seams
- Split line appears at weird angle on diagonal channels
- User: "they are at a weird angle and not side by side"

### v4: SVG LinearGradient (userSpaceOnUse)
- Attempted to fix the angle issue with absolute coordinates
- Still produces wrong-looking splits on diagonal channels
- User: "38/58 are still not split in half properly"

### v5: Side-by-Side (no overlap)
- Two adjacent polygons sharing centerline boundary
- Floating-point rounding creates visible hairline seams

### v6: Side-by-Side with Gap
- `slotPoly(..., -SLOT_W, HALF_W - 0.3)` — width reduction creates visible gap
- Made the seam worse

### v7 (FINAL): Side-by-Side with 0.5px Overlap ✅
- `slotPoly(..., -SLOT_W, HALF_W + 0.5)` and `slotPoly(..., SLOT_W, HALF_W + 0.5)`
- Intentional overlap at centerline eliminates all visible seams
- Works correctly for all channel angles (vertical, horizontal, diagonal)
- Red polygon drawn second (on top), black underneath — visually correct left/right split
- No gradients, no defs, no analysis paralysis — simple and deterministic
