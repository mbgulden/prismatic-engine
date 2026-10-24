# 📐 Architectural Specification: Live Production Post-Merge Deploy Receiver (`pe/deploy/receiver.py`)

**Engine Version**: Prismatic Engine v0.3.3  
**Feature Name**: Live Production Post-Merge Deploy Receiver  
**Task Identifier**: [GRO-4364](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4364)  
**Status**: ARCHITECTURAL SPECIFICATION & IMPLEMENTATION PLAN  

---

## 1. System Architecture & Trigger Flow

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                    POST-MERGE PRODUCTION DEPLOY PIPELINE                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
  1. Commit Pushed to `origin/main`    │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  GitHub Actions Workflow (`.github/workflows/post-merge-deploy.yml`)        │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
  2. Constructs JSON Payload + Computes HMAC-SHA256 Signature (`DEPLOY_HMAC_SECRET`)
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Deploy Receiver Endpoint (`http://<receiver_host>:9460/deploy`)            │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
  3. Verifies `X-Hub-Signature-256`
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Atomic Deploy Runner (`pe/deploy/integrate.py`)                            │
│  - Fetches latest `origin/main` in repo                                      │
│  - Creates immutable release directory (`~/.prismatic/versions/prismatic-engine-<sha>`) │
│  - Atomically swaps release symlink (`~/.prismatic/releases/prismatic-engine`) │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Post-Deploy Health Checker (`pe/deploy/health.py`)                         │
│  - Probes Gateway Port 9000 (`/health`, `/api/skills`)                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Security & Anti-Pattern Defenses (§16.8)

1. **Strict HMAC Authentication**: Requests lacking a valid `X-Hub-Signature-256` matching `DEPLOY_HMAC_SECRET` are rejected with HTTP 401.
2. **Immutable Release Directories**: Source code is never mutated in-place; each deploy materializes an immutable release directory (`~/.prismatic/versions/prismatic-engine-<sha>`).
3. **Atomic Symlink Swapping**: Uses `os.replace` on temporary symlinks (`.tmp_symlink_<hash>`) to guarantee zero-downtime release transitions.
