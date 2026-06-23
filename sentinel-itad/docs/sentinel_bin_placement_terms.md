# Sentinel Bin Placement Terms & Conditions
## Sentinel ITAD — Phase 1 Feeder Mode
**Version:** 1.0 — June 7, 2026
**Status:** Draft for Michael review

---

## 1. Overview

The Sentinel Bin is a locked collection console placed at partner MSP locations for secure accumulation of decommissioned IT hardware. The MSP fills the bin at their own pace; Sentinel ITAD performs scheduled pickups. This document defines the terms governing bin placement, pickup, liability, and termination.

---

## 2. Agreement Structure

This is a **Non-Exclusive, At-Will Service Agreement** between:
- **Sentinel ITAD** (a division of Growth Web Development, Meridian, ID) — "Collector"
- **[MSP Name]** — "Partner"

No written contract is required for initial bin placement. This document serves as the operating framework. A formal MOU can be executed upon request.

---

## 3. Bin Placement Terms

| Term | Detail |
|------|--------|
| **Bin Type** | Locked metal collection console, ~24"x18"x18", with tamper-evident seal slots |
| **Placement** | Partner-designated location (server room, storage closet, loading dock) |
| **Ownership** | Bin remains property of Sentinel ITAD at all times |
| **Maintenance** | Sentinel ITAD inspects and services bin during each pickup |
| **Replacement** | Damaged or compromised bins replaced within 48 hours of notification |
| **Labeling** | Each bin displays: "Sentinel ITAD — Authorized Collection Point — DO NOT DISCARD" with contact info |

---

## 4. Accepted Materials

### Accepted (free pickup):
- Decommissioned servers and chassis (rackmount, tower)
- Network switches, routers, firewalls, access points
- Patch panels, cabling, rack accessories
- UPS units (intact, no leaking batteries)
- Workstations, thin clients, terminals
- Storage arrays and JBOD enclosures
- Server accessories (rails, ear mounts, cable management arms)

### Accepted with notice (separate processing):
- Hard drives and SSDs (any form factor — see Sections 6-7 for sanitization)
- RAM modules and CPUs
- GPUs and accelerator cards
- RAID controllers and HBAs
- NVMe drives and carrier cards
- Battery backups with detectable swelling or leakage

### NOT accepted (must be disposed of separately by Partner):
- CRT monitors and TVs
- Household appliances
- Hazardous chemical waste
- Medical devices with patient data
- Media containing classified/government data (Partner must sanitize first)

---

## 5. Pickup Schedule & Process

| Cadence | Description |
|---------|-------------|
| **Standard** | Bi-weekly pickup on designated day |
| **On-demand** | Partner emails or calls when bin is full; pickup within 3 business days |
| **Emergency** | Urgent pickup (e.g., office move, security incident) within 24 hours |

**Pickup process:**
1. Partner notifies Sentinel ITAD via email (itad@growthwebdev.com) or text
2. Sentinel ITAD confirms pickup window within 4 business hours
3. Driver arrives in marked vehicle (Tesla Model Y with Sentinel branding)
4. Bin contents are sealed in tamper-evident bags
5. Both parties sign a simple Chain of Custody form (digital or paper)
6. Partner receives a Ticket ID for tracking

---

## 6. Data Security & Sanitization

### For storage devices found in collected hardware:
1. All storage media (HDDs, SSDs, NVMe) are segregated on arrival
2. **Certified-Aligned Sanitization** performed using NIST SP 800-88 standards:
   - HDDs: 1-pass zero-fill + verification read
   - SSDs: ATA Secure Erase (enhanced where supported)
   - NVMe: Sanitize Block Erase per NVMe spec
   - Failed drives: Physical destruction (manual hydraulic crusher)
3. Each sanitization event is logged with: drive serial number, method used, start/end timestamps, operator, SHA-256 hash of verification read
4. Partners can request a Certificate of Data Sanitization for any serial number

### Chain of Custody:
- Every pickup generates a chain-of-custody document
- Document chain: collection → transport → intake → sanitization → downstream transfer
- All events GPS-timestamped via Tessie integration
- Tamper-evident seals recorded at collection and verified at intake

---

## 7. Downstream Processing

Hardware that cannot be harvested for Sentinel's compute cluster is processed through one of three paths:

| Path | Description | R2v3 Coverage |
|------|-------------|---------------|
| **Harvest** | High-value components (GPUs, RAM, CPUs) extracted for internal cluster | Not applicable |
| **Resale** | Tested, functional equipment sold via marketplace listings | Buyer handles end-of-life |
| **Recycling** | Scrap/low-value hardware sent to R2v3 certified downstream partner | ✅ Covered by partner's R2v3 cert |

All downstream recycling is subcontracted to a SERI R2v3-certified facility (see [downstream_partners_research.md](downstream_partners_research.md)). Sentinel ITAD does NOT claim to be R2v3 certified — we operate under our partner's certification for end-of-life processing.

---

## 8. Liability & Indemnification

### Sentinel ITAD responsibilities:
- Safe transport and handling of all collected hardware
- Proper data sanitization per Section 6
- Compliance with all applicable federal, state, and local e-waste regulations
- General liability insurance (minimum $1M)
- Maintenance of downstream R2v3 partner relationship

### Partner responsibilities:
- Truthful representation of hardware condition (known hazards, data sensitivity)
- Removal of any sensitive data before bin placement (per Partner's own data policies)
- Secure bin placement area (not in public/unsecured spaces)

### Limitation of liability:
- Sentinel ITAD's maximum liability for any claim arising from bin placement is limited to $500 per incident
- Neither party is liable for indirect, consequential, or incidental damages
- This limitation does not apply to data breach claims resulting from Sentinel ITAD's gross negligence

---

## 9. Termination

| Trigger | Notice | Process |
|---------|--------|---------|
| **Either party at will** | 7 days written notice | Bin retrieval scheduled within notice period |
| **Breach of terms** | Immediate | Bin retrieved within 24 hours |
| **Partner relocates** | 14 days notice | Bin retrieval coordinated with move |

Upon termination:
- Sentinel ITAD retrieves the bin within the notice period
- Any hardware in the bin at time of retrieval is processed per standard procedures
- Partner receives final Certificate of Destruction (if applicable) within 30 days
- No fees, penalties, or early termination charges

---

## 10. Fees & Pricing

| Service | Fee |
|---------|-----|
| **Bin placement & standard pickup** | Free |
| **On-demand pickup** | Free (up to 2x/month; $25/drop after) |
| **Certificate of Data Sanitization** | Free (digital); $10/paper copy |
| **Emergency pickup (<24h notice)** | $50 |
| **Third-party downstream destruction** | Covered by down-stream partner (no cost to MSP) |
| **Premium Logistics (Premium Logistics & Destruction service)** | $150/hr + $12/drive |

---

## 11. Signatures

By placing a Sentinel Bin at your location, you agree to these terms. A formal MOU can be provided upon request.

**Sentinel ITAD — Growth Web Development**
Meridian, ID
itad@growthwebdev.com

---

*Next action: Michael reviews and approves these terms before any MSP conversations begin.*
