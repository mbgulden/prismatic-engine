---
title: Linear PR Prod Deploy Hook Specification
version: v1
workstream: WB-9
status: accepted
last_verified: 2026-08-01
---

# Linear → PR → Prod Deploy Hook Specification V1

This specification documents the post-merge production deployment pipeline and Linear issue transition hook (`pe/deploy/`).

## Architecture & Trigger Flow (§7)

1. **Trigger**: Post-merge `push` to `main` triggers GitHub Action `.github/workflows/post-merge-deploy.yml`.
2. **HMAC Signature**: Payload is signed via HMAC-SHA256 (`DEPLOY_HMAC_SECRET`).
3. **Local Receiver**: `pe/deploy/receiver.py` receives request on port `9460` and validates signature.
4. **Atomic Deploy**: `pe/deploy/integrate.py` executes git pull, copies files to immutable versioned directory `/versions/prismatic-engine-<sha>/`, and updates `/releases/prismatic-engine` symlink atomically via temporary link replace.
5. **Post-Deploy Health Check**: `pe/deploy/health.py` runs smoke checks.
6. **Linear Issue Transition**: `pe/deploy/linear_transition.py` extracts `GRO-XXXX` identifiers and moves them to `Done` (batched $\le 10/\text{min}$).
7. **Manifest Record**: `pe/deploy/manifest.py` records `DeployRecord` in `~/.prismatic/db/deploy_records.json`.

## Invariants & Guardrails (§16.8)

- **No In-Place Mutation**: Always rsync fresh copies to a new versioned release directory.
- **Atomic Symlink Swap**: Never write directly to release directory without atomic symlink indirection.
- **HMAC Verification**: Signature is validated on every request.
- **Health Gate**: Linear issue transitions ONLY fire after successful post-deploy health check.
- **Rate Limit Control**: Linear transitions are batched at max 10/minute.
- **Dry-Run Mode**: Manual deploys support `--dry-run` flag.
