# Alignment Document Processing Workflow

## When to Use
When the user has Google Drive documents containing strategic analysis or business deliverables (typically named "Alignment 1", "Alignment 2", etc.) that need to be read, improved, and integrated into the golden thread system.

## Pipeline

### 1. Discover
```python
mcp_gdrive_drive_search(query="Alignment", pageSize=20)
```
Look for documents matching the pattern. The user typically names them "Alignment N: [topic]".

### 2. Read All
```python
mcp_gdrive_drive_read_file(fileId="...")
```
Google Docs export as markdown via the MCP tool. Read them all — Alignment documents are a series, each builds on the previous.

### 3. Classify
- **Strategic analysis** (like Alignment 1): Contains market data, recommendations, timelines. These inform prioritization but are not directly deliverable.
- **Deliverables** (like Alignment 2-7): Contain templates, emails, listings, SOPs, MOUs. These are meant to be USED.

### 4. Improve Deliverables
For each deliverable document:
- Fill in placeholder brackets (`[Name]`, `[Client Company]`)
- Add concrete details you know about the user (location, contacts, phone, website)
- Add ROI examples with real numbers
- Add "before sending" checklists
- Add a footer: "Generated from Alignment Document [X]. Last improved: [date]. Next action: [specific step]."
- Delegate to subagents for parallel processing

### 5. Save Locally
```bash
mkdir -p ${PRISMATIC_HOME}/work/alignment-deliverables/
```
Save each improved deliverable as a separate markdown file. Naming convention: descriptive dash-case (`outreach-email-msp.md`, `server-liquidation-listings.md`).

### 6. Register in Golden Thread
Add each new venture or deliverable to `project-registry.json`:
- Create a Linear project for new ventures
- Update `next_action` fields
- Link to the deliverable file path

### 7. Execute
The deliverable is not the endpoint. The footer says "Next action" — DO THAT NEXT ACTION. If it says "send this email," draft the final version. If it says "post this listing," format it for the platform.

## Common Alignment Document Types
- **Outreach emails**: Fill recipient names, add phone, add LinkedIn variant
- **SOPs**: Add business name, tech stack table, emergency contacts
- **Proposals**: Add industry-specific ROI examples, fill pricing
- **MOUs/Agreements**: Add legal disclaimers, term clauses
- **Listings**: Add photo tips, pricing verification notes, tags
- **Workflows**: Add specific terminal commands, cron schedules, directory paths
