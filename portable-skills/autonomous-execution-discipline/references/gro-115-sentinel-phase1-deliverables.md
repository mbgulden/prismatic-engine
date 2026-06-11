# GRO-115 — Sentinel ITAD Phase 1 "Feeder Mode" Deliverables
## Completed by Nudge Executor — June 7, 2026 (Updated after second run)

## Issue Status
- **GRO-115**: Active (verified in Linear). State: "In Review". Labels: requires:human-approval, type:research, agent:done
- **Title**: "Launch Sentinel ITAD Phase 1 'Feeder Mode' — MSP outreach, HP DL380 liquidation, downstream partner vetting"
- **Signal**: e4414677-4bdb-4e79-a826-f1a51e9205d6
- **Dispatches ignored**: 40+ dispatcher routing comments with zero prior execution

## Pre-Existing Artifacts Found (Step 0.5 pre-verification)
- `sentinel-itad/docs/msp_outreach_kit.md` — 7 MSP targets, pitch, email template, walk-in protocol
- `sentinel-itad/docs/dl380_liquidation_kit.md` — FB Marketplace + eBay listing drafts, pricing strategy, contact strategy
- `sentinel-itad/docs/ebay_setup_checklist.md` — One-time eBay/PirateShip integration checklist
- `sentinel-itad/ops/ebay_test_listing.py` — eBay sandbox test (needs API keys)
- `sentinel-itad/ops/ship.py` — PirateShip label generation (needs API keys)

## New Artifacts Created This Cycle (June 7, second run)

| File | Purpose |
|------|---------|
| `docs/downstream_partners_research.md` | SERI R2 Certified Facilities Directory query — 3 R2v3 facilities within 10mi of Meridian (Recycle Boise Inc. best pick, PC Recyclers of Idaho, Pacific E-Recycling in Nampa). Zero NAID AAA within 150mi. Full partner profiles with contact info and driving distances. |
| `docs/sentinel_bin_placement_terms.md` | Full terms-of-service: bin placement, accepted/rejected materials, NIST 800-88 sanitization protocol, liability caps ($500/incident), 7-day at-will termination, free pricing model, downstream R2v3 partner coverage. |
| `docs/subcontract_loop_workflow.md` | 6-step operational workflow: Collection -> Transport -> Intake & Sort -> Downstream Transfer -> Certificate of Destruction -> Harvest Utilization. Includes verification gates (G1-G5), insurance requirements ($3k-$7k/yr estimated), monthly overhead projection (~8h/mo). |

## Research Methodology Used (reusable for any SERI R2 query)
- Queried SERI R2 Certified Facilities Directory at sustainableelectronics.org by reverse-engineering their AJAX API
- Extracted all 2,951 facility records, filtered by state (ID, OR, WA, UT, NV, MT)
- Cross-referenced each candidate via website visits, phone lookups, Google Maps
- Calculated straight-line driving distances from Meridian, ID (43.6121 N, 116.3915 W)
- Delegate task (subagent with toolsets: ['web','terminal','file']) completed 43 API calls in 386 seconds

## What Still Needs Michael (requires:human-approval gates)
1. **Approve MSP outreach** — greenlight contacting 7 Treasure Valley MSPs
2. **Approve marketplace listings** — set up eBay developer account (~15 min) or approve FB Marketplace
3. **Fill in buyer contacts** — 3 slots in DL380 Liquidation Kit marked [MICHAEL TO FILL]
4. **Review Sentinel Bin Placement Terms** — read and confirm before first bin is placed
5. **Insurance** — quote General Liability + Cyber/E&O ($3k-$7k/yr est.)

## Acceptance Criteria Coverage

| # | Criterion | Status | Notes |
|---|-----------|--------|-------|
| 1 | Identify/contact 4+ MSPs | Ready | Kit written; Michael must approve outreach |
| 2 | Draft bin placement terms | Done | docs/sentinel_bin_placement_terms.md |
| 3 | Create DL380 listings | Ready | Templates ready; needs Michael to list or set up API |
| 4 | Research downstream partners | Done | docs/downstream_partners_research.md (3 R2v3 within 10mi) |
| 5 | Document subcontract loop | Done | docs/subcontract_loop_workflow.md |

## Phase 2 (when API keys set up)
- Run python3 ops/ebay_test_listing.py --sandbox to verify eBay
- Run python3 ops/ship.py --dry-run --weight 45 to verify PirateShip
- Contact Recycle Boise Inc. (208-871-9432) to establish downstream relationship