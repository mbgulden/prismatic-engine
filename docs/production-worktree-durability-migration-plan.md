# Production Worktree Durability Migration Plan

**Status:** planned, not implemented in this slice  
**Required marker:** `PRODUCTION_WORKTREE_DURABILITY_PLAN_OK`

`PRODUCTION_WORKTREE_DURABILITY_PLAN_OK`

---

## Current live source path

Live gateway service readback on July 15, 2026:

```text
service: prismatic-gateway.service
FragmentPath: /etc/systemd/system/prismatic-gateway.service
WorkingDirectory: /home/ubuntu/work/prismatic-engine
ExecStart: /home/ubuntu/.prismatic/venv_stable/bin/python3 -m prismatic.gateway.server --port 9000 --log-level info
```

## Risk

`/home/ubuntu/work/prismatic-engine` is the mutable multi-agent development checkout. It is actively used by Fred, AGY, Ned, Kai, Jules, and automation for branches, PR work, verifier runs, rebases, and temporary experiments.

That means the live gateway currently depends on a source path that can be changed by ordinary development activity. This violates the production durability invariant:

```text
live service source != mutable multi-agent development checkout
```

## Target durable source path

Recommended target:

```text
/home/ubuntu/.prismatic/runtime/prismatic-engine
```

Acceptable alternatives:

```text
/home/ubuntu/prod/prismatic-engine
/home/ubuntu/prismatic-production/prismatic-engine
```

The exact path is less important than the invariant that live service source is a dedicated production checkout or immutable release artifact and not the shared development checkout.

## Proposed migration

1. Create dedicated runtime directory:

```bash
mkdir -p /home/ubuntu/.prismatic/runtime
git clone https://github.com/mbgulden/prismatic-engine.git /home/ubuntu/.prismatic/runtime/prismatic-engine
```

2. Pin runtime checkout to the intended production branch/commit:

```bash
cd /home/ubuntu/.prismatic/runtime/prismatic-engine
git fetch origin main
git checkout main
git reset --hard <approved-production-commit-sha>
git clean -fdx
```

3. Verify runtime source:

```bash
git status --short --branch
git rev-parse HEAD
python3 -m py_compile prismatic/gateway/server.py scripts/verify_production_durability_standard.py
```

4. Update systemd `WorkingDirectory` only after a backup unit is written:

```bash
sudo cp /etc/systemd/system/prismatic-gateway.service /etc/systemd/system/prismatic-gateway.service.pre-production-worktree-migration.bak
sudo systemctl edit prismatic-gateway.service
```

Override target:

```ini
[Service]
WorkingDirectory=/home/ubuntu/.prismatic/runtime/prismatic-engine
Environment=PATH=/home/ubuntu/.local/bin:/home/ubuntu/.prismatic/runtime/prismatic-engine/.venv_dev/bin:/usr/local/bin:/usr/bin:/bin
```

5. Reload and restart intentionally:

```bash
sudo systemctl daemon-reload
sudo systemctl restart prismatic-gateway
systemctl show -p WorkingDirectory,ExecStart,ActiveState,ActiveEnterTimestamp prismatic-gateway
curl -sS http://127.0.0.1:9000/health
```

6. Run production durability verifier in enforce mode for the target route after the route fix exists:

```bash
python3 scripts/verify_production_durability_standard.py \
  --route /workspace-tree \
  --local-base http://127.0.0.1:9000 \
  --require-local \
  --enforce-route
```

7. Public/browser proof follows the standard gate.

## Rollback

```bash
sudo cp /etc/systemd/system/prismatic-gateway.service.pre-production-worktree-migration.bak /etc/systemd/system/prismatic-gateway.service
sudo systemctl daemon-reload
sudo systemctl restart prismatic-gateway
systemctl show -p WorkingDirectory,ActiveState prismatic-gateway
curl -sS http://127.0.0.1:9000/health
```

## Why not implemented in this slice

Changing `prismatic-gateway.service` moves the live control-plane runtime. That is a production operation requiring a narrow deploy window, rollback readiness, and route/browser proof after restart. This slice is scoped to the standard, verifier, review gate, and prompt/checklist integration. The risk is named here and should be implemented as a dedicated production migration task, not silently mixed into documentation work.

## Required follow-up

Dedicated Linear issue created:

```text
GRO-3942 — [Production Durability] Migrate prismatic-gateway to dedicated runtime checkout
https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-3942
```

Create a dedicated implementation PR for:

```text
Migrate prismatic-gateway.service from /home/ubuntu/work/prismatic-engine to a dedicated production checkout at /home/ubuntu/.prismatic/runtime/prismatic-engine.
```

Acceptance criteria:

- runtime checkout exists and is clean;
- systemd readback shows the dedicated runtime path;
- `/health` passes after restart;
- `/workspace-tree` enforce-mode verifier passes after route fix lands;
- public/authenticated proof and screenshot/browser proof are attached;
- rollback path is tested or dry-run documented.
