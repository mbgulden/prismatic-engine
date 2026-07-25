# Antigravity/AGY Customization Audit

**Audit scope:** live operator roots, AGY 1.1.6→1.1.7 behavior observed during the slice, Prismatic `origin/main`, merged canonical AGY workflow, and portable customization candidates.
**Audit method:** secret-safe structural inventory; private runtime contents were neither read nor copied.

## Executive finding

The live machine has valuable AGY procedures but no product-grade portable customization contract. Three overlapping sources existed:

1. `~/.antigravity/skills` — 24 curated AGY skills plus mutable model/swarm/runtime configuration;
2. `~/.gemini/config/skills` — legacy skills with duplicate flat Markdown projections and project registry state;
3. `AGY_SKILLS_DIR` — pointed at one Hermes orchestrator profile, coupling local operational scripts to a machine-local agent profile.

The built-in AGY documentation identifies `~/.gemini/config` as the native global customization root and `.agents/`, `.agent/`, `_agents/`, or `_agent/` as workspace roots. It does **not** establish native discovery semantics for this machine's `AGY_CONFIG_DIR` or `AGY_SKILLS_DIR`; those are treated here as local integration conventions, not portable AGY guarantees.

Prismatic already had portable Hermes skills and, through merged PR #396, a canonical AGY runtime launcher/containment workflow. It did not have a managed Antigravity workspace bundle or installed-wheel customization command.

## Live proof

```text
AGY_VERSION=1.1.7 (1.1.6 was observed earlier in the same audit slice)
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

The original audit returned relative candidate paths, hashes, hazard classes, and selected raw frontmatter metadata while excluding OAuth/token contents, transcripts, messages, conversations, logs, MCP data, caches, and model state. Independent security review later showed that returning arbitrary frontmatter values and following candidate symlinks was not secret-safe. The shipped audit now skips symlinks/non-regular files and sensitive path components, returns only bounded field-presence/name-match indicators, and suppresses hashes for candidates containing possible secret material.

No live custom AGY `agents/`, `workflows/`, `rules/`, plugins, hooks, `skills.json`, or `plugins.json` existed in either primary customization root. The reusable global corpus was skill-only: 27 directory-form `skills/<name>/SKILL.md` entries with valid required frontmatter. Twenty-six legacy flat `skills/*.md` projections duplicated those skills; 25 pairs were byte-identical, while the flat `prismatic-engine-operations.md` diverged from its directory copy. Two legacy directory names used underscores while frontmatter used hyphens. Flat projections, naming mismatches, and divergent duplicates are not distributed.

Generic architecture/coding/review/debug/TDD skills were classified as possible future optional packs. The default Prismatic bundle deliberately synthesizes only the five governance-critical skills needed to operate the Engine safely; it does not silently promote the entire machine-local corpus.

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

- repository-native `.agents/rules`, five progressive skills, and task/plan/result workflow templates;
- identical installed-package resources;
- managed `audit`, `validate`, `install`, `status`, and `uninstall` commands;
- dry-run, atomic writes, whole-plan conflict blocking, backup-before-force, managed digest upgrades, and drift-preserving uninstall;
- source/package parity and clean-room distribution tests.

See [`../contracts/antigravity-customizations.md`](../contracts/antigravity-customizations.md).

## Non-claims

This audit does not claim that every legacy skill is worthless, that live AGY account authentication is portable, that optional plugins/hooks/MCP servers are validated for general distribution, or that installing workspace rules authorizes external actions. Those remain separate opt-in integration slices.
