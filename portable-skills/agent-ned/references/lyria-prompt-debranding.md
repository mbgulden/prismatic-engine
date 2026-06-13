# Lyria 2 Prompt De-Branding — Worked Example (GRO-1272, Jun 2026)

When Lyria 2 returns `InvalidArgument: 400 Music generation failed`, the prompt
almost certainly contains a branded artist or soundtrack reference. Lyria's
content filter blocks these.

## Detection

Run `--track <id>` on a single track. If it fails with the 400 error while
`--check` succeeds (proving auth is valid), the prompt has branded references.

## Fix Pattern

Replace branded references with descriptive genre/mood terms:

| Blocked phrase | Replacement |
|---|---|
| "Daft Punk Tron Legacy style" | "retro-futuristic synth style" |
| "Hans Zimmer Interstellar organ swells" | "cinematic organ swells with emotional depth" |
| "Vangelis Blade Runner style" | "cinematic sci-fi noir style" |
| "Johann Johannsson Arrival textures" | "otherworldly orchestral textures" |
| "Trent Reznor tension" | "pulsing industrial tension" |
| "Daft Punk propulsion" | "retro-electronic propulsion" |
| "Ben Salisbury Annihilation alien beauty" | "alien organic-synthetic beauty" |
| "M83 Oblivion emotional sweep" | "sweeping emotional synth-orchestral blend" |
| "Disasterpeace Hyper Light Drifter melodic sensibility" | "pixel-cinematic melodic sensibility" |
| "John Carpenter style driving bassline" | "synth-driven driving bassline" |
| "Lorn style heavy distorted bass" | "heavy distorted bass with deep sub weight" |
| "Trent Reznor digital decay textures" | "glitch-textured digital decay" |
| "Hans Zimmer style emotional crescendo" | "emotional orchestral crescendo with organ swells" |
| "16-bit Sega Genesis style" | (this one is OK — generic era reference, not a specific artist) |

## Workflow

1. Identify all prompts with branded references:
   ```bash
   python3 -c "
   import tools.generate_audio as ga
   for tid, info in ga.MUSIC_CATALOG.items():
       p = info['prompt']
       # Check for common branded terms
       branded = ['Daft Punk', 'Hans Zimmer', 'Vangelis', 'Blade Runner',
                  'Trent Reznor', 'Lorn', 'M83', 'Disasterpeace',
                  'John Carpenter', 'Johannsson', 'Ben Salisbury',
                  'Cliff Martinez', 'Interstellar', 'Tron Legacy',
                  'Annihilation', 'Oblivion', 'Arrival', 'Hyper Light']
       hits = [b for b in branded if b.lower() in p.lower()]
       if hits:
           print(f'{tid}: {hits}')
   "
   ```

2. Edit `tools/generate_audio.py` — modify prompts inline using `patch` tool.
   Each prompt is in `MUSIC_CATALOG["track_id"]["prompt"]`. Replace branded
   phrases with genre descriptions. Update the `scene` description too.

3. Regenerate affected tracks:
   ```bash
   python3 tools/generate_audio.py --track theme_heroic
   ```

4. Verify with `--track` that each previously-blocked prompt now succeeds.

5. Commit both the prompt fixes AND the generated MP3s in one commit.

## Real Session (GRO-1272, Jun 2026)

10 main theme prompts had branded references across 8 catalog entries:

| Track | Blocked phrases | Fixed? |
|---|---|---|
| theme_heroic | "Daft Punk Tron Legacy", "Hans Zimmer Interstellar" | ✅ |
| theme_mystery | "Vangelis Blade Runner", "Johann Johannsson Arrival" | ✅ |
| theme_action | "Trent Reznor", "Daft Punk" | ✅ |
| theme_lament | "Ben Salisbury Annihilation", "M83 Oblivion" | ✅ |
| theme_wonder | "Disasterpeace Hyper Light Drifter", "John Carpenter" | ✅ |
| theme_dark | "Lorn", "Trent Reznor" | ✅ |
| title_screen | "Daft Punk Tron Legacy", "Vangelis Blade Runner" | ✅ |
| victory | "Hans Zimmer" | ✅ |

Two ambient tracks (ambient_deep_space, ambient_abyssal_trench) had no branded
references and would have generated fine with the original prompts.

All 10 tracks generated successfully after de-branding. Total cost: $0.40.

## Not a Recitation Block

Don't confuse with the *recitation block* (overly simple prompts like "single
piano note"). Copyright blocks have different error messages — they say "Music
generation failed" not "recitation checks." The fix is de-branding, not
lengthening the prompt.
