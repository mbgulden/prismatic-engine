# Project Automation with tasks.json

**Added:** June 8, 2026 — from GRO-848 (Darius Star)
**Pattern class:** Any project with lint/build/deploy needs.

## Overview

A lightweight, zero-dependency project automation system using a `tasks.json` manifest and a `tasks/` directory of Python/Bash scripts. No npm, no Make, no Docker required. Works on any Linux host with Python 3 and bash.

## Structure

```
project/
├── tasks.json              # Manifest: task names → commands + metadata
├── tasks/
│   ├── lint.py             # Asset/ref validation
│   ├── build.py            # Production build + validation
│   └── deploy.sh           # Git push → CD trigger
└── dist/                   # Build output (in .gitignore)
```

## tasks.json Schema

```json
{
  "name": "Project Name",
  "version": "1.0.0",
  "tasks": {
    "lint": {
      "description": "Check for missing references/validation errors",
      "command": "python3 tasks/lint.py",
      "output": { "success": "...", "failure": "..." }
    },
    "build": {
      "description": "Validate constraints + generate production output",
      "command": "python3 tasks/build.py",
      "output": { "success": "...", "failure": "..." }
    },
    "deploy": {
      "description": "Push to trigger CD deploy",
      "command": "bash tasks/deploy.sh",
      "output": { "success": "...", "failure": "..." }
    }
  },
  "ci": {
    "pipeline": ["lint", "build", "deploy"],
    "on_push": ["lint", "build"]
  }
}
```

## Task Script Patterns

### lint.py — Reference Validation
- Scan source files (HTML, JS, JSON, YAML) for asset/file references (`src=`, `href=`, string paths)
- Verify each reference exists on disk
- Cross-validate manifest files (e.g., `sprites.json`) against filesystem
- Flag unused files as warnings (not errors) — they may be for future use
- Exit 0 on pass, 1 on blocking errors
- Optional `--fix` flag to auto-regenerate manifests

### build.py — Production Build + Constraint Validation
- Validate constraints (dimensions, file sizes, naming conventions)
- Warnings for non-blocking issues (e.g., non-power-of-two dimensions)
- Errors for blocking issues (missing critical files, corrupt assets)
- Generate minified/optimized output in `dist/`
- Report compression savings
- Copy static assets to `dist/`
- Exit 0 on pass (with warnings allowed), 1 on blocking errors

### deploy.sh — CD Trigger
- Stage all changes: `git add -A`
- Skip commit if nothing changed (`git diff --cached --quiet`)
- Push to trigger Cloudflare Pages (or other git-based CD)
- Print live URL

## Pitfalls

- **Don't commit `dist/`:** Build artifacts duplicate source assets. Add `dist/` to `.gitignore`. The build task regenerates it on demand.
- **Warnings ≠ errors:** Non-blocking validation issues (unused sprites, non-POT dimensions) should produce warnings but exit 0. Only missing references or corrupt files should fail.
- **Use `#!/usr/bin/env python3`** not `/usr/bin/python3` — venv-compatible.
- **Run from repo root:** Each script should `os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))` so it works regardless of cwd.

## Integration with Cloudflare Pages

For static sites on CF Pages with git-based deploy:
- `deploy.sh` does `git push origin main`
- CF Pages auto-deploys on push
- No build command needed in CF dashboard (static HTML)
- Framework Preset: None
- Output directory: root (`/`) for the game, or `dist/` for production build
