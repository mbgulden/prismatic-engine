# Ned / Fred Agent Lane Split

Established June 2026 by Michael. This is the canonical routing guide.

## The Split

| Agent | Role | Linear Label | Model | Cron |
|-------|------|-------------|-------|------|
| **Ned** | PRIMARY executor — does the work | `agent:ned` | openai-codex/gpt-5.5 (OAuth) → deepseek-v4-pro fallback | Every 5 min (2eb84a34c716) |
| **Fred** | Orchestrator — reviews, integrates, routes | `agent:fred` | deepseek-v4-pro | Session-driven |
| **Jules** | Async coding via GitHub PRs | `agent:jules` | Claude 3.5 Sonnet | Dispatcher (e2f1a3b4c5d6) |
| **AGY** | Research, audits, visual QA | `agent:agy` | Gemini 3.5 Flash | Dispatcher (e2f1a3b4c5d6) |

## Routing Rules

1. **Execution tasks → Ned.** If Fred would normally do it, route to Ned instead.
2. **Review/integration → Fred.** Ned finishes → swaps label to `agent:fred` → Fred reviews.
3. **Coding sessions → Jules.** PR-producing async code work on GitHub repos.
4. **Research/audits → AGY.** Broad research, Google Drive synthesis, visual QA.

## Ned Cron Config

```
job_id: 2eb84a34c716
name: Ned — agent:ned task executor (Fred's workhorse)
schedule: every 5m
model: gpt-5.5
provider: openai-codex  ← OAuth, NOT API key
deliver: local  ← silent — posts results as Linear comments, NOT chat. (Michael: \"It's clutter to be on Fred's page.\")
```

**CRITICAL:** Ned MUST use `openai-codex` OAuth as primary, NOT `openai-direct` API key. The API key path burns through quota silently. Always verify `hermes auth list` shows active OAuth tokens before launching Ned tasks.

## Task Creation Patterns

```bash
# Execution task → Ned
labelIds: ["6e0400c9-fc04-4868-86e3-f3156821f413"]  # agent:ned

# Review task → Fred
labelIds: ["a43efb77-534a-4e39-8ff3-76f0e42019d1"]  # agent:fred

# Both (Ned builds, Fred reviews)
labelIds: ["6e0400c9-...", "a43efb77-..."]
```

## Michael's Directive
> "Fred can review instead of do everything. Ned can do and review or whatever needs to be done."
> 
> "Keep the openAI OAuth 5.5 + deepseek-v4-pro and setup agent:Ned tasks. Any task that Fred would normally do should go to Ned."

## When Ned Fires
1. Every 5 minutes, Ned queries Linear for `agent:ned` issues in Todo/Backlog
2. Picks oldest → executes fully → comments result → swaps label to `agent:fred`
3. If no `agent:ned` issues, picks oldest `agent:fred` issue
4. Reports "nothing to do" if both queues empty
