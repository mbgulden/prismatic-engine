# Hermes Discovery Artifact Search Pattern (GRO-830)

**Date:** Jun 8, 2026
**Nudge executor:** Fred (cron `c2cce4fec4ed`)
**Issue:** GRO-830 — Prismatic Engine: Hermes Discovery — Full Plugin & Service Audit

## The Problem

GRO-830 was a nudge trigger at `/tmp/prismatic/nudge-fred`. The issue asked for a complete Hermes plugin/service/profile audit. Step 0.5 required pre-verification of existing artifacts before starting fresh work.

All of the skill's Search A–F patterns restricted broad keyword searches to `~/work/research/`. The artifact was at `~/work/prismatic-engine/reports/agy-hermes-discovery-report.md` — a project subdirectory entirely outside that path.

## What Worked

A broader tree search across the full `~/work/` using **project-name keywords** caught it:

```python
# Search by project name (not restricted to ~/work/research/)
search_files(target='files', pattern='*prismatic*', path='~/work/')
# → Found the report among 19 results
```

The artifact matched `*prismatic*` (the project name) but NOT `GRO-830*`, `*plugin*audit*`, or `*hermes-inventory*`. Only `*hermes*discovery*` also matched (because the file contains "hermes-discovery" in its name), but `*prismatic*` was the more reliable catch-all.

## The Fix (added to Step 0.5 as Search G)

Search the full `~/work/` tree using the **project name** from the Linear issue's project field or trigger metadata workspace. The project name is the most reliable signal because:

1. Artifacts tend to be named by function (`hermes-discovery-report.md`) not by Linear issue number
2. They're stored in project subdirectories (`prismatic-engine/reports/`) not just `research/`
3. The project name from Linear is available in every trigger's metadata

## Verification Pattern

After finding the artifact:
1. Read it to assess completeness
2. Verify against live infrastructure (port listeners, running services, plugin dirs)
3. If match → document in Linear comment with artifact path, close issue
4. If mismatch → identify gaps and execute fresh work for missing sections only
