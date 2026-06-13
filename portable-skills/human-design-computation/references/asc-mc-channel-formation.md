# Asc/MC Gate Channel Formation — Regression & Fix (May 30–31, 2026)

## The Bug

On May 30, 2026, the `circuit` filter in `compute_defined_centers()` was removed, allowing Ascendant, MC, IC, and Descendant gates to participate in channel formation. This was a **regression** — the original code correctly excluded these gates with `if g.get("gate") and g.get("circuit", True)`.

## The Fix

Restored on May 31, 2026. The filter now correctly reads:
```python
for g in personality_gates.values():
    if g.get("gate") and g.get("circuit", True):
        all_active.add(g["gate"])
for g in design_gates.values():
    if g.get("gate") and g.get("circuit", True):
        all_active.add(g["gate"])
```

Asc/MC/IC/Dsc entries in `collect_gates()` are already correctly marked:
```python
gates["Ascendant"] = {..., "circuit": False, ...}
gates["MC"] = {..., "circuit": False, ...}
```

## Why This Matters

Only the **13 planet activations** per side (Sun, Earth, Moon, Mercury, Venus, Mars, Jupiter, Saturn, Uranus, Neptune, Pluto, North Node, South Node) should form channels. Asc/MC/IC/Dsc positions are informational (for AstroHD angle analysis) but do NOT define centers.

## Reproduction Cases (all confirmed fixed May 31)

| Person | Symptom | Fix |
|--------|---------|-----|
| William Gulden | P MC (Gate 6) + planet Gate 59 → false channel 6-59 → Solar Plexus defined → Authority flipped from Sacral to Emotional | Restored filter |
| Michael Gulden | Multiple false channels → Type flipped from Projector to MG | Restored filter |
| Ella Georgeson | D MC (Gate 31) + planet Gate 7 → false channel 7-31 → G defined | Restored filter |

## Verification

All 7 audited people match Neutrino Design after the fix:
- Michael: Projector 3/5 Splenic ✅
- Becca: Projector 6/2 Splenic ✅
- Ella: MG 2/4 Emotional ✅ (5 defined, 4 open)
- William: Generator 3/6 Sacral ✅
- Benjamin: MG 5/1 Emotional ✅
- Victoria: Generator 4/1 Sacral ✅
- JT: MG 4/6 Sacral ✅
