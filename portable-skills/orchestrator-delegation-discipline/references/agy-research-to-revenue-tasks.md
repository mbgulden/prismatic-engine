# AGY Research → Revenue Tasks Pipeline

Proven June 11, 2026 on Active Oahu Tours. AGY produces strategic research reports that contain 
numbered, specific action items. Fred converts these directly into executable Linear issues with 
expected impact metrics.

## Pattern

### 1. AGY Research Phase
AGY runs on a strategic question (e.g., "CRO + SEO Integration Plan"). Output lands in 
`site/_seo/reports/<category>/` as multiple markdown files (summary, audit, plan, walkthrough).

### 2. Fred Extract Actions
Read the summary report. Each numbered action item becomes a Linear issue:
- Pull the exact recommendation text as the title
- Include the expected impact metric in the description (+X% bookings, +Y% CTR)
- Reference the source report path
- Assign to `agent:ned` with appropriate priority

### 3. Close Research Issue
Once all action items are extracted as issues, close the research issue → `agent:done`.

### 4. Ned Executes
Ned picks up the implementation tasks FIFO and ships fixes to AOT production.

## Example: AOT CRO Audit → Implementation (June 11, 2026)

| AGY Finding | Linear Issue | Expected Impact |
|-------------|-------------|-----------------|
| Deep-link CTAs on homepage | GRO-1194 | +15% bookings |
| Fix Sharks Cove geography mismatch | GRO-1195 | Stops 100% leak on 405 sessions/mo |
| Batch schema injection (149 EN + 83 JA) | GRO-1196 | +20-50% CTR |
| Mobile header CSS fix | GRO-1197 | +10% mobile conversions |
| FareHarbor form simplification | GRO-1198 | Decrease checkout abandonment |

## Revenue Priority Rule
When AGY produces research across multiple ventures, prioritize the revenue vehicle:
**Active Oahu Tours > AI Consulting > Darius Star > HD Engine**

Apply highest priority to tasks with explicit ROI metrics in the research.
