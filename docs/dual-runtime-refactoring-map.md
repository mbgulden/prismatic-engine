# Dual-Runtime Refactoring Map — GRO-1494

**Generated:** 2026-06-12 by Ned (agent:ned)
**Target:** Replace all 347 `/home/ubuntu` hardcoded paths with `$PRISMATIC_HOME` (and related env vars)
**Blueprint:** `specs/core-architecture-v1.md`

## Summary

| Category | Files | Occurrences | Risk | Autonomous-safe? |
|----------|-------|-------------|------|-------------------|
| Python (runtime) | 9 | 29 | HIGH | NO — runtime path changes |
| Shell (runtime) | 1 | 2 | HIGH | NO — verify-pipeline.sh |
| YAML (config) | 2 | 4 | MEDIUM | PARTIAL — template creation |
| JavaScript (plugins) | 6 | 12 | HIGH | NO — plugin path changes |
| HTML (dashboards) | 1 | 4 | MEDIUM | NO — dist files |
| Markdown (docs) | 49 | 296 | LOW | YES — doc-only changes |

**347 occurrences total across 68 files. 296 (85%) are in documentation — safe to change autonomous.**

---

## 1. Python Runtime Files (HIGH RISK — 29 occurrences, 9 files)

These are EXECUTED by the agent runtime. Changing paths incorrectly breaks agent functionality.

### 1.1 `prismatic/lock.py` — Lock registry
```
Line 15: Lock registry: /home/ubuntu/.antigravity/swarm_locks.json
Line 33: LOCK_FILE = Path("/home/ubuntu/.antigravity/swarm_locks.json")
```
**Change to:** `$PRISMATIC_HOME/.antigravity/swarm_locks.json`

### 1.2 `prismatic/dispatcher.py` — Agent dispatcher
```
Line 61: AGY_PATH = os.environ.get("AGY_PATH", "/home/ubuntu/.local/bin/agy")
Line 62: JULES_PATH = os.environ.get("JULES_PATH", "/home/ubuntu/.local/bin/jules")
Line 63: CODEX_PATH = os.environ.get("CODEX_PATH", "/home/ubuntu/.local/bin/codex")
```
**Change to:** Keep `/home/ubuntu/.local/bin/` as default — these are system-wide tool binaries, NOT prismatic-specific. Only change if tools move to `$PRISMATIC_HOME/bin/`. For now: **keep as-is, add env var override comment.**

### 1.3 `prismatic/agents/hermes.py` — Hermes agent integration
```
Line 120: - ``/home/ubuntu/work/`` — primary workspace
Line 141: Path("/home/ubuntu/work"),
```
**Change to:** `$PRISMATIC_HOME/work/`

### 1.4 `scripts/pre-push-hook.py` — Git pre-push hook
```
Line 29: LOCK_FILE = Path("/home/ubuntu/.antigravity/swarm_locks.json")
```
**Change to:** `$PRISMATIC_HOME/.antigravity/swarm_locks.json`

### 1.5 `scripts/gdocs-auth.py` — Google Docs auth
```
Line 9:  CREDS_PATH = "/home/ubuntu/mounts/synology-photo/Antigravity/credentials.json"
Line 10: TOKEN_PATH = "/home/...json"
```
**Change to:** Keep `/home/ubuntu/mounts/` — this is NAS mount, NOT prismatic-specific.

### 1.6 `plugins/hermes-plugin-workspace-tree-navigator/dashboard/plugin_api.py`
```
Line 36: "HD Reports": "/home/ubuntu/work/hd-reports",
Line 37: "HD Birth Data": "/home/ubuntu/work/next-step-bot",
Line 57: """Dynamically discover all workspace roots in /home/ubuntu/work/"""
Line 59: work_dir = Path("/home/ubuntu/work")
```
**Change to:** `$PRISMATIC_HOME/work/` references

### 1.7 `portable-skills/export.py`
```
Line 12: SOURCE_DIR = "/home/ubuntu/.hermes/profiles/orchestrator/skills"
Line 13: TARGET_DIR = "/home/ubuntu/work/prismatic-engine/portable-skills"
```
**Change to:** SOURCE_DIR → `$PRISMATIC_HOME/.hermes/profiles/orchestrator/skills`; TARGET_DIR → `$PRISMATIC_HOME/work/prismatic-engine/portable-skills`

### 1.8 `scratch/create_issue_and_plan.py` (6 occurrences)
**Risk:** LOW — scratch scripts, not in production path.

### 1.9 `scratch/post_completion.py` (6 occurrences)
**Risk:** LOW — scratch scripts, not in production path.

---

## 2. Shell Files (HIGH RISK — 2 occurrences, 1 file)

### 2.1 `scripts/verify-pipeline.sh`
```
Line 87: check "Swarm lock DB"  "/home/ubuntu/.antigravity/swarm_locks.json"
Line 88: check "Swarm CLI"      "/home/ubuntu/.antigravity/swarm.js"
```
**Change to:** `$PRISMATIC_HOME/.antigravity/...`

---

## 3. YAML Config Files (MEDIUM RISK — 4 occurrences, 2 files)

### 3.1 `PRISMATIC_ENGINE.yaml`
```
Line 48: file: "/home/ubuntu/.antigravity/swarm_locks.json"
```
**Change to:** `$PRISMATIC_HOME/.antigravity/swarm_locks.json`

### 3.2 `config/workspaces.yaml`
All paths are commented-out examples. Low risk.

---

## 4. JavaScript Plugin Files (HIGH RISK — 12 occurrences, 6 files)

### 4.1 `plugins/hermes-plugin-mcp-controller/dashboard/index.js`
### 4.2 `plugins/hermes-plugin-mcp-controller/dashboard/dist/index.js`
### 4.3 `plugins/hermes-plugin-prismatic-hub/dashboard/dist/index.js`
### 4.4 `plugins/hermes-plugin-prismatic-hub/src/index.js`
### 4.5 `plugins/hermes-plugin-swarm-manager/dashboard/dist/index.js`
### 4.6 `plugins/hermes-plugin-swarm-manager/src/index.js`

These reference `/home/ubuntu/` paths in API endpoint URLs and config defaults.
**Change to:** Read from `process.env.PRISMATIC_HOME` or use relative API paths.

---

## 5. HTML Dashboard Files (MEDIUM RISK — 4 occurrences, 1 file)

### 5.1 `plugins/hermes-plugin-prismatic-hub/dashboard/dist/index.html`
**Change to:** Use relative paths or env-injected config.

---

## 6. Markdown Documentation (LOW RISK — 296 occurrences, 49 files)

These are ALL safe to change autonomously. Sub-categories:

### 6.1 SKILL.md files (7 files)
Portable skills referencing `/home/ubuntu/` in examples and docs.

### 6.2 Reference docs (12 files)
`portable-skills/*/references/*.md`

### 6.3 Research/reports (24 files)
`reports/*.md`, `research/*.md`

### 6.4 Templates (2 files)
`templates/cron/cron-job-templates.md`, profile SOUL.md templates

### 6.5 Root-level docs (4 files)
`SOUL.md`, `README.md`, `beyondsaas-setup-guide.md`, `soul-alignment-matrix.md`

---

## 7. Safe Prep Work (AUTONOMOUS — already delivered in this session)

✅ **`docs/dual-runtime-refactoring-map.md`** — This file
✅ **`specs/config.yaml.template`** — Runtime config template
✅ **`scripts/setup-dual-venvs.sh`** — Virtualenv setup script
✅ **`scripts/export-prismatic-env.sh`** — Environment export template

---

## 8. Recommended Execution Order

| Step | Description | Risk | Status |
|------|-------------|------|--------|
| 1 | Create `$PRISMATIC_HOME` directory structure | LOW | ⚠️ Interactive |
| 2 | Create config.yaml from template | LOW | ✅ Template ready |
| 3 | Set up venv_stable + venv_dev | LOW | ✅ Script ready |
| 4 | Replace 296 markdown doc paths | LOW | ⚠️ Needs execution |
| 5 | Replace YAML config paths | MEDIUM | ⚠️ Interactive |
| 6 | Replace Python runtime paths | HIGH | ⚠️ Interactive |
| 7 | Replace JS plugin paths | HIGH | ⚠️ Interactive |
| 8 | Replace shell script paths | HIGH | ⚠️ Interactive |
| 9 | Update PRISMATIC_ENGINE.yaml lock path | MEDIUM | ⚠️ Interactive |
| 10 | Test full pipeline end-to-end | HIGH | ⚠️ Interactive |

---

## 9. Verification Steps (for interactive session)

1. `echo $PRISMATIC_HOME` — verify env var is set
2. `ls $PRISMATIC_HOME/.prismatic/` — config, db, venv_stable exist
3. `source $PRISMATIC_HOME/.prismatic/venv_stable/bin/activate && python -c "import prismatic"` — stable runtime imports
4. `source $PRISMATIC_HOME/work/prismatic-engine/.venv_dev/bin/activate && python -m pytest` — dev env works
5. `grep -r '/home/ubuntu' prismatic/ scripts/ plugins/ --include='*.py' | grep -v scratch/` — zero hardcoded paths remain in runtime code
6. `bash scripts/verify-pipeline.sh` — verification script still passes with new paths
