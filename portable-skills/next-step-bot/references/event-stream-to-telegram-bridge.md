# Event Stream to Telegram Bridge

Build persistent, auto-reconnecting bridges that consume internal event streams (SSE, webhooks, message queues) and push formatted notifications to Telegram. Used for agent lifecycle monitoring, dispatch cycle summaries, and infrastructure alerting.

> **Absorbed from:** `event-telegram-bridge` skill (2026-06-09 consolidation pass). The standalone bridge pattern lives here as a specialized Telegram bot use-case under the `next-step-bot` umbrella.

## Architecture

```
Event Source (SSE server, webhook, queue)
        │
        ▼
   Bridge Agent (async Python)
        │
        ▼
   Telegram API (sendMessage with HTML)
```

## Step-by-Step

### 1. Identify the event source and message shape

Read the event source code first. Know:
- The event schema (what fields are always present)
- The event types you need to format
- The transport (SSE → streaming HTTP, webhooks → POST endpoint, etc.)

### 2. Build the bridge script

Template in `templates/bridge.py`. Key sections:

- **Config loading**: Load from env with a `_load_dotenv()` fallback that checks multiple candidate paths. Services started via systemd won't have the user's shell env, so dotenv loading is essential.
- **Telegram sender**: `async def send_telegram(text) → bool`. Use HTML parse mode for rich formatting. Escape user-supplied strings with `html_escape()`.
- **Event formatter**: One formatting function per event type. Return `None` to skip an event silently. Use these Telegram HTML patterns:
  - Bold: `<b>text</b>`
  - Italic: `<i>text</i>`
  - Code: `<code>text</code>`
  - Emoji for visual scanability: 🚀✅❌⏸️📊🔄📢
- **SSE reader**: If the source is SSE, use `httpx.AsyncClient.stream("GET", url)` with `response.aiter_text()`. Parse the SSE wire format manually (field lines + double-newline separators). Handle keepalive comments (`: comment\n`).
- **Reconnection loop**: `while True` with exponential backoff. Reset backoff to initial on successful event receipt. Cap at 60s max.

### 3. Test with event injection

Start the bridge in background, then inject test events via the event source's publish endpoint (or by directly calling the emitter). Verify:
- Events appear in the source's status/log API
- Bridge logs show receipt and Telegram send
- Telegram API returns HTTP 200

### 4. Deploy as systemd service

Create a `.service` file:
- Use `EnvironmentFile=` to load secrets from an existing `.env` — never hardcode tokens
- `Restart=always` with `RestartSec=5`
- `Wants=` the upstream event server service so it starts after
- `StandardOutput=journal` + `StandardError=journal` for `journalctl` visibility

Install: `sudo cp <name>.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now <name>`

### 5. Verify in production

- `sudo journalctl -u <name> -f` to watch live
- Check SSE subscriber count on the event server's health endpoint
- Publish a test event and confirm Telegram delivery in journal

## Pitfalls

- **httpx version matters**: The version in `pipx`-managed venvs can be old (0.13.x). See `references/httpx-0.13-api.md` for the API subset available.
- **No `global` on module-level config**: Python rejects `global CHAT_ID` if `CHAT_ID` is used earlier in the same function body (even in a default argument). Use a local variable and set the module attribute via `import <self>` instead.
- **systemd EnvironmentFile format**: Plain `KEY=VALUE` lines, no `export`, no quotes around values. Comments with `#`. Does NOT expand variables.
- **SSE keepalives**: SSE servers send `: keepalive\n\n` comments. Your parser must ignore lines starting with `:`.
- **Partial chunks**: SSE messages can be split across TCP frames. Buffer incomplete data and wait for `\n\n`.
- **Swarm notification routing pitfall (Autobot lesson, Jun 2026):** Standalone bots that post automated notifications (SSE feeds, swarm events, dispatch cycles) must use a DEDICATED bot token — not shared with the main chat bot. When `autobot.py` posted to `TELEGRAM_CHAT_ID=8190664947` with a misconfigured token, every agent launch/completion/stall appeared in Jamie's chat. The fix: dedicated @Autob0tautob0t_bot token in the service file's `Environment=`. The bot can only DM a user after `/start` — until then, 403 Forbidden errors are normal and messages queue silently.
