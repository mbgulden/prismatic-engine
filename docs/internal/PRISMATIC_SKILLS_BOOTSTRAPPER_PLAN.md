# 📐 Architectural Specification: Built-In Skill Bootstrapper & Package Distribution

**Engine Version**: Prismatic Engine v0.3.2  
**Feature Name**: Native Skill Bootstrapper & Package Distribution (`prismatic skills sync`)  
**Task Identifier**: [GRO-4362](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4362)  
**Status**: ARCHITECTURAL SPECIFICATION & PRODUCTION DESIGN  

---

## 1. Executive Summary & Design Rationale

Currently, governance rules (`.agents/AGENTS.md`) and executable skills (`.agents/skills/`) must be manually copied between local workspace repositories (`Hermes`, `prismatic-engine`) and global configuration paths (`~/.gemini/config`). This introduces manual synchronization overhead and risks skill drift across developer machines.

The **Built-In Skill Bootstrapper & Package Distribution Engine** (`prismatic skills sync`) bundles governance rules and skills directly inside `prismatic-engine` package data (`pyproject.toml`). Running `prismatic skills sync` automatically bootstraps and verifies Dual-Tree skill integrity across any target workspace repository.

---

## 2. Skill Bootstrapper Lifecycle

```text
  Prismatic Engine Package Data (pyproject.toml / importlib.resources)
  │
  ├─▶ `.agents/AGENTS.md` (System Governance Rules)
  ├─▶ `.agents/skills/subagent-claim-verification-gate/`
  └─▶ `.agents/skills/antigravity-prismatic-pr-evidence/`
  │
  ▼ `prismatic skills sync [--target <workspace>]`
  │
  ├─▶ 1. Materialize `.agents/AGENTS.md` in Target Workspace
  ├─▶ 2. Materialize `.agents/skills/` in Target Workspace
  ├─▶ 3. Materialize `prismatic/skills/` (Dual-Tree Mirror)
  └─▶ 4. Compute SHA-256 Digest Tree & Assert Hash Integrity Match
  │
  ▼
  PE_SKILLS_SYNC_VERIFIED_OK
```

---

## 3. CLI Command Interface & Schema

```bash
# Standard CLI invocation
prismatic skills sync

# Target specific workspace directory with JSON payload
prismatic skills sync --target ../my-new-service --json
```

### Machine-Readable Output Schema (`prismatic skills sync --json`)
```json
{
  "marker": "PE_SKILLS_SYNC_VERIFIED_OK",
  "status": "PASS",
  "target_workspace": "C:\\Users\\Michael Gulden\\Github\\my-new-service",
  "agents_rules_synced": true,
  "skills_synced_count": 2,
  "dual_tree_matched": true,
  "skill_tree_sha256": "4E3A9C780B042C9D1745E67A20E15BB470A1028BF0A6E1E45C1083921E90F4A1"
}
```

---

## 4. Subsystem Components

1. **`prismatic/skills/bootstrapper.py`**: Implementation of `SkillBootstrapper`.
2. **`prismatic/cli/__init__.py`**: Enhanced `skills` subcommand parser.
3. **`tests/test_bootstrapper.py`**: Unit test suite.
