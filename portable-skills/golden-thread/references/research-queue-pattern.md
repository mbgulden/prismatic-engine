# Autonomous Research Queue

Pattern for batching research prompts that the nightly worker processes after clearing the Linear backlog.

## Queue File: `~/work/research/queue.json`

```json
{
  "_description": "Autonomous research prompts. Process in priority order after Linear backlog is clear.",
  "_priority": ["hd-engine", "active-oahu-tours", "ai-consulting", "hermes-infra"],
  "queue": [
    {
      "id": "research-001",
      "domain": "hd-engine",
      "category": "revenue",
      "prompt": "Research HD API competitors...",
      "output": "~/work/research/hd/competitor-analysis.md"
    }
  ],
  "completed": []
}
```

## When to Use
- Linear backlog is clear or blocked on human input
- User approved a batch of research prompts they want done overnight
- Need to generate content, research, or analysis autonomously

## Worker Integration
The nightly worker (`cron:0ce73bbeee4e`) and nudge trigger both process this queue in Phase 2, after Linear backlog. Order: `hd-engine` → `active-oahu-tours` → `ai-consulting` → `hermes-infra`.

## User Workflow
1. Hermes generates prompts based on project needs and user goals
2. User approves all or selects specific ones
3. Hermes writes them to `~/work/research/queue.json`
4. Nightly worker (or nudge) picks them up and executes autonomously
5. Results written to specified output paths, items moved to `completed`
6. User sees results in the morning

## Prompt Format Rules
Each prompt must be:
- Self-contained — no external context needed
- Specific output path — tells the worker where to write
- Categorized: `hd-engine`, `active-oahu-tours`, `ai-consulting`, or `hermes-infra`
- Prioritized: `revenue`, `content`, `leads`, or `capability`
