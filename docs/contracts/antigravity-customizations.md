# Portable Antigravity Customizations Contract

**Status:** Canonical workspace-customization contract
**Owner:** Prismatic Engine orchestration maintainers
**Runtime contract:** [`canonical-agy-cli-workflow.md`](canonical-agy-cli-workflow.md)

## Purpose

Prismatic Engine ships a small, provider-native Antigravity customization bundle so a user who clones the repository—or installs the wheel and targets a workspace—gets the same Prismatic execution, context, evidence, and worktree discipline.

This bundle configures agent behavior. It does not authenticate Antigravity, select a mutable model, copy machine state, or weaken Prismatic admission and approval gates.

The bundle can be discovered by AGY on any supported AGY host, but Prismatic's canonical supervised runtime currently requires Linux (or Linux container/WSL), `/proc`, `prctl` descendant containment, and `tmux`. Unsupported hosts must report runtime execution as blocked rather than substituting raw or uncontained AGY launch.

## Discovery layout

Antigravity discovers project customizations under `.agents/` (also recognized by current AGY documentation as a workspace customization root). Prismatic ships:

```text
.agents/
├── rules/
│   └── prismatic-engine.md
├── skills.json
└── skills/
    ├── prismatic-agy-execution/
    │   ├── SKILL.md
    │   └── templates/
    │       ├── implementation-plan.md
    │       ├── result.md
    │       └── task.md
    ├── prismatic-context-discipline/SKILL.md
    ├── prismatic-engine-operations/SKILL.md
    ├── prismatic-evidence-and-review/SKILL.md
    └── prismatic-worktree-safety/SKILL.md
```

The checked-in copy benefits AGY users in the Prismatic repository. An exact duplicate is packaged under `prismatic/resources/antigravity/workspace/agents/` (mapped to destination `.agents/`) for non-editable wheel installs. A parity test prevents those copies from drifting.

## Front door

```bash
# Inspect the shipped bundle
prismatic agy customizations validate

# Audit existing AGY roots without returning secret/runtime contents
prismatic agy customizations audit \
  --config-root "$HOME/.antigravity" \
  --config-root "$HOME/.gemini"

# Preview installation into another workspace
prismatic agy customizations install --workspace /path/to/project --dry-run

# Install or adopt exact matching files
prismatic agy customizations install --workspace /path/to/project

# Inspect managed state
prismatic agy customizations status --workspace /path/to/project

# Remove only unchanged managed files
prismatic agy customizations uninstall --workspace /path/to/project
```

`pip install` performs no home/workspace mutation. `install.sh` validates that the packaged bundle is present and prints the explicit preview/install commands; it does not choose or overwrite a target workspace for the user. Source checkouts already contain the canonical `.agents/` files.

`--force` is available for install/uninstall conflicts, but it first writes every displaced regular file beneath an exclusive, collision-resistant `.agents/.prismatic-backups/<operation-id>/` directory. Backup ancestors are opened without following symlinks, backup files use exclusive creation, and an unsafe or pre-existing destination blocks the transaction.

## Managed-install guarantees

1. The packaged bundle validates before any workspace mutation.
2. Paths are relative, beneath `.agents/`, UTF-8, secret-free, and machine-path-free.
3. Existing symlink roots/components, non-directory ancestors, non-regular managed targets, and non-regular manifests are rejected before mutation.
4. Conflict detection and path preflight are whole-plan. Without `--force`, one conflict means no files are written or removed.
5. Matching files may be adopted idempotently; creating or repairing only the manifest is reported as a change.
6. The mutable manifest is inventory, not authorization. Install never trusts its digest to overwrite non-current bytes, and uninstall requires the complete manifest to match the currently shipped bundle before deleting anything.
7. Non-current files require explicit `--force`, even when an older mutable manifest claims them. They are backed up before replacement/removal.
8. Every write is staged before commit. Existing destinations are atomically captured and verified before replacement/removal; newly appeared destinations are never overwritten. Install and uninstall restore captured files and modes if a later commit step fails.
9. Backups use unique operation IDs and no-follow/exclusive creation; existing backup paths are never overwritten.
10. Drift is preserved by default. Force removal backs it up first.
11. User-created files and unrelated `.agents` assets are never included in the managed manifest.

## Explicit exclusions

The bundle and installer must not copy or manage:

- OAuth tokens, API keys, passwords, private keys, or credential files;
- `~/.gemini/antigravity-cli` conversations, brain/transcripts, logs, caches, protobuf state, updater state, bundled binaries, or MCP result caches;
- `.antigravity` active-work, deployment, swarm database/lock, or machine model-binding state;
- absolute user-home paths;
- raw wrapper scripts that bypass Prismatic admission;
- hooks, MCP servers, plugins, or global config by default;
- provider account/model IDs that may differ by user or change over time.

## Audit behavior

`audit` is structural and secret-safe:

- it reads only recognized portable candidates such as `SKILL.md`, rules, `skills.json`, `plugins.json`, `hooks.json`, and `mcp_config.json`;
- it counts but does not read generated/runtime paths;
- it counts but does not read sensitive filenames;
- it rejects a symlink supplied as the audit root and anchors traversal to a no-follow directory descriptor;
- it counts but never dereferences descendant symlinks or non-regular files;
- it classifies sensitive terms across every relative-path component before reading;
- it counts other non-portable files without returning content;
- it reports bounded structural indicators, optional digests for non-secret regular candidates, and hazard classes such as machine paths, raw AGY launches, unrestricted execution, and permission bypasses;
- it never returns raw YAML/frontmatter values, and it suppresses candidate digests when possible secret material is detected.

## Authority boundary

Workspace skills improve AGY behavior; they do not create authorization. Governed work still requires the canonical task specification, exact executable/task digests, admission receipt, durable launch receipt, result packet, and independent exact-artifact review. Merge, deploy, service restart, tracker mutation, public/financial sends, and concurrency changes remain separately gated.

## Distribution verification

Release proof must establish:

1. source `.agents` and packaged resources are byte-identical;
2. wheel and sdist contain all ten bundle files;
3. a non-editable wheel installed in a fresh venv can validate, dry-run, install, status, idempotently reinstall, preserve conflicts, and uninstall from an empty workspace;
4. import resolution comes from the fresh venv, not the source tree;
5. focused customization tests and existing canonical AGY workflow tests pass;
6. canonical full-suite and independent exact-head review remain separate acceptance gates.
