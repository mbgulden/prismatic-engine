# Bot Delegation Bridge — Standalone Bots → Hermes Agents

## Problem

Standalone Next Step bots (Jamie, Sage, Sam) run as python-telegram-bot processes — they have the AI API but lack Hermes tools (terminal, file, web, search, skills). They can chat but can't compute, research, read files, or run commands.

## Solution: File-Based IPC + Fred Cron Watchdog

Three components:

### 1. Bridge Module (`bot_delegation.py`)

Drop-in Python module for bot.py. Provides `ask_fred()` (blocking) and `ask_fred_async()` (fire-and-forget):

```python
from bot_delegation import ask_fred
result = ask_fred("Search the web for latest HD transit insights", 
                   bot_name='jamie', chat_id=chat_id, timeout=60)
```

Module writes request JSON to `/tmp/bot-delegation/requests/{id}.json`, polls for response at `/tmp/bot-delegation/responses/{id}.json`.

### 2. Bot Message Handler (`!fred` command)

Patch the bot's `handle_message` to intercept `!fred <prompt>` and `!ask <prompt>`:

```python
if DELEGATION_ENABLED and (text.startswith('!fred ') or text.startswith('!ask ')):
    prompt = text.split(' ', 1)[1]
    await update.message.reply_text("🔄 Delegating to Fred...")
    result = await asyncio.to_thread(ask_fred, prompt, bot_name='jamie', 
                                      chat_id=chat_id, timeout=60)
    await update.message.reply_text(result[:4000])
    return
```

### 3. Fred Cron Watchdog

Cron job (`every 1m`) under orchestrator profile:

- Scans `/tmp/bot-delegation/requests/` for JSON files
- Processes ONE request per tick using full Hermes tools
- Writes response to `/tmp/bot-delegation/responses/{id}.json`
- For async requests, also sends confirmation via `hermes send --to telegram:{chat_id}`

## Deployment Steps

1. Create directories: `mkdir -p /tmp/bot-delegation/{requests,responses}`
2. Copy `bridge.py` → `bot_delegation.py` in each bot directory
3. Add import + delegation block + `!fred` handler to each bot's `bot.py`
4. Create cron job under orchestrator profile with `terminal,file,search,web,skills,session_search` toolsets
5. Restart bots: `sudo systemctl restart next-step-bot becca-sage next-step-sam`

## File Locations

| File | Path |
|---|---|
| Bridge module | `/home/ubuntu/work/bot-delegation/bridge.py` |
| Bot copy | `/home/ubuntu/work/next-step-{user}/bot_delegation.py` |
| Request dir | `/tmp/bot-delegation/requests/` |
| Response dir | `/tmp/bot-delegation/responses/` |

## Limitations

- 60-second polling interval (cron every 1m) — not real-time
- Blocking `ask_fred()` holds the bot's message handler thread
- One request per tick — concurrent delegations queue up
- Response limited to ~4000 chars (Telegram message limit)
