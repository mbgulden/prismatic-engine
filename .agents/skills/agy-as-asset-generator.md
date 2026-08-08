---
name: agy-as-asset-generator
description: "AGY as visual asset generator: sprites, pixel art, UI panels, icons, moodboards using native GenerateImage and procedural scripts; exact dimensions, palettes, transparent PNGs, and manifest output."
tags: []
related_skills:
  - agy-delegate-goals-not-tasks
  - antigravity-cli-orchestration
---

# agy-as-asset-generator

## Purpose

Give AGY production-art constraints so it can generate usable assets, not vague visuals.

## When to Use

Use for Darius Star sprites/bosses/minions/projectiles/HUD, UI panels/icons, content illustrations, moodboards, and asset reference boards.

## Operating Loop

1. Define dimensions, style, palette, frames, transparency, naming.
2. Launch from /tmp, usually no repo --add-dir.
3. Generate image/procedural asset.
4. Save PNG/WebP and manifest.
5. Create contact sheet if multiple assets.
6. Copy finished assets to stable artifact dir only after verification.

## Required Artifacts

- PNG/WebP assets
- Manifest JSON with dimensions/frame data/palette/prompt/source notes
- Contact sheet for batches
- Post-processing script if used

## Quality Bar

- Output must be artifact-backed, not vibes.
- AGY should state assumptions and constraints explicitly.
- AGY should surface blockers instead of inventing missing facts.
- AGY should verify with external proof when the lane touches code, UI, APIs, or deployments.

## Pitfalls / Do Not Do

- PIL post-processing can hang after image is already saved; inspect brain output.
- Do not accept anti-aliased pixel art when crisp pixels were required.
- Do not leave assets only inside AGY brain scratch.

## Dispatch Skeleton

```text
Lane: agy-as-asset-generator
Goal: <outcome>
Context anchors: <files/URLs/repos>
Freedom: You may investigate beyond anchors where useful.
Constraints: <allowed files / no-code / branch / security rules>
Required artifacts: <exact files or outputs>
Quality bar: <tests, citations, screenshots, review verdict, etc.>
Do not do: <explicit anti-goals>
```
