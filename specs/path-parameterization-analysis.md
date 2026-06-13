## REPORT: Path Parameterization Analysis — Sat Jun 13 03:52:45 UTC 2026

We have analyzed the codebase to identify hardcoded `/home/ubuntu` paths and categorized them based on their runtime role, risk levels, and portability requirements as outlined in Ned's [dual-runtime-refactoring-map.md](file:///home/ubuntu/work/prismatic-engine/docs/dual-runtime-refactoring-map.md).

---

### 1. Scope Summary

#### Total Files Affected by Category
- **Python (runtime)**: 9 files, 29 occurrences
- **JavaScript (plugins)**: 6 files, 12 occurrences
- **Shell (runtime)**: 1 file, 2 occurrences
- **YAML (config)**: 2 files, 4 occurrences
- **HTML (dashboards)**: 1 file, 4 occurrences
- **Markdown (docs)**: 49 files, 296 occurrences
- **Total**: 68 files, 347 occurrences

#### High-Risk vs. Low-Risk Classification
- **HIGH RISK (Runtime Execution)**:
  - [lock.py](file:///home/ubuntu/work/prismatic-engine/prismatic/lock.py) (Lock registry path affects engine concurrency lockouts).
  - [dispatcher.py](file:///home/ubuntu/work/prismatic-engine/prismatic/dispatcher.py) (Agent dispatcher tool binaries path).
  - [hermes.py](file:///home/ubuntu/work/prismatic-engine/prismatic/agents/hermes.py) (Hermes workspace directory resolution).
  - [pre-push-hook.py](file:///home/ubuntu/work/prismatic-engine/scripts/pre-push-hook.py) (Lock verification before pushing).
  - [plugin_api.py](file:///home/ubuntu/work/prismatic-engine/plugins/hermes-plugin-workspace-tree-navigator/dashboard/plugin_api.py) (Dashboard plugin workspace scanning).
  - [verify-pipeline.sh](file:///home/ubuntu/work/prismatic-engine/scripts/verify-pipeline.sh) (CI/CD build execution and verification).
  - JavaScript src & dist files in `plugins/` (affects dynamic API endpoints & plugins backend routing).
- **MEDIUM RISK (Config / Assets)**:
  - [PRISMATIC_ENGINE.yaml](file:///home/ubuntu/work/prismatic-engine/PRISMATIC_ENGINE.yaml) (Workspace governance configuration).
  - [workspaces.yaml](file:///home/ubuntu/work/prismatic-engine/config/workspaces.yaml) (Workspace registry examples).
  - [index.html](file:///home/ubuntu/work/prismatic-engine/plugins/hermes-plugin-prismatic-hub/dashboard/dist/index.html) (Frontend dashboard files).
- **LOW RISK (Documentation / Scratch)**:
  - 49 Markdown (`.md`) documentation files including [SOUL.md](file:///home/ubuntu/work/prismatic-engine/SOUL.md).
  - Scratch scripts: `scratch/create_issue_and_plan.py` and `scratch/post_completion.py`.

#### Paths That Should NOT Be Changed
- **System Tool Binaries**: `/home/ubuntu/.local/bin/agy`, `/home/ubuntu/.local/bin/jules`, `/home/ubuntu/.local/bin/codex` in [dispatcher.py](file:///home/ubuntu/work/prismatic-engine/prismatic/dispatcher.py).
  - *Reason*: These are global CLI tool binaries managed by the host environment, not by the Prismatic Engine. They should remain in user-local directories, with support for environment overrides (`AGY_PATH`, `JULES_PATH`, `CODEX_PATH`).
- **NAS Mounts**: `/home/ubuntu/mounts/synology-photo/Antigravity/credentials.json` in [gdocs-auth.py](file:///home/ubuntu/work/prismatic-engine/scripts/gdocs-auth.py).
  - *Reason*: This is a host-specific hardware network-attached storage mount point which is independent of the portable Prismatic home path.

---

### 2. Replacement Strategy

For portable path parameterization, we will resolve `/home/ubuntu` dynamically via environment variables or fallback values.

| Target Path Category | Original Hardcoded Path | Replacement Target | Justification / Context |
| :--- | :--- | :--- | :--- |
| **Runtime Work Paths** | `/home/ubuntu/work/` | `$PRISMATIC_HOME/work/` | Standardizes all workspaces relative to the engine home directory. |
| **Lock Files** | `/home/ubuntu/.antigravity/` | `$PRISMATIC_HOME/.antigravity/` | Centralizes lockfiles for coordinate-based concurrency. |
| **Skills** | `/home/ubuntu/.hermes/profiles/` | `$PRISMATIC_HOME/.hermes/profiles/` | Keeps skill definitions and profiles portable across environments. |
| **Config** | `/home/ubuntu/.prismatic/` | `$PRISMATIC_HOME/.prismatic/` | Points engine configurations and database files to the user home layout. |

#### Specific Strategies for Non-Shell Languages
- **Python**: Use `os.environ.get("PRISMATIC_HOME", "/home/ubuntu")` or python's `pathlib.Path` resolving from environmental defaults.
- **JavaScript (Node.js)**: Use `process.env.PRISMATIC_HOME` for plugin server-side logic; use relative API calls for client-side files to decouple directories from URL layouts.
- **YAML**: Replace hardcoded values with template placeholders (e.g. `{{PRISMATIC_HOME}}` or env-parsed parameters).

---

### 3. Execution Plan

#### Order of Operations
1. **Directory Setup**: Create `$PRISMATIC_HOME` structure (`.prismatic`, `.antigravity`, `work`, `.hermes`) on the target server.
2. **Markdown Documentation (Low Risk)**: Replace documentation references using batch tools (e.g. `sed`).
3. **YAML & Configuration (Medium Risk)**: Update commented examples in [workspaces.yaml](file:///home/ubuntu/work/prismatic-engine/config/workspaces.yaml) and update [PRISMATIC_ENGINE.yaml](file:///home/ubuntu/work/prismatic-engine/PRISMATIC_ENGINE.yaml).
4. **Python Runtime (High Risk)**: Refactor [lock.py](file:///home/ubuntu/work/prismatic-engine/prismatic/lock.py) and [hermes.py](file:///home/ubuntu/work/prismatic-engine/prismatic/agents/hermes.py) to read from environmental variables.
5. **Shell Scripts (High Risk)**: Parametrise [verify-pipeline.sh](file:///home/ubuntu/work/prismatic-engine/scripts/verify-pipeline.sh) to consume `$PRISMATIC_HOME`.
6. **JavaScript Plugins (High Risk)**: Update backend API endpoint resolution in plugin dashboards.
7. **End-to-End Test**: Execute verification commands and sanity tests.

#### Verification Commands per Category
- **Python**: 
  ```bash
  source $PRISMATIC_HOME/.prismatic/venv_stable/bin/activate && python -c "import prismatic; print(prismatic.__file__)"
  pytest prismatic/tests/
  ```
- **Shell**: 
  ```bash
  PRISMATIC_HOME=/path/to/home bash scripts/verify-pipeline.sh
  ```
- **YAML**: 
  ```bash
  python -c "import yaml; yaml.safe_load(open('PRISMATIC_ENGINE.yaml'))"
  ```
- **JavaScript**: 
  ```bash
  grep -r "/home/ubuntu" plugins/*/dashboard/dist/ --include="*.html" || echo "HTML clean"
  ```

#### Rollback Strategy
- **Git State Rollback**: Maintain a clean Git stage prior to replacement. Discard all changes with:
  ```bash
  git reset --hard HEAD
  ```
- **Config Backup**: Keep backup snapshots of critical files:
  ```bash
  cp PRISMATIC_ENGINE.yaml PRISMATIC_ENGINE.yaml.bak
  ```

---

### 4. Risk Assessment

- **Consequences of Misconfiguration**:
  - **Lock Failure**: If [lock.py](file:///home/ubuntu/work/prismatic-engine/prismatic/lock.py) is unable to resolve or write to `$PRISMATIC_HOME/.antigravity/swarm_locks.json`, agents will fail to acquire file locks, creating risk for concurrent file corruption and race conditions.
  - **Agent Dispatch Failure**: If workspace paths are parsed incorrectly in [hermes.py](file:///home/ubuntu/work/prismatic-engine/prismatic/agents/hermes.py), spawned agents will fail to boot or read files inside workspaces, crashing the dual-runtime.
- **Must Be Verified Interactively**:
  - [lock.py](file:///home/ubuntu/work/prismatic-engine/prismatic/lock.py): Requires live concurrency testing to ensure file locks are successfully registered under `$PRISMATIC_HOME`.
  - [verify-pipeline.sh](file:///home/ubuntu/work/prismatic-engine/scripts/verify-pipeline.sh): Pipeline execution behavior must be monitored live.
  - JavaScript dashboard `dist/` files: Plugins require browser-based visual validation to ensure dashboard graphs resolve correctly.
- **Safe for Batch Replacement**:
  - All Markdown (`.md`) files (total 296 occurrences).
  - Non-functional comments and templates.
