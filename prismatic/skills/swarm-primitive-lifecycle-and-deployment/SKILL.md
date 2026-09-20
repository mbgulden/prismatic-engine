---
name: swarm-primitive-lifecycle-and-deployment
description: Standard operating procedure for developing, testing, packaging, and synchronizing Swarm primitives (swarmlock, swarmcron, swarmrouter, swarmproof) between standalone GitHub repositories, Prismatic Engine, and live VM environments.
---

# Swarm Primitive Lifecycle & Deployment Protocol

## Purpose
Enforce a consistent, error-free lifecycle for all standalone Swarm primitives (`swarmlock`, `swarmcron`, `swarmrouter`, `swarmproof`). Every primitive must remain 100% portable with zero mandatory external dependencies, possess full test coverage, declare formal dependencies in `prismatic-engine`, and provide one-command update execution on server nodes.

---

## 🏛️ The 4-Tier Swarm Primitive Hierarchy

1. **Source of Truth**: `github.com/mbgulden/<primitive>@main`
2. **Local Development**: Editable mode (`pip install -e /path/to/primitive`) for instant zero-copy code reflection.
3. **Engine Consumption**: `prismatic-engine` declares formal dependencies in `pyproject.toml` under `dependencies` and `[project.optional-dependencies]`.
4. **Production Server Sync**: `/home/ubuntu/.prismatic/update-<primitive>.sh` (or `scripts/ops/update-all-primitives.sh`) pulls the latest `@main` branch into all virtual environments and cleanly reloads gateway services.

---

## 🛠️ Step-by-Step Primitive Release & Sync Workflow

### Step 1: Local Development & Adversarial Testing
When authoring or modifying a primitive in its standalone repository (e.g. `c:\Users\Michael Gulden\Github\<primitive>`):
1. Maintain zero mandatory external dependencies (standard library only for core functionality).
2. Author comprehensive unit and adversarial tests in `tests/`.
3. Run pytest and verify 100% pass:
   ```bash
   python3 -m pytest tests/ -v
   ```

---

### Step 2: Packaging & Clean Build Validation
1. Verify PEP 561 `py.typed` marker exists.
2. Build the distribution wheel and source tarball:
   ```bash
   python3 -m build
   ```
3. Verify zero deprecation warnings and zero dirty artifacts in git (`git ls-files | grep -E '\.pyc|__pycache__|dist/'`).

---

### Step 3: Git Commit & Remote Push
1. Commit changes to `main` with semantic commit messages.
2. Push to the GitHub repository:
   ```bash
   git push origin main
   ```

---

### Step 4: Prismatic Engine Dependency Linkage
1. In `prismatic-engine/pyproject.toml`, ensure the primitive is declared under `dependencies` and `[project.optional-dependencies]`:
   ```toml
   [project.optional-dependencies]
   primitives = [
       "swarmlock @ git+https://github.com/mbgulden/swarmlock.git@main",
       "swarmcron @ git+https://github.com/mbgulden/swarmcron.git@main",
       "swarmrouter @ git+https://github.com/mbgulden/swarmrouter.git@main",
   ]
   verification = [
       "swarmproof @ git+https://github.com/mbgulden/swarmproof.git@main",
   ]
   all = ["prismatic-engine[http,redis,gateway,primitives,verification]"]
   ```
2. Commit and push `prismatic-engine` to GitHub.

---

### Step 5: Server / VM Node Deployment
To propagate updates to the production server (e.g. Proxmox VM 800 or gateway hosts):

#### Single-Primitive Update:
```bash
# SwarmLock
ssh ubuntu@100.83.32.92 "bash /home/ubuntu/.prismatic/update-swarmlock.sh"

# SwarmCron
ssh ubuntu@100.83.32.92 "bash /home/ubuntu/.prismatic/update-swarmcron.sh"

# SwarmRouter
ssh ubuntu@100.83.32.92 "bash /home/ubuntu/.prismatic/update-swarmrouter.sh"

# SwarmProof
ssh ubuntu@100.83.32.92 "bash /home/ubuntu/.prismatic/update-swarmproof.sh"
```

#### All-Primitives Update (One Command):
```bash
ssh ubuntu@100.83.32.92 "bash /home/ubuntu/.prismatic/update-all-primitives.sh"
```

---

## 🔍 Verification Checklist for Agents

Before completing any task involving a Swarm primitive, the agent MUST verify:
- [ ] Primitive's test suite passes (100% green).
- [ ] Clean wheel package builds without warnings.
- [ ] Source repository is pushed to GitHub `origin/main`.
- [ ] `prismatic-engine` dependencies and OKF documents are updated.
- [ ] Server update scripts (`scripts/ops/update-*.sh`) are verified and committed.
