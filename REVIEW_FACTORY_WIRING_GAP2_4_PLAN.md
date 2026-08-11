# 📐 Architectural Specification: Review Factory Wiring Gaps 2 & 4 (Auto-Repair Loop & Live Dashboard Attestation Controls)

**Engine Version**: Prismatic Engine v0.3.3  
**Feature Name**: Review Factory Automated Repair Loop & Live Dashboard Attestation Controls  
**Task Series Identifiers**:
- [GRO-4370](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4370): Automated Self-Healing Repair Loop Wiring
- [GRO-4371](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4371): Review Factory Dashboard Surface & Attestation Controls  
**Status**: ARCHITECTURAL SPECIFICATION & PRODUCTION IMPLEMENTATION PLAN  

---

## 1. System Flow Specification

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                 SELF-HEALING REPAIR LOOP & DASHBOARD FLOW                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
  1. Verification or Audit Failure (`ReviewQueue.mark_repair_required`)
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Automated Repair Dispatcher (`prismatic/review_factory/queue.py`)          │
│  - Generates `RepairPacket` with failure log & traceback                    │
│  - Posts high-priority repair admission event to `task_admission.py`         │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Dashboard Live Review Factory Tab (`https://prismatic.growthwebdev.com`)   │
│  - Fetches `/api/review-factory/queue` & `/api/review-factory/status`      │
│  - Renders live candidate cards, risk tiers, & verification logs            │
│  - Enables **Approve & Merge** and **Reject / Repair** controls              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Component Design

1. **`prismatic/review_factory/queue.py`**:
   - In `mark_repair_required()`: Submit high-priority repair task to `task_admission.py`.
2. **`prismatic/gateway/dashboard_src/tabs/review_factory.html`**:
   - Live summary stats & review job cards template.
3. **`prismatic/gateway/dashboard_src/scripts/dashboard.js`**:
   - `renderReviewFactoryView()`, `authorizeReviewJobMerge()`, and `rejectReviewJob()`.
