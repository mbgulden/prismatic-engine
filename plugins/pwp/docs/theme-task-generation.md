# PWP theme autopilot task generation

Phase 9 creates Linear issues for theme work before the build actually starts. The safe behavior is boring and strict:

1. Generate the issue with its parent, dependencies, priority, exact contracts, expected files, and verification commands.
2. Assign exactly one owner label from the PWP theme lane map.
3. Keep `dispatch:ready` off the issue until Michael or the orchestrator explicitly initiates the build process.

## Lane map

| Theme lane | Generated agent label | Default priority | Notes |
|---|---|---:|---|
| `content-strategist` | `agent:kai-content` | 3 | Page/module planning and content structure. |
| `design-theme` | `agent:agy` | 3 | Token choices, variants, and visual consistency. |
| `astro-implementation` | `agent:ned-code` | 2 | Plugin/theme code implementation. |
| `accessibility-verifier` | `agent:ned-code` | 2 | A11y fixtures and semantic checks. |
| `seo-schema` | `agent:kai-content` | 3 | Structured data, metadata, sitemap work. |
| `deploy` | `agent:ned-infra` | 2 | Cloudflare deploy, DNS, rollback metadata. |
| `reviewer` | `agent:ned-code` | 3 | Contract enforcement and verification review. |

The generator rejects any template that tries to supply a manual `agent:*` label. The lane is the owner-routing source of truth; otherwise the scanner gets a mixed signal and the robots start doing community theatre.

## Dispatch gate

`plugins.pwp.theme_task_generation.labels_for_theme_task()` only adds `dispatch:ready` when both conditions are true:

- `build_initiated=True`
- `dispatch_ready=True`

The default is `build_initiated=False`, so pre-created Phase 9 issues are visible in Linear for planning but invisible to dispatch scans.

## Verification contract

Every generated task must include at least one verification command. The generated description includes:

- lane and owner label,
- the no-`dispatch:ready` policy,
- parent issue,
- dependency identifiers,
- contract files,
- expected file lanes,
- verification commands.

This keeps the generated issue compatible with the PWP master plan requirement that agents cannot mark work done without concrete verifier output.
