# AI Consulting Outreach Pipeline

Pattern for building a local-market AI consulting outreach pipeline. Used for Hawaii (Oahu) and Treasure Valley (Idaho) markets for beyondsaas.ai.

## Infrastructure

- `leads.json` — Single source of truth for all leads. Schema: id, company, industry, location, size_estimate, ai_readiness (1-5), bottleneck, contact_name, contact_title, contact_email, contact_phone, status (researching → contact_found → contacted → replied → meeting → won/lost), hook, notes
- `outreach.py` — CLI tool for managing leads and generating personalized emails. Commands: stats, leads <status>, generate <lead_id>, batch <status>
- `outreach/` directory — Holds generated email drafts as .txt files
- Linear issues — One parent epic + sub-issues per industry segment

## Lead Research Workflow

1. **Initial research:** AGY or subagent researches businesses by industry in the target market. Output: markdown table with company, industry, size, AI readiness, bottleneck, hook. Save to `~/work/research/ai-consulting/<market>-outreach-list.md`.
2. **Contact discovery:** Per lead, find decision-maker name + email. Check: company website (About/Team), LinkedIn, Idaho SOS business registry (sosbiz.idaho.gov), BBB.org, Google Maps. Save findings back to leads.json.
3. **Email generation:** Use `outreach.py generate <lead_id>` to create personalized draft from templates.
4. **AGY review:** Create a Linear task labeled `pipeline:research-strategy` + `agent:agy` for AGY to quality-check the pipeline before outreach begins.
5. **Pipeline tracking doc:** Maintain `<market>-pipeline.md` with lead status table, outreach calendar, and revenue estimates.

## Outreach Angle Types

Different businesses need different hooks. The angle MUST match what they actually sell:

- **Hardware MSPs:** Local AI runs on their GPUs/servers. Privacy + predictable cost + hardware upsell. "AI as hardware sales accelerator."
- **Service MSPs:** White-label AI layer above IT. No overlap with managed services. "Your clients are asking about AI — here's the answer."
- **Law firms:** Document automation — intake, discovery, contracts. "Reduce 45-minute intake to 5 minutes."
- **Medical:** Prior auth, claims, scheduling. Compliance-sensitive = competitive moat.
- **Logistics:** Routing optimization, inventory intelligence, order processing.

## Pitfalls

- **Wrong company name mismatch:** "CompuNet" can be CompuNet Inc. (Meridian, ID) OR CompuNet International (St. Louis Park, MN). Always verify the PHYSICAL ADDRESS matches the target market before researching contacts. Google Maps links from the user are authoritative.
- **Cloudflare/WAF blocks:** Law firm and enterprise websites often block automated access. Flag these for manual research.
- **Email inference:** Firstname@company patterns are common but should be noted as "inferred, verify before sending."
