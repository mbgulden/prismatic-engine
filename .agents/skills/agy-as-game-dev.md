---
name: agy-as-game-dev
description: "AGY as playable web-game developer: Phaser/Canvas loop, input, mobile controls, audio unlock, asset loading, deployment smoke tests, and playability-first verification."
tags: []
related_skills:
  - agy-as-coder
  - agy-as-debugger
  - agy-as-asset-generator
  - agy-tdd-discipline
---

# agy-as-game-dev

## Purpose

The game is not done until it is playable. This lane prioritizes player experience over architectural elegance.

## When to Use

Use for Darius Star and other browser games: game loops, controls, mobile UX, audio, assets, enemy/boss behavior, deploy smoke tests.

## Operating Loop

1. Launch locally.
2. Verify page loads with no console errors.
3. Verify keyboard and touch/mobile controls.
4. Verify all assets load with real sizes.
5. Verify audio user-gesture unlock and actual play calls.
6. Verify gameplay loop: start, move, shoot, damage, die/restart, win/advance.
7. Verify production URL.

## Required Artifacts

- Local server command
- Browser console output
- Screenshot/video or DOM proof
- Asset HTTP size/status check
- Audio play-path proof
- Mobile viewport proof
- Live URL smoke test

## Quality Bar

- Output must be artifact-backed, not vibes.
- AGY should state assumptions and constraints explicitly.
- AGY should surface blockers instead of inventing missing facts.
- AGY should verify with external proof when the lane touches code, UI, APIs, or deployments.

## Pitfalls / Do Not Do

- Site builds is not proof of playability.
- Desktop-only verification is incomplete.
- Do not dismiss audio as browser policy without proving the user-gesture path.
- Ship playability first; polish follows.

## Dispatch Skeleton

```text
Lane: agy-as-game-dev
Goal: <outcome>
Context anchors: <files/URLs/repos>
Freedom: You may investigate beyond anchors where useful.
Constraints: <allowed files / no-code / branch / security rules>
Required artifacts: <exact files or outputs>
Quality bar: <tests, citations, screenshots, review verdict, etc.>
Do not do: <explicit anti-goals>
```
