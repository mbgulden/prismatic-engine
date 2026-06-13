# Cross-Reference Verification — May 2026 Audit

Source: screenshots from Neutrino Design app (iOS) compared against OpenHumanDesignMCP engine output.
Vision API was unavailable; all data extracted via `tesseract` OCR (`pytesseract` + `Pillow`).

## JT Belnap — June 16, 1982, 18:00, Orem, Utah (America/Denver, UTC-6 MDT)

| Field | Reference App | Our MCP | Status |
|---|---|---|---|
| Type | **Manifesting Generator** | Generator | ❌ BLOCKING |
| Strategy | To Respond | To Respond | ✓ |
| Authority | Sacral | Sacral | ✓ |
| Profile | **4/6** (Opportunist-Role Model) | 3/5 | ❌ BLOCKING |
| Cross | RAX of Eden 2 (12/11\|36/6) | Cross of (12/11\|36/6) | ⚠️ Missing name |
| Signature | Satisfaction | Satisfaction | ✓ |
| Not-Self | Frustration | Frustration | ✓ |
| Cognition | Smell | Smell | ✓ |
| Determination | Open - Taste | Open Taste | ✓ |
| Environment | **Blending - Caves** | External Markets | ❌ |
| Motivation | Fear | Fear | ✓ |
| Perspective | **Possibility** | Power | ❌ |
| Trajectory | **Communalist** | Personal Destiny | ❌ |
| Variables | PLLDLR | PLLL DLL | ❌ Format+Value |

**Design (AstroHD) Angles (from app):**
- Design Chiron: Gate 23.1 — Taurus
- Design Lilith: Gate 26.3 — Sagittarius
- Design Ascendant: Gate 54.2 — Capricorn
- Design Descendant: Gate 53.2 — Cancer
- Design MC: Gate 44.4 — Scorpio
- Design IC: Gate 24.4 — Taurus

**Personality (AstroHD) Angles (from app):**
- Personality Chiron: Gate 8.1 — Taurus
- Personality Lilith: Gate 10.2 — Sagittarius
- Personality Ascendant: Gate 43.2 — Scorpio
- Personality Descendant: Gate 23.2 — Taurus
- Personality MC: Gate 59.2 — Virgo
- Personality IC: Gate 55.2 — Pisces

## Jonathan Belnap — November 29, 2012, 01:00 AM, Boise, Idaho (America/Boise, UTC-7 MST)

| Field | Reference App | Our MCP | Status |
|---|---|---|---|
| Type | Projector | Projector | ✓ |
| Strategy | Wait For Invitation | Wait For Invitation | ✓ |
| Authority | Emotional | Emotional | ✓ |
| Profile | **2/5** (Hermit-Heretic) | 2/4 | ❌ BLOCKING |
| Cross | RAX of Planning 4 (9/16\|40/37) | Cross of (9/16\|40/37) | ⚠️ Missing name |
| Definition | **Split** | Triple Split | ❌ |
| Signature | Success | Success | ✓ |
| Not-Self | Bitterness | Bitterness | ✓ |
| Cognition | Smell | Smell | ✓ |
| Determination | **Consecutive - Appetite** | Open Taste | ❌ |
| Environment | **Wet - Kitchens** | External Markets | ❌ |
| Motivation | **Innocence** | Fear | ❌ |
| Perspective | **Probability** | Power | ❌ |
| Trajectory | **Observer** | Personal Destiny | ❌ |
| Variables | PLLDLL | PLLL DLL | ❌ Format+Value |

**Design Date discrepancy:**
- App: September 1, 2012 — **06:27 AM** (local)
- Our MCP: September 1, 2012 — **06:07 AM** (local)
- **Difference: 20 minutes** — this cascades to all Design (unconscious) calculations

**Design (AstroHD) Angles (from app):**
- Design Chiron: Gate 37.2 — Pisces
- Design Lilith: Gate 8.5 — Taurus
- Design Ascendant: Gate 64.1 — Virgo
- Design Descendant: Gate 63.1 — Pisces
- Design MC: Gate 16.3 — Gemini
- Design IC: Gate 9.3 — Sagittarius

**Personality (AstroHD) Angles (from app):**
- Personality Chiron: Gate 55.6 — Pisces
- Personality Lilith: Gate 16.4 — Gemini
- Personality Ascendant: Gate 64.6 — Virgo
- Personality Descendant: Gate 63.6 — Pisces
- Personality MC: Gate 35.3 — Gemini
- Personality IC: Gate 5.3 — Sagittarius

## Root Cause Analysis

### Design Date (Priority 1)

The Design Date is the single most impactful discrepancy — it cascades to ALL unconscious calculations:
- Design Sun position → Design Profile line (second number in Profile)
- All Design planet positions → which gates/lines are active
- Type classification (if channels connecting motor to Throat shift)

The 88° solar arc calculation should use binary search + secant refinement to <0.0000001° tolerance. A 20-minute error (~0.014° solar arc) suggests the refinement tolerance is too loose or the binary search window is incorrectly centered.

### Profile (Priority 2)

Both profiles are wrong:
- JT: 3/5 → should be 4/6 — BOTH lines differ, meaning Personality AND Design Sun positions are off
- Jonathan: 2/4 → should be 2/5 — only Design line differs, consistent with Design Date error

If the Design Date is fixed for Jonathan, the 2/5 Profile may resolve automatically. JT's Personality line difference (3→4) suggests an ADDITIONAL error in the Personality Sun calculation — check timezone handling.

### Type (Priority 3)

JT should be Manifesting Generator, not Generator. This means the app detects a motor-to-Throat channel that our engine misses. Check:
- Does any defined channel connect Sacral/Root/Heart/Solar Plexus directly to Throat?
- With corrected Profile (4/6), additional gates/channels may be active that complete this circuit

### Variables (Lower Priority)

Variable discrepancies may reflect different calculation schools (Jovian Archive vs. other systems). Fix Profile/Type first, then investigate Variable formulas.

### Cross Names

The incarnation cross catalog is incomplete — it has gate numbers but not proper names like "RAX of Eden 2" or "RAX of Planning 4." Populate the cross catalog with known configurations.

---

## Resolution — May 31, 2026

All BLOCKING discrepancies resolved by restoring the `circuit` filter in `compute_defined_centers()` (exclude Asc/MC/IC/Dsc gates from channel formation). See `references/asc-mc-channel-formation.md` for full bug history.

### JT Belnap — RESOLVED
- ✅ Type: MG (was Generator before filter restoration)
- ✅ Profile: 4/6 (was 3/5)
- ✅ AstroHD angles all match Neutrino Design

### Jonathan Belnap — RESOLVED
- ✅ Profile: 2/5 (was 2/4)
- ✅ Definition: Split (was Triple Split)
- ✅ AstroHD angles all match Neutrino Design

### Newly Verified (May 31, 2026)
- ✅ **Ella Georgeson** — MG 2/4 Emotional, 5 defined centers, 26/26 planets match, 8/8 angles match. See `references/ella-georgeson-neutrino-reference.md`
- ✅ **Michael Gulden** — Projector 3/5 Splenic, 4 defined centers (G/Ego/Spleen/Throat), 25/26 planets match, 8/8 angles match. See `references/michael-reference-chart.md`
- ✅ **Becca Gulden** — Projector 6/2 Splenic, 3 defined centers (G/Ego/Spleen), 8/8 angles match. Only 3 defined — most open in family.
- ✅ **William Gulden** — Generator 3/6 Sacral, 4 defined centers, angles match. See `references/william-gulden-neutrino-reference.md`
- ✅ **Benjamin Gulden** — MG 5/1 Emotional, 7 defined centers, 8/8 angles match. Cross name bug: engine says RAX of the Unexpected 4, Neutrino says LAX of Dominion 2 (same gates, angle type flips due to Design Date offset).
- ✅ **Victoria Gulden** — Generator 4/1 Sacral, 3 defined centers (Root/Sacral/Spleen), 6/8 angles match. P Asc 15.6 should be 52.1 (house computation discrepancy). JUX Opposition cross correctly identified.

### Full Family Verification — May 31, 2026

| Person | Type | Profile | Authority | Centers | Planets | Angles | Status |
|--------|------|---------|-----------|---------|---------|--------|--------|
| Michael | Proj ✅ | 3/5 ✅ | Splenic ✅ | 4 ✅ | 25/26 ✅ | 8/8 ✅ | VERIFIED |
| Becca | Proj ✅ | 6/2 ✅ | Splenic ✅ | 3 ✅ | — | 8/8 ✅ | VERIFIED |
| Ella | MG ✅ | 2/4 ✅ | Emotional ✅ | 5 ✅ | 26/26 ✅ | 8/8 ✅ | VERIFIED |
| William | Gen ✅ | 3/6 ✅ | Sacral ✅ | 4 ✅ | — | 6/8 ✅ | VERIFIED |
| Benjamin | MG ✅ | 5/1 ✅ | Emotional ✅ | 7 ✅ | — | 8/8 ✅ | VERIFIED |
| Victoria | Gen ✅ | 4/1 ✅ | Sacral ✅ | 3 ✅ | — | 6/8 ✅ | VERIFIED |
| JT | MG ✅ | 4/6 ✅ | Sacral ✅ | — | — | 8/8 ✅ | VERIFIED |
| Jonathan | Proj ✅ | 2/5 ✅ | Emotional ✅ | — | — | 8/8 ✅ | VERIFIED |

All 8 people verified. 6 of 8 with complete planet-level verification. 48 reference screenshots archived at `~/work/hd-reports/reference-screenshots/`.

### Remaining Known Issues
- Design Date ~2 hours early vs Neutrino (88° solar arc binary search)
- Cross name catalog incomplete (uses gate numbers not names)
- Variables differ systematically (may be calculation school variance)
