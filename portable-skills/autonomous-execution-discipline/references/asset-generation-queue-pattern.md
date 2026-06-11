# Asset Generation Queue Pattern

**Sessions:** June 8-9, 2026 — Darius Star: Cyber Coelacanth

## The Pattern

When the user asks to generate assets (Imagen sprites, Veo SFX, Lyria music), queue them as background processes so they run while you continue working on code tasks. Never make the user wait for asset generation to finish.

## How It Works

1. **Verify the generation script** supports the asset IDs. If unknown IDs are passed, the script silently produces 0 results. Always add new entries to the catalog first (see Catalog Expansion below).

2. **Launch as background process:**
   ```python
   terminal(
       command="python3 generate_audio.py --track id1 id2 --delay 3 2>&1 | tee /tmp/gen.log",
       background=True,
       notify_on_complete=True,
       timeout=600
   )
   ```

3. **While generation runs:** Continue executing code tasks, creating Linear issues, launching AGY subagents. When `notify_on_complete` fires, update the relevant Linear issue.

## Parallel Batch Generation (PROVEN — June 9, 2026)

**Multiple background processes can run simultaneously.** Each `generate_audio.py` invocation is independent (different output files, no shared state). Proven: 4 batches × 51 total tracks launched in parallel — all succeeded with zero failures.

```python
# Launch all 4 independently — they run concurrently
terminal(command="python3 generate_audio.py --track boss_b1..boss_b10 --delay 3", background=True, notify=True)
terminal(command="python3 generate_audio.py --track midboss_b1..midboss_b10 --delay 3", background=True, notify=True)
terminal(command="python3 generate_audio.py --track engine_b1..engine_b10 --delay 3", background=True, notify=True)
terminal(command="python3 generate_audio.py --track env_b1_vent..env_b10_code --delay 3", background=True, notify=True)
```

**Cost:** Each batch costs ~$0.20–$0.40 (10–20 tracks × ~$0.02–$0.04 each). Total for 4 batches: ~$1.04 for 51 tracks.

**Monitoring:** Each batch writes to a separate log file (`/tmp/lyria-boss-music.log`, `/tmp/lyria-midboss-music.log`, etc.). Use `process(action='poll')` on individual session_ids to check progress. All four will `notify_on_complete` independently.

## Catalog Expansion — Programmatic (PROVEN — June 9, 2026)

When adding MANY entries (50+), do NOT patch one-by-one. Write a Python script that inserts all entries at once:

```python
# 1. Read the catalog file
# 2. Find the closing of the dict (e.g., MUSIC_CATALOG's closing })
# 3. Build new entries as a multi-line string
# 4. Insert before the closing }
# 5. Write back and verify syntax

# Example: adding 67 entries to generate_audio.py
new_entries = """
    "boss_b1_abyssal": {
        "scene": "Boss B1: Abyssal Trench",
        "prompt": ("Epic deep-sea boss battle, ..."),
        "duration": 30, "output": "assets/audio/boss_b1_abyssal.mp3", "loop": True,
    },
    ...
"""
new_content = content[:insert_point] + new_entries + "\n" + content[insert_point:]
```

**Verification:**
```bash
python3 -c "compile(open('generate_audio.py').read(),'generate_audio.py','exec'); print('✅ Syntax OK')"
python3 generate_audio.py --list | grep -c 'ID:'  # Count tracks
```

Proven: 14 → 81 tracks in one programmatic insertion. All 67 new tracks generated successfully in 4 parallel batches.

## Lyria 2 Specifics

- **Track IDs:** Lowercase, underscore-separated (e.g., `boss_b1_abyssal`, `engine_b5_ice`)
- **Prompt format:** "16-bit Sega Genesis style, retro arcade, instrumental. [SPECIFIC CHARACTER]. Short and impactful, no vocals."
- **Duration:** Music 30s, midboss 15s, ambients 30s, SFX 2s (minimum)
- **Output:** `assets/audio/<id>.mp3`
- **Spacing:** `--delay 3` (3 seconds between API calls)
- **Cost:** ~$0.04 per 30s track, ~$0.02 per 15s track

## Imagen 3 Pitfall (SILENT FAILURE)

The Imagen script silently skips unknown asset IDs — produces "Results: 0 Imagen, 0 mock fallback" with exit code 0. **Always `--list` first** or grep the catalog for exact IDs before launching. Unknown IDs are silently dropped — the script reports success but generated nothing.

## Proven Queue Sizes

| Tool | Count | Spacing | Est. Time | Cost |
|---|---|---|---|---|
| Lyria 2 | 6 tracks | 3s | ~18s | ~$0.24 |
| Lyria 2 | 14 tracks | 3s | ~42s | ~$0.56 |
| Lyria 2 | 51 tracks (4 parallel) | 3s | ~70s | ~$1.04 |
| Imagen 3 | 15 sprites | 22s | ~5.5 min | — |
| Imagen 3 | 30 backgrounds | 22s | ~11 min | — |
| Veo 3.1 | 41 SFX | 65s | ~45 min | — |

## Integration with Linear

After generation completes:
1. Update the Linear issue state (Todo → In Progress or Done)
2. Post a comment with the results
3. Update the relevant manifest file (audio_manifest.json, sprites.json)
4. Regenerate the manifest: `python3 generate_audio.py --manifest` or `python3 generate_sprites_manifest.py`
