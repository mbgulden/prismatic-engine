---
name: human-design-channel-rendering
description: Unified channel rendering for HD bodygraph. Colors expand to fill available width slots. 4-quadrant system with white inactive fill.
category: human-design
triggers:
  - channel rendering
  - bodygraph channels
  - double line channels
  - half activation
  - hanging gates
---
# Human Design Channel Rendering

## Architecture
Each channel is a single **unified tube** (6px wide) with a thin `#ccc` outline. The tube is split into **4 quadrants** (2 halves × 2 width slots):

- **Left half** (gate A → midpoint) | **Right half** (midpoint → gate B)
- Colors expand to fill available width

## Color Expansion Rules
For each half (gateA→mid, mid→gateB):

| Active count | Behavior |
|---|---|
| 0 colors | Full width white (#ffffff) |
| 1 color | Fills BOTH width slots (full width) |
| 2 colors | Split width: left slot = black (personality), right slot = red (design) |

## Key Colors
- `PERSONALITY = '#000000'` (black)
- `DESIGN = '#EB5757'` (red)
- Inactive = `#ffffff` (white)

## Example: Channel 26-44 (Michael)
- Gate 26: personality ✓, design ✗
- Gate 44: personality ✗, design ✓
- Left half (26→mid): 1 active → full width BLACK
- Right half (mid→44): 1 active → full width RED

## Implementation
File: `~/work/hd-bodygraph/render-pro.mjs`
- `slotPoly(x1,y1,x2,y2, offsetCenter, slotWidth)` — builds quad polygon
- `drawHalf(x1,y1,x2,y2, hasPers, hasDes)` — colors one half
- `drawUnifiedChannel(pA,pB,persA,persB,desA,desB)` — full channel
- Outline: `slotPoly` with `fill="none" stroke="#ccc" stroke-width="0.5"`

## Related
- Gates INSIDE centers (not on edges), inset ~8px from border
- Open/undefined centers filled white (#ffffff) not transparent
- Defined centers get colored fill with #202020 border
- Undefined centers get white fill with #BEBEBE border
- Bodygraph live at `humandesignengine.com/bodygraph`
- Server outputs PDF by default: `rsvg-convert -f pdf`
