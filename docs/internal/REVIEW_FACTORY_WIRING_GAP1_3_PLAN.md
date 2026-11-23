# 📐 Architectural Specification: Review Factory Wiring Gaps 1 & 3 (Auto-Ingestion & Verification Worker Daemon)

**Engine Version**: Prismatic Engine v0.3.3  
**Feature Name**: Review Factory Task Admission Ingestion & Background Verification Daemon  
**Task Series Identifiers**:
- [GRO-4368](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4368): Task Admission Consumer Closeout Auto-Ingestion
- [GRO-4369](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4369): Background Verification Worker Daemon  
**Status**: ARCHITECTURAL SPECIFICATION & PRODUCTION IMPLEMENTATION PLAN  

---

## 1. System Flow Specification

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                 TASK CLOSEOUT & VERIFICATION DAEMON FLOW                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
  1. Agent Task Closeout Event (`task_admission_consumer.py`)
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Backlog Importer Auto-Ingestion (`BacklogImporter.ingest_result_packet`)   │
│  - Parses `result-packet.json`                                              │
│  - Enqueues `ReviewJob` into SQLite DB (`merge_factory.sqlite3`)            │
│  - State: `QUEUED`                                                           │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Background Verification Daemon (`verification_daemon.py`)                 │
│  - Polls `QUEUED` jobs                                                      │
│  - Executes clean-room TDD & Playwright verification                        │
│  - Emits `VerificationReceipt` & transitions job to `REVIEW_READY`          │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Component Design

1. **`prismatic/task_admission_consumer.py`**:
   - Updates closeout handler to invoke `BacklogImporter().ingest_result_packet(...)`.
2. **`prismatic/gateway/verification_daemon.py`**:
   - Background thread daemon polling Review Factory DB for `QUEUED` jobs.
3. **`prismatic/gateway/server.py`**:
   - Starts `VerificationWorkerDaemon` on Gateway server startup.
