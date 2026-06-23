# Sentinel ITAD — Subcontract Loop Workflow
## Phase 1 Feeder Mode: Collection → Harvest → Downstream Destruction
**Version:** 1.0 — June 7, 2026

---

## 1. Overview

This document defines the full operational workflow for Sentinel ITAD's Phase 1 "Feeder Mode." The loop covers: hardware collection from MSP partners → transport → intake → sorting/harvest → downstream subcontracting for R2v3 compliant end-of-life processing → Certificate of Destruction delivery back to the partner.

Sentinel ITAD does NOT hold R2v3 or NAID AAA certifications. Instead, we subcontract end-of-life processing to a SERI R2v3-certified downstream partner, providing the same trust signal to MSP clients at zero certification overhead.

---

## 2. Loop Diagram

```
┌─────────────────────────────────────────────────────────┐
│                    THE SUBCOONTRACT LOOP                  │
│                                                         │
│  MSP Partner ──(1)──▶ Sentinel Bin ──(2)──▶ Pickup      │
│      ▲                                                  │
│      │(6)              │(3)                             │
│      │                 ▼                                │
│  Cert of           Intake & Sort                        │
│  Destruction         │                                  │
│      ▲               ├──▶ Harvest (GPUs, RAM, CPUs)     │
│      │               │      │                           │
│      │               │      ▼                           │
│      │               │  Compute Cluster                 │
│      │               │                                  │
│      │               └──▶ Low-Value / Scrap             │
│      │                       │                          │
│      │                       ▼ (4)                      │
│      │              R2v3 Downstream Partner             │
│      │                       │                          │
│      └─────────────(5)───────┘                          │
│                    [CoD issued]                         │
└─────────────────────────────────────────────────────────┘
```

---

## 3. Step-by-Step Workflow

### Step 1: Collection from MSP Partner

**Trigger:** Partner fills Sentinel Bin, or scheduled pickup date arrives.

**Actions:**
1. Partner notifies via email/text, or bin reaches 80% fill (estimated by frequency)
2. Sentinel ITAD confirms pickup window within 4 business hours
3. Driver dispatched with: tamper-evident bags, chain-of-custody forms, bodycam (Zero-Blind-Spot protocol)

**Artifacts produced:**
- Chain of Custody Form (digital, signed by both parties)
- Tamper-evident seal photograph (serial number recorded)
- GPS-timestamped pickup confirmation

---

### Step 2: Transport

**Vehicle:** Tesla Model Y with:
- Interior camera monitoring (Tesla Sentry Mode)
- Tessie GPS tracking for route verification
- Lockable cargo area for secured transport

**Protocol:**
1. Hardware sealed in tamper-evident bags before leaving partner location
2. Direct transport to Sentinel processing location (Meridian residential)
3. No intermediate stops unless emergency (then notify partner + reseal)

**Verification:**
- Chain-of-custody continuity: seal numbers match collection records
- GPS breadcrumb trail within 15-minute intervals
- Estimated transit time: 15-30 min (Meridian/Treasure Valley)

---

### Step 3: Intake & Sorting at Processing Location

**Location:** Sentinel processing station (Garage Lab, Meridian)

**Intake:**
1. Verify tamper-evident seals are intact
2. Photograph all items against intake card with date/time
3. Record in intake manifest: item type, model, serial number, physical condition

**Sorting categories:**

| Category | Criteria | Destination |
|----------|----------|-------------|
| **A — Harvest** | GPU, RAM, CPU, NVMe, high-value NICs, RAID controllers | Extract → compute cluster or resale inventory |
| **B — Resale** | Complete servers, switches, functional equipment | Test → clean → photograph → marketplace listing |
| **C — Scrap Metal** | Chassis, racks, cabling, non-harvestable components | Weigh → downstream partner |
| **D — Destruction** | Storage devices (HDD, SSD, NVMe), failed drives | Sanitize per NIST 800-88 → physical destroy failed → downstream partner for scrap |
| **E — Hazardous** | Leaking batteries, CRTs (rare), chemical waste | Segregate → special handling → downstream partner |

**Harvest extraction procedure:**
1. ESD-safe workstation with grounding mat and wrist strap
2. Internal components photographed in-situ before removal
3. Harvested items logged: source server, component type, model, condition
4. Harvested items stored in ESD-safe bins, labeled with source asset ID
5. Remaining chassis → Category C (scrap metal)

---

### Step 4: Downstream Partner Transfer

**For Categories C, D, and E:**

**Preparation:**
1. Accumulate minimum viable load (suggested: 50+ lbs scrap or 20+ drives)
2. Contact downstream partner to schedule drop-off/pickup
3. Create downstream manifest: list of all items being transferred, by category
4. Generate Sentinel's own internal Certificate of De-manifestation (acknowledging items leave Sentinel custody)

**Transport to downstream partner:**
1. Load items into Tesla Model Y
2. Document departure: timestamp, item count, estimated weight
3. On arrival: downstream partner signs receiving manifest
4. Receive downstream partner's internal tracking/receipt ID

**Preferred downstream partners (see [downstream_partners_research.md](downstream_partners_research.md)):**
| Rank | Partner | Distance | R2v3 | Best For |
|:----:|---------|:--------:|:----:|----------|
| 🥇 | **Recycle Boise Inc.** | 8 mi | ✅ | Walk-in, full spectrum, ISO 14001 |
| 🥈 | **PC Recyclers of Idaho** | 1 mi | ✅ | Meridian, most convenient |
| 🥉 | **Pacific E-Recycling (Nampa)** | 9 mi | ✅ | Mixed loads with scrap metal |

---

### Step 5: Certificate of Destruction Delivery

**Trigger:** Downstream partner confirms destruction/disposal.

**Downstream partner provides:**
- Receipt of acceptance (may include weighing/scaling ticket)
- Final disposition confirmation (recycled, shredded, smelted)

**Sentinel ITAD generates:**
1. **Full Certificate of Destruction**: Combines Sentinel's chain-of-custody + downstream partner's destruction confirmation
2. **Certificate of Sanitization** (for storage devices): NIST standard applied, serial numbers, method used, operator, SHA-256 hashes
3. **Quarterly Environmental Impact Summary**: Total pounds diverted from landfill, estimated carbon saved

**Delivery to MSP Partner:**
1. Certificates sent via email within 30 days of pickup
2. Digital copies stored in Partner Portal (Phase 2 feature)
3. Physical copies available on request ($10/document)

---

### Step 6: Harvest Utilization

Harvested components (Category A) flow into Sentinel's compute cluster:
- GPUs → AI/ML inference (supports AI Consulting work)
- RAM → Cluster capacity expansion
- CPUs → Spares, testing, or resale
- NVMe/SSD → Cluster storage (after sanitization)

**Resale items (Category B)** are listed on Facebook Marketplace/eBay with proceeds funding operations.

---

## 4. Verification Gates

| Gate | Check | Owner | Frequency |
|------|-------|-------|-----------|
| **G1: Seals Intact** | Tamper-evident seals match collection record | Driver | Every pickup |
| **G2: Intake Complete** | All items photographed and logged | Processor | Every intake batch |
| **G3: Sanitization Verified** | Verification read passes for every drive | Processor | Every drive sanitized |
| **G4: Downstream Receipt** | Partner signed receiving manifest | Processor | Every downstream transfer |
| **G5: CoD Delivered** | Certificate sent to partner within 30 days | Admin | After destruction confirmed |

---

## 5. Documentation Templates

| Document | Template Location | Purpose |
|----------|------------------|---------|
| Chain of Custody | `docs/templates/chain_of_custody.md` (draft) | Signed at pickup |
| Intake Manifest | `docs/templates/intake_manifest.md` (draft) | Internal tracking |
| Certificate of Sanitization | `docs/templates/cert_sanitization.md` (draft) | Per-drive destroy record |
| Downstream Transfer Manifest | `docs/templates/downstream_transfer.md` (draft) | Receipt from partner |
| Certificate of Destruction | `docs/templates/cert_destruction.md` (draft) | Delivered to MSP |

*Note: Template files are TBD — Michael to approve these terms first, then we generate templates.*

---

## 6. Insurance & Compliance Requirements

| Requirement | Status | Action Needed | Est. Annual Cost |
|-------------|--------|---------------|:-----------------:|
| General Liability ($1M+) | ❌ Needed | Quote from local broker | $800-$1,500 |
| Cyber Liability / E&O ($1M+) | ❌ Needed | Quote from broker | $2,000-$5,000 |
| Commercial Auto | Already held (Tesla Model Y) | Verify ITAD coverage | $0 incremental |
| Pollution Liability | ❌ Needed (deferred) | Only if handling hazmat | $1,500-$3,500 |
| Idaho e-waste registration | ♻️ Research needed | Check if registration required | Unknown |

**Total estimated insurance cost:** ~$3,000-$7,000/year (Phase 1 minimum).
*Without insurance, MSP partners may decline engagement. See GRO-105 research for detailed breakdown.*

---

## 7. Operational Overhead Estimate (Phase 1)

| Activity | Est. Time | Frequency | Total/Month |
|----------|:---------:|:---------:|:-----------:|
| Pickup & transport | 30 min | 2x/month | 1 hr |
| Intake & sorting | 45 min | 2x/month | 1.5 hr |
| Harvest extraction | 1-2 hr | 2x/month | 3 hr |
| Downstream drop-off | 45 min | 1x/month | 0.75 hr |
| Documentation | 30 min | 2x/month | 1 hr |
| Marketplace listing (resale) | 30 min | As needed | ~1 hr |
| **Total monthly overhead** | | | **~8 hours** |

---

*Next action: Michael reviews this workflow and either (a) approves proceeding with partner engagement or (b) identifies gaps to address first.*
