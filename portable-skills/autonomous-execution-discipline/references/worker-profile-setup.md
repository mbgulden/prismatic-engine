# Worker Agent Profile Setup Pattern

How to create a dedicated Hermes profile for a human collaborator (like a content writer) to interact with via Telegram. This gives them their own bot to talk to, separate from the orchestrator's main chat.

## When to Use

- You need a human collaborator (Ella for content, a VA for scheduling) to talk to Hermes directly
- You don't want their conversations colliding with the orchestrator's main chat
- They need a bot with domain-specific context (Active Oahu Tours, HD Engine, etc.)
- Each worker gets their own Telegram bot and personality

## Setup Steps

### 1. Create the profile

```bash
hermes profile create <name>
```

Creates `~/.hermes/profiles/<name>/` with default SOUL.md and config.

### 2. Copy provider config

Worker shares the same LLM provider as the orchestrator. Check what the orchestrator uses:

```bash
grep -A5 "^model:" ~/.hermes/profiles/orchestrator/config.yaml
grep "DEEPSEEK_API_KEY\|OPENAI_API_KEY" ~/.hermes/profiles/orchestrator/.env
```

Write the worker's `config.yaml`:

```yaml
model:
  default: deepseek-v4-pro
  provider: deepseek

memory:
  enabled: true
  max_entries: 100

toolsets:
  - terminal
  - file
  - search
  - web
```

### 3. Write the .env

Copy the API key from the orchestrator's `.env`. The worker profile inherits nothing — it needs its own `.env` with at minimum the provider API key and Telegram token.

### 4. Write SOUL.md

The personality file is the most important part. It should include:

- **Role**: Who they are and who they work with
- **Domain knowledge**: What they know about (the website, product, tours, etc.)
- **Responsibilities**: What they do (assign tasks, answer questions, review content)
- **Communication style**: How they talk — concise, friendly, etc.
- **Project context**: File paths, Linear project IDs, repo locations
- **Operating rules**: What they should and shouldn't do

Keep it dense and specific. This is the worker's entire context.

### 5. Telegram gateway setup

Each Hermes profile needs its own Telegram bot token. Two profiles CANNOT share the same token — Telegram only allows one connection per token.

Michael creates a new bot at @BotFather on Telegram (takes 1 minute):
1. `/newbot`
2. Name it (e.g., "Kai — Active Oahu")
3. Pick a username (e.g., `ActiveOahuKaiBot`)
4. Copy the token

Add to worker's `.env`:

```
TELEGRAM_BOT_TOKEN=<token-from-botfather>
TELEGRAM_HOME_CHANNEL=<ella-chat-id>
```

To find the chat ID: Ella messages the bot first, then check `hermes gateway status` or curl the Telegram API.

### 6. Start the gateway

```bash
hermes --profile <name> gateway start
```

The worker is now live. Ella messages the bot → Hermes responds with the worker's SOUL.md personality.

## Pitfalls

- **Each worker needs their own Telegram bot.** You cannot share one bot token across profiles.
- **The `.env` must be in the profile directory** (`~/.hermes/profiles/<name>/.env`), not the global `~/.hermes/.env`.
- **The worker profile has its own memory store** — it won't see the orchestrator's conversations.
- **If you need the worker to see Linear tasks**, include the Linear API key in their `.env` and the project context in SOUL.md.
- **SOUL.md is the entire personality.** If the worker seems off-target, fix their SOUL.md.
