# Full-Stack Asset Audit Methodology

## When to Use
Any creative/game project with design docs, asset manifests, and generated media files. Use this when you need to answer: "What assets are still missing? What's the gap between the design vision and what's on disk?"

## Proven On
Darius Star: Cyber Coelacanth, June 9, 2026. Cross-referenced 12 design docs against 2,498 sprite PNGs, 115 audio files, and code modules. Found 50+ missing background variants, 6 ambient tracks, 41 SFX, 10 environmental particle systems, and 4 polish systems. Created 8 Linear issues (GRO-983–990) spanning Imagen, Veo, Lyria, and AGY.

## The Pattern

### Phase 1: Parallel Audit (4 lanes)
Launch 4 simultaneous data-gathering passes. Do not wait for one to complete before starting the next.

**Lane 1 — Visual Assets (enhanced with dynamic-path cross-reference)**

Two-pass approach to catch ALL missing sprites, not just statically-referenced ones:

**Pass 1: Static paths.** Extract every `'assets/sprites/...'` string literal from all code files (HTML + JS):
```python
for m in re.finditer(r"""['"]assets/sprites/([^'"]+)['"]""", code):
    paths.add(m.group(1))
```

**Pass 2: Dynamic paths (template literals).** Find all loops that construct paths at runtime
(e.g., `playerSprites[key].src = \`assets/sprites/${key}.png\``). Simulate the path construction
by iterating the key arrays and building the resulting paths. After both passes, verify every
constructed path exists on disk:

```python
for key in enemy_types:  # from loadEnemySprites() in code
    path = f'assets/sprites/{key}_0.png'
    if not os.path.exists(path): missing.append(f'Enemy: {key}_0.png')
```

**Pass 3: Naming mismatch check.** When a file exists but with a different name than what
the code expects (e.g., code references `console_frame.png` but the actual file is
`ui-console-frame.png`), flag it — the sprite will 404 at runtime. Use `ls` on disk
to find similar filenames and report the mismatch.

**Lane 2 — Audio Assets**
```python
# Walk disk, categorize by prefix (music/ambient/sfx/voice)
# Cross-reference audio_manifest.json for generation status
```
Key questions:
- Ambient tracks per biome? Which biomes are silent?
- Music tracks cover all phases (title, gameplay, boss, victory, game over)?
- SFX coverage: lasers, explosions, shields, powerups, menu, death, warning, engine?
- Voice lines exist for all characters?

**Lane 3 — Design Docs**
```bash
# Read ALL docs in docs/ directory
# Extract: biome specs, environmental effects, music descriptions, SFX requirements
# Search for keywords: weather, rain, wind, mist, dust, smog, fog, particle, environment
```
Key questions:
- What environmental effects do the design docs promise?
- What audio moods are specified per biome?
- What polish elements are mentioned?

**Lane 4 — AGY Style Guide (delegate_task)**
```
Goal: Read all design docs and produce a cohesive style guide for missing elements.
CONSTRAINT: NO CODE CHANGES. READ-ONLY. Write only the output file.
Deliverable: docs/missing-elements-style-guide.md
```
AGY reads every design doc and synthesizes a comprehensive spec covering colors, particle behaviors, audio moods, and polish for EVERY biome — not just the ones with gaps.

### Phase 2: Gap Synthesis
Synthesize all 4 lanes into a structured gap analysis:

1. **Category tables** — what's complete vs missing, with counts
2. **Per-biome matrix** — each biome scored across backgrounds, ambient, particles, engine hum, environmental SFX
3. **Priority ranking** — critical (zero assets exist) vs important (minimal assets) vs polish

### Phase 3: Linear Issue Creation
Create one issue per gap category, not per individual asset:

**Bad:** "Generate ambient track for biome 5", "Generate ambient track for biome 6", ...
**Good:** "Generate 6 ambient audio tracks for biomes 5-10 via Lyria 2"

Bundle related work. Use the style guide as the reference in every issue description. Tag issues with the correct agent label (agent:fred for generation, agent:agy for code).

### Phase 4: Queue Generation
For assets that have proven generation scripts:

1. **Check catalog coverage** — does the script have prompts for what you need?
2. **If not, extend the catalog FIRST** — add entries to the script's prompt dictionary
3. **Verify syntax** — `python3 -c "compile(open('script.py').read(), 'script.py', 'exec')"`
4. **Verify --list** — confirm new entries appear
5. **Launch with background + notify_on_complete** — frees the orchestrator to keep working

**Imagen 3 queue:** `--delay 22` (respects 3/min quota, 22s spacing avoids 429)
**Veo 3.1 queue:** `--delay 65` (respects 1/min quota)
**Lyria 2 queue:** `--delay 3` (no strict quota, 3s spacing avoids rate limits)

### Phase 5: Follow-Through
When background processes complete:
- Verify generated files exist with correct sizes
- Update manifests
- Move Linear issues from Todo to In Progress/Done
- Comment on issues with generation results

## Pitfalls

- ❌ **Skipping the catalog extension step** — running the script with track IDs that don't exist in the catalog produces "Unknown track" errors. Check `--list` first.
- ❌ **Generating one-at-a-time** — Lyria 2 at 3s spacing × 6 tracks = 18 seconds. Imagen at 22s × 30 backgrounds = 11 minutes. Always batch.
- ❌ **Creating one Linear issue per asset** — bundle into category-level issues. 30 individual "generate background for biome X sub-level Y" issues = noise.
- ❌ **Not reading design docs before auditing** — the gap between design vision and reality is the whole point. You need to know what was promised.
- ❌ **Running audits sequentially** — all 4 lanes can run simultaneously. They read different data sources with no overlap.
