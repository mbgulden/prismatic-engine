# Prismatic Engine — Skills Index

Skills extracted from AGY research reports and operational patterns.
Each skill is a self-contained SKILL.md with Hermes frontmatter.

## Prismatic Core Skills

| Skill | Description | Source |
|---|---|---|
| [prismatic-7-step-loop](prismatic-7-step-loop/SKILL.md) | DECOMPOSE → DISPATCH → EXECUTE → REVIEW → FEEDBACK → REFINE → INTEGRATE | `reports/agy-7-step-loop-design.md` |
| [alchemy-quality-gates](alchemy-quality-gates/SKILL.md) | Structured intake, recipe-based chains, YAML quality gates, provenance logging | `reports/agy-alchemy-mode-design.md` |
| [prismatic-portability](prismatic-portability/SKILL.md) | Standalone Mode: SQLite task queue, subprocess/Docker signaling, offline operation | `reports/agy-portability-standalone-mode.md` |
| [lane-governance](lane-governance/SKILL.md) | Lane ownership, file locking, pre-push hooks, PRISMATIC_ENGINE.yaml | `PRISMATIC_ENGINE.yaml` + `reports/agy-implementation-plan.md` |
| [agent-soul-template](agent-soul-template/SKILL.md) | Agent SOUL.md schema, profile templates, bootstrap checklist | `SOUL.md` + `prismatic/templates/profiles/` |
| [prismatic-plugin-development](prismatic-plugin-development/SKILL.md) | Plugin anatomy, manifest format, widget patterns, build pipeline | `plugins/` ecosystem |
| [agy-research-metabolizer](agy-research-metabolizer/SKILL.md) | Multi-source research metabolizer: source mapping, evidence ledger, confidence labeling, report bundles | `docs/agy-research-metabolizer.md` |

## When to Load Each Skill

| Trigger | Load |
|---|---|
| Executing any Prismatic task | `prismatic-7-step-loop` |
| Content generation / client deliverables | `alchemy-quality-gates` |
| Offline/air-gapped deployment | `prismatic-portability` |
| Setting up a new repo or agent | `lane-governance` |
| Creating or updating an agent | `agent-soul-template` |
| Building a dashboard plugin | `prismatic-plugin-development` |
| Multi-source research or technical discovery | `agy-research-metabolizer` |

## Templates

- **Linear issue templates:** `templates/linear/issue-templates.md`
- **Cron job templates:** `templates/cron/cron-job-templates.md`

## Portable Skills

The `portable-skills/` directory contains skills designed for distribution
across Hermes profiles (not Prismatic Engine-specific):

| Skill | Description |
|---|---|
| golden-thread | Master project tracking framework |
| autonomous-execution-discipline | Never wait for user direction |
| orchestrator-delegation-discipline | Delegate multi-step work to worker lanes |
| systematic-debugging | 4-phase root cause debugging |
| static-site-seo-fix | Audit and fix SEO on static HTML sites |
| github-pr-workflow | GitHub PR lifecycle patterns |
| himalaya | Terminal email via Himalaya CLI |
