# Strategic Research → Execution Tasks Pipeline

AGY produces strategic research reports that contain numbered, specific action items.
The orchestrator converts these directly into executable Linear issues with expected impact metrics.

## Pattern

### 1. Research Phase
Strategic research runs on a targeted question (e.g., "Performance & Conversion Optimization Plan"). Output lands in
designated report paths as structured markdown files (summary, audit, plan, walkthrough).

### 2. Extract Actions
Read the summary report. Each numbered action item becomes an execution issue:
- Pull the exact recommendation text as the title
- Include the expected impact metric in the description
- Reference the source report path
- Assign to appropriate execution agent with appropriate priority

### 3. Close Research Issue
Once all action items are extracted as issues, close the research issue.

### 4. Swarm Executes
Assigned agents pick up implementation tasks FIFO and ship verified fixes to production.

## Priority Rule
When research produces insights across multiple initiatives, prioritize the high-impact operational paths with verified ROI metrics.
