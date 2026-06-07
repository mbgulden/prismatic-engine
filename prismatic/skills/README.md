# Prismatic Engine — Bolt-On Skill Marketplace

Self-contained agent capability packages that plug into the Prismatic Engine. Each skill adds a specialized agent behavior — install what you need, skip what you don't.

## Available Skills

| Skill | Version | Category | Description |
|-------|---------|----------|-------------|
| `code-review` | 1.0.0 | review | Review PRs for security flaws, bugs, and performance issues |
| `docs-generator` | 1.0.0 | documentation | Auto-generate markdown docs from code structure and docstrings |
| `research-synthesizer` | 1.0.0 | research | Synthesize research from Drive, web, and local files |

## Quick Start

```bash
# List available skills
prismatic-engine skills list

# Install a skill (copies to ~/.prismatic/skills/)
prismatic-engine skills install code-review

# Show skill info
prismatic-engine skills info docs-generator

# Uninstall a skill
prismatic-engine skills uninstall research-synthesizer
```

## Skill Directory Structure

Each skill lives in its own subdirectory under `skills/`:

```
skills/<name>/
├── manifest.yaml      # Required. Metadata, version, dependencies, config defaults
├── README.md          # Recommended. Usage documentation
├── templates/         # Optional. Config files merged on install
│   ├── config.yaml
│   └── pipeline.yaml
└── hooks/             # Optional. Python scripts run at install/uninstall/run
    └── install.py
```

## Manifest Format

Every skill must have a `manifest.yaml` at its root:

```yaml
name: code-review
version: 1.0.0
description: Review pull requests for security flaws, bugs, and performance issues
author: Prismatic Engine
category: review
labels: [agent:codex]
config:
  default_rules:
    - security: Check for SQL injection, XSS, CSRF, hardcoded secrets
    - performance: Check for N+1 queries, missing indexes, memory leaks
    - style: Check for consistency with project conventions, naming, formatting
  max_comments: 10
  require_review: true
dependencies: []
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Unique skill identifier (lowercase, hyphens) |
| `version` | semver | yes | Current version |
| `description` | string | yes | One-line summary |
| `author` | string | yes | Creator name |
| `category` | string | yes | `review`, `documentation`, `research`, `monitoring`, `custom` |
| `labels` | list | no | Agent labels this skill applies to |
| `config` | dict | no | Default configuration values |
| `dependencies` | list | no | Required package names (pip) |

## Creating a Custom Skill

```bash
# Scaffold a new skill
prismatic-engine skills create my-custom-skill

# Edit the manifest
vim ~/.prismatic/skills/my-custom-skill/manifest.yaml

# Install it
prismatic-engine skills install my-custom-skill
```

See the [code-review](./code-review/), [docs-generator](./docs-generator/), and [research-synthesizer](./research-synthesizer/) directories for complete working examples.

## How Skills Work at Runtime

When a skill is installed:

1. The skill manifest is read and validated
2. Config templates are merged into the active agent configuration
3. If present, `hooks/install.py` runs (setup, dependency checks)
4. The skill appears in `prismatic-engine skills list --installed`

When a task arrives that matches the skill's label:
1. The router checks the skill's `labels` field
2. If matched, the skill's configuration is loaded
3. The agent executes using the skill's behavior profile
4. Pipeline hooks fire if `pipeline.yaml` is present
