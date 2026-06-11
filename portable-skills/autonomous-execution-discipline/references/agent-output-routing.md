# Agent Output Routing & Model Preferences

## The Rule

Autonomous cron agents MUST NOT clutter the orchestrator's chat. Route output to Linear, not Telegram.

## Agent Configuration Table

| Agent | Cron Job | Deliver | Primary Model | Output Destination |
|---|---|---|---|---|
| **Ned** | `2eb84a34c716` (every 5m) | `local` | `openai-codex`/`gpt-5.5` | Linear issue comments |
| **Fred** | — (orchestrator) | `origin` | `deepseek-v4-pro` | Active conversation |
| **Jules** | — (CLI + cron dispatch) | — | Claude 3.5 Sonnet | GitHub PRs + Linear |
| **AGY** | — (CLI + cron dispatch) | — | Gemini 3.5 Flash | Files on disk |

## Ned's Pattern (proven Jun 2026)

```
cronjob update:
  deliver: local          # silent — no chat messages
  model: gpt-5.5          # premium primary
  provider: openai-codex  # OAuth, not API key
  prompt: "Post ALL results as Linear comments. Do NOT deliver chat messages."
```

Ned queries Linear for `agent:ned` issues → executes → posts structured comment → swaps label to `agent:fred` for review.

## Why This Matters

Michael's directive: "This needs to appear either on Ned's profile or Autobot. It's clutter to be on Fred's page."

Autonomous agents produce high-frequency output (every 5 min). Routing it to the orchestrator's chat creates noise that buries human conversation. Linear is the system of record for task status.

## Model Preference

- **Autonomous agents**: Premium models as PRIMARY (openai-codex/gpt-5.5). They run unattended and need quality output.
- **Orchestrator**: Cost-efficient models (deepseek-v4-pro). Human-in-the-loop can correct.
- **Fallback**: System-level `fallback_providers` chain handles model unavailability.

## Adding a New Autonomous Agent

1. Create Linear label: `agent:<name>`
2. Create cron job with `deliver: local`, premium model
3. Prompt instructs: post to Linear, not chat
4. Add to dispatcher `AGENT_CONFIG` with `mode: signal`
5. Update this reference file
