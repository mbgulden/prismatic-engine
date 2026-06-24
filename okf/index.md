---
type: Index
title: Prismatic Engine
description: Portable multi-agent orchestration engine.
resource: https://github.com/mbgulden/prismatic-engine
tags: [index, project, okf-spoke]
timestamp: 2026-06-24T09:12:00Z
linear_issue: GRO-2234
git_repo: mbgulden/prismatic-engine
git_path: okf/index.md
last_verified: 2026-06-24
verified_by: agy
status: current
---

# Prismatic Engine

> Portable multi-agent orchestration engine.

- **Repo:** [mbgulden/prismatic-engine](https://github.com/mbgulden/prismatic-engine)
- **Linear project:** [Prismatic Engine](https://linear.app/growthwebdev/project/2eb2913f-740c-4142-b844-59feec230a9d)

## Status & Fixes

### GRO-2234: [INFRA] Fix prismatic-engine linear_project_id

On 2026-06-24, we resolved a configuration issue where the `linear_project_id` for `prismatic-engine` was aliased to the `agentic-swarm-ops` project.
- **Problem:** Issues created from `prismatic-engine` profile landed in the incorrect project board (`agentic-swarm-ops`).
- **Fix:** Corrected the ID mapping in `project-registry.json` (the single source of truth for project configurations) from `165b3d41-8915-49b7-8985-9f1817e8c6ec` to the active `Prismatic Engine` project ID `2eb2913f-740c-4142-b844-59feec230a9d`.
- **Verification:** Verified by programmatically filing a test issue (`GRO-2378`) under `Prismatic Engine` and then canceling it.
