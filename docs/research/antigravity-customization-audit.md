# Antigravity/AGY Customization Audit

**Audit scope:** live operator roots, installed AGY 1.1.6 behavior, Prismatic `origin/main`, merged canonical AGY workflow, and portable customization candidates.
**Audit method:** secret-safe structural inventory; private runtime contents were neither read nor copied.

## Executive finding

The live machine has valuable AGY procedures but no product-grade portable customization contract. Three overlapping sources existed:

1. `~/.antigravity/skills` — 24 curated AGY skills plus mutable model/swarm/runtime configuration;
2. `~/.gemini/config/skills` — legacy skills with duplicate flat Markdown projections and project registry state;
3. `AGY_SKILLS_DIR` — pointed at one Hermes orchestrator profile, coupling AGY behavior to a machine-local agent profile.

Prismatic already had portable Hermes skills and, through merged PR #396, a canonical AGY runtime launcher/containment workflow. It did not have a managed Antigravity workspace bundle or installed-wheel customization command.

## Live proof

```text
AGY_VERSION=1.1.6
AGY_COMMAND=agy
SEPARATE_ANTIGRAVITY_COMMAND=absent
AGY_CONFIG_DIR=~/.antigravity
AGY_SKILLS_DIR=<machine-local Hermes orchestrator skills>
AGY_IMPORTED_PLUGINS=0
CANONICAL_RUNTIME_PR=https://github.com/mbgulden/prismatic-engine/pull/396 MERGED
CURRENT_BASE=fdd93051df202a080a725bec5e77e12b67ff2cc6
```

Full-root structural audit:

| Root | Portable candidates | Generated/runtime files excluded | Sensitive paths excluded | Other non-portable files | Hazards |
|---|---:|---:|---:|---:|---|
| `~/.antigravity` | 24 | 5 | 0 | 3 | 15 machine paths; 2 permission-bypass references |
| `~/.gemini` | 31 | 11,449 | 2 | 49 | 7 machine paths; 7 raw AGY launches; 2 permission-bypass references; 1 unrestricted-execution reference |

```text
AUDIT_LOG=/tmp/george-agy-full-root-audit.json
AUDIT_SHA256=bd2b9a4b47ce8b731a668959bc6ae2dad57e9554e6930197c7fc689f000c514b
```

The audit returned metadata, relative candidate paths, hashes, and hazard classes. It did not return OAuth/token contents, transcripts, messages, conversations, logs, MCP data, caches, or model state.

## What was reusable

Reusable ideas—not raw files—include:

- bounded task context and plan-first execution;
- exact AGY model/binary/task awareness;
- long-running task continuity;
- credential hygiene;
- GitHub/Linear authority boundaries;
- systematic debugging and test-first work;
- subagent communication;
- Prismatic evidence and completion packets;
- worktree preservation.

These were consolidated into five concise Prismatic-specific skills rather than exporting 24–55 overlapping skills.

## What was rejected from the portable core

- hardcoded `/home/ubuntu` and tool paths;
- mutable model bindings and account-specific model IDs;
- raw `agy`/`agy-bin` execution recipes for governed tasks;
- generic `dangerously-skip-permissions` guidance outside exact admission;
- unrestricted execution and scanner-bypass procedures;
- detached `tmux` lifecycle instructions outside canonical Prismatic containment;
- shell patterns that print possible secrets into logs;
- global token chmod/config mutation;
- AOT, email, SEO, Cloudflare, Human Design, or persona-specific skills;
- hooks, plugins, and MCP configuration without an explicit optional package and credential contract;
- OAuth token, brain, conversation, transcript, cache, implicit protobuf, binary, updater, MCP-result, project-registry, swarm, and deployment state.

## Wrapper finding

The local `agy` wrapper is an operator artifact, not a distributable contract. It:

- hardcodes a user-home binary path;
- uses a fallback signing secret when an environment secret is absent;
- writes `STARTED.md` markers as a side effect;
- rewrites legacy flags;
- accepts unsigned ordinary execution outside the signed-injection path.

Prismatic does not package this wrapper. The merged `prismatic agy` render/admission/launch/wait implementation is the canonical runtime boundary.

## Standardized result

Prismatic now ships:

- repository-native `.agents/rules` and five progressive skills;
- identical installed-package resources;
- managed `audit`, `validate`, `install`, `status`, and `uninstall` commands;
- dry-run, atomic writes, whole-plan conflict blocking, backup-before-force, managed digest upgrades, and drift-preserving uninstall;
- source/package parity and clean-room distribution tests.

See [`../contracts/antigravity-customizations.md`](../contracts/antigravity-customizations.md).

## Non-claims

This audit does not claim that every legacy skill is worthless, that live AGY account authentication is portable, that optional plugins/hooks/MCP servers are validated for general distribution, or that installing workspace rules authorizes external actions. Those remain separate opt-in integration slices.
