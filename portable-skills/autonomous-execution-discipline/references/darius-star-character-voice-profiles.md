# Darius Star — Character Voice Profiles Reference

> **File:** `~/work/darius-star/docs/character-voice-profiles.md`
> **Size:** 1,162 lines, 75KB
> **Last verified:** June 9, 2026

## What It Contains

Authoritative reference doc for all 8 speaking characters in Darius Star: Cyber Coelacanth. Each character profile includes:

1. **Speech Patterns** — sentence length, rhythm, formality level, verbal quirks
2. **Core Vocabulary** — frequently used words AND words they never use
3. **Catchphrases** — 3 per character with usage context
4. **Emotional Range** — angry, scared, triumphant, desperate, bonding, joking
5. **Voice Profile** — pitch, timbre, accent, speaking pace, EQ profile (specific dB values)
6. **Relationship Voice** — how they speak to every other character
7. **Stress Responses** — combat stress, emotional stress, sustained pressure
8. **Pull-Out Exclamations** — emergency damage/retreat calls (3-4 per character)
9. **Retreat Lines** — checkpoint fallback dialogue (3-4 per character)

## 8 Characters

| # | Name | Role | Vibe |
|---|---|---|---|
| 1 | **Darius Star** | Protagonist / The Determined | Baritone, grounded, scrapper metaphors |
| 2 | **Lyra Star** | Navigator / Emotional Center | Age 8, bright, ethereal harmonic underlay |
| 3 | **Naya Star** | The Heart / Protective Mother | Contralto, warm but steely, direct |
| 4 | **Valera Cross** | The Soldier / Defector | Alto, clipped military, guarded warmth |
| 5-8 | Remaining 4 characters | In doc tail | Defined in lines ~501-1162 |

## TTS Generation Notes

- Each character has specific EQ profiles (e.g., Darius: +2dB @ 120Hz, Lyra: +2dB @ 4kHz + +1dB @ 14kHz)
- PG-rated dialogue only — no swearing, sarcasm deliberately excluded
- 75% positive/uplifting, 25% urgent/tense tone split
- Pull-out lines and retreat lines are the highest-priority TTS recordings for gameplay
- File destination: `assets/audio/voices/<character>/`

## Dependencies

- GRO-1010 (Voice recordings) requires banter line data from GRO-957 (Banter System — 504 Lines Across 10 Biomes) which is currently in Backlog
- No `generate_tts.py` script exists yet in the darius-star repo
