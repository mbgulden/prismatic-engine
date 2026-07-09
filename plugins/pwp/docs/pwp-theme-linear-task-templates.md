# PWP theme Linear task templates

These templates support Phase 9 of the PWP AI-first Astro theme system: routing theme work to specialized agents without prematurely dispatching the build.

## Build-start policy

- Generated issues start in `Todo`.
- Do **not** add `dispatch:ready` until Michael explicitly starts the build process.
- Every implementation issue must include docs updates and concrete verification evidence before Done.
- Templates inherit the PWP theme contracts from the master plan:
  - theme manifest: `plugins/pwp/schemas/pwp-theme.schema.json`
  - module contract: `plugins/pwp/schemas/pwp-module.schema.json`
  - token contract: `plugins/pwp/schemas/pwp-token.schema.json`
  - EmDash map: `plugins/pwp/schemas/pwp-emdash-map.schema.json`

## Template artifact

The machine-readable template set lives at:

```text
plugins/pwp/linear_task_templates/pwp-theme-lane-templates.json
```

It defines seven lanes:

| Lane | Default agent label | Primary responsibility |
|---|---|---|
| content strategist | `agent:kai-content` | route/module plan, content safety, EmDash field plan |
| design/theme | `agent:fred` | theme family, tokens, variants, visual consistency |
| Astro implementation | `agent:agy` | Astro layouts/components, fixtures, build wiring |
| accessibility verifier | `agent:qa` | WCAG/axe/semantic/keyboard verification |
| SEO/schema | `agent:kai-seo` | metadata, sitemap/link rules, JSON-LD |
| deploy | `agent:ned-infra` | Cloudflare deploy, provenance, rollback/idempotency |
| reviewer | `agent:reviewer` | contract drift review and merge/block checklist |

## Required issue shape

Each generated task must include:

1. exact input artifacts,
2. exact files/contracts to use,
3. expected outputs,
4. verification commands or artifact requirements,
5. a completion checklist,
6. blocked paths (`content/`, `assets/`, `designs/`, `research/`, `active-oahu/`),
7. the no-`dispatch:ready` guard until Michael starts the build.

This keeps Phase 9 useful instead of creating a swarm-shaped confetti cannon. We have enough of those.
