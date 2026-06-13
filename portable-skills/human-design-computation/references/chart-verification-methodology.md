# HD Chart Verification Methodology — May 31, 2026

Systematic process for verifying our engine against Neutrino Design app reference screenshots.

## Reference Screenshot Archive

Screenshots are archived per-person at `~/work/hd-reports/reference-screenshots/{person}/`.
Each person needs 8 standard views:
1. `{person}_01_general.jpg` — Type, Profile, Authority, Cross, Strategy, Signature, Not-Self, Definition
2. `{person}_02_advanced.jpg` — Cognition, Determination, Environment, Variables, Motivation, Perspective, Trajectory
3. `{person}_03_activations.jpg` — Gate activations by planet (13 Design + 13 Personality)
4. `{person}_04_additional.jpg` — Melancholy, Fears, Penta Qualities, Genetic Trauma, Star Archetype
5. `{person}_05_cycles.jpg` — Saturn Return, Uranus Opposition, Chiron Return dates
6. `{person}_06_birthdata.jpg` — Birth Date (Local + UTC), Design Date (Local + UTC), Location, Time Zone
7. `{person}_07_astrohd_design.jpg` — Design Chiron, Lilith, Asc, Desc, MC, IC (gate + line + sign)
8. `{person}_08_astrohd_personality.jpg` — Personality Chiron, Lilith, Asc, Desc, MC, IC

## OCR Extraction

When vision API is down, use tesseract:
```bash
sudo apt-get install -y tesseract-ocr
tesseract {image} stdout 2>/dev/null
```

Tesseract output format for activations: `Gate.Line.Color.Tone.Base` per planet.
Design column is left, Personality column is right.

## Comparison Checklist

For each person, verify:
1. **Type** — BLOCKING if wrong (affects Strategy, Signature, Not-Self)
2. **Profile** — BLOCKING if wrong (affects entire social dynamic narrative)
3. **Authority** — CRITICAL if wrong (affects decision-making advice)
4. **Cross** — HIGH if gate mismatch; MEDIUM if name-mismatch only
5. **Centers** — BLOCKING if wrong number of defined/undefined
6. **Channels** — HIGH if extra/missing channels
7. **Planets (gate.line)** — Verify all 26 (13D + 13P) against reference activations
8. **Angles (gate.line)** — Verify all 8 (P/D Asc/Desc/MC/IC)
9. **Variables** — LOW (calculation school difference may explain discrepancies)

## Deduplication

Screenshots are cached by Telegram at `~/.hermes/profiles/orchestrator/image_cache/img_{hash}.jpg`.
When OCR matches an already-archived screen, skip the copy. Label duplicates explicitly.

## Known Reference Values (Verified May 31, 2026)

**Michael Gulden** — Dec 10 1989, 5:07 PM PST, Simi Valley CA
Cross: RAX of Rulership 4 (26/45 | 47/22)
D Sun: 47.5, D Earth: 22.5, P Sun: 26.3, P Earth: 45.3
Angles: P Asc 12.3, P MC 37.1, D Asc 34.1, D MC 40.6

**Becca Gulden** — Dec 14 1987, 4:18 AM PST, Tacoma WA
Cross: LAX of Confrontation 2 (26/45 | 6/36)
Angles: P Asc 44.3, P MC 4.4, D Asc 31.4, D MC 51.2

**Ella Georgeson** — Sep 17 2003, 8:46 AM PDT, Hillsboro OR
Cross: RAX of Eden 3 (6/36 | 12/11)
D Sun: 12.4, D Earth: 11.4, P Sun: 6.2, P Earth: 36.2
Angles: P Asc 48.6, P MC 53.4, D Asc 50.4, D MC 31.6

**William Gulden** — Sep 23 2017, 12:00 PM HST, Kailua HI
Cross: RAX of the Vessel of Love 3 (46/25 | 15/10)
Design Date UTC: Jun 24 2017, 09:19 AM
Angles: P Asc 5.6, P MC 6.3, D Asc 37.6, D MC 5.6

**Benjamin Gulden** — Sep 7 2015, 6:15 PM HST, Kailua HI
Cross: LAX of Dominion 2 (64/63 | 45/26)
D Sun: 45.1, P Sun: 64.5
Angles: P Asc 37.3, P MC 5.4, D Asc 54.6, D MC 28.1

**Victoria Gulden** — Jan 3 2020, 5:16 PM HST, Kailua HI
Cross: JUX of Opposition (38/39 | 57/51 | fixed)
D Sun: 57.1, P Sun: 38.4
Angles: P Asc 15.6, P MC 36.2, D Asc 43.3, D MC 4.6
⚠️ Engine P Asc 52.1 — manual override to 15.6 for reports
