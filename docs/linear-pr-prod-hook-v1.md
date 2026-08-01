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

## Operator Runbook & Emergency Rollback Procedure (§7.4)

### Instant Emergency Rollback (Atomic Symlink Revert)

If a deployment causes a runtime regression or fails post-deploy smoke checks:

1. **List Deployed Versions**:
   ```bash
   ls -la ~/.prismatic/versions/
   ```
2. **Repoint Release Symlink to Previous Version**:
   ```bash
   ln -sfn ~/.prismatic/versions/prismatic-engine-<PREVIOUS_SHA> ~/.prismatic/releases/prismatic-engine.tmp
   mv -Tf ~/.prismatic/releases/prismatic-engine.tmp ~/.prismatic/releases/prismatic-engine
   ```
3. **Verify Active Version**:
   ```bash
   readlink ~/.prismatic/releases/prismatic-engine
   ```

### Troubleshooting Receiver & HMAC Errors

- **401 Unauthorized / Invalid HMAC**:
  Ensure `DEPLOY_HMAC_SECRET` is set in both GitHub repository secrets and the daemon environment on port 9460.
- **Linear Issue Transition Failures**:
  Linear transitions are stored durably in `~/.prismatic/db/linear_transitions.json`. Queued items automatically retry on the next deployment cycle.

## API & CLI Reference

- Run Receiver Standalone: `python -m pe.deploy.receiver`
- History Endpoint: `GET /api/deploy/recent`
- Manual Trigger Endpoint: `POST /api/deploy/trigger?pr_sha=<SHA>&dry_run=true`
