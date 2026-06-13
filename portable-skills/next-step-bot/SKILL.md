---
name: next-step-bot
description: Build, modify, and operate Next Step Telegram bots — executive function + Human Design coaching assistants for AuDHD users. Covers multi-instance architecture, SOUL.md authoring, scheduler/proactive features, dopamine party mechanics, silent HD pre-fetch, and systemd deployment.
category: next-step
triggers:
  - next step bot
  - jamie or sage telegram bot
  - executive function bot
  - dopamine party
  - daily check-in bot
  - telegram bot human design
  - AuDHD assistant bot
---

# Next Step Bot Architecture

Build and operate Telegram bots that combine executive function coaching with Human Design wisdom for AuDHD users. Each user gets an isolated bot instance with their own personality (SOUL.md), database, AI model, and Telegram token.

## Architecture Overview

> **Architecture philosophy:** The Next Step bot uses a unified agent conversation pipeline — one system prompt, one brain, no classifier routing. See `references/unified-pipeline-architecture.md` for the full architectural rationale (absorbs the former `unified-agent-conversation-pipeline` skill).

Support files: `references/bot-delegation-bridge.md` (standalone bot → Hermes agent delegation via file-based IPC + Fred cron watchdog), `references/bot-capability-audit.md` (runtime HD MCP / tool capability verification via /proc/PID/environ + MCP import test + source grep), `references/dopamine-party.md` (celebration mechanics), `references/soul-authoring-guide.md` (SOUL.md template), `references/soul-hd-informed-example.md` (Becca's 6/2 Projector SOUL.md — full HD-chart-informed personality), `references/bot-debugging-409-conflict.md` (409/401 conflict diagnostic workflow), `references/bot-access-audit.md` (multi-bot ALLOWED_CHAT_IDS audit), `references/event-stream-to-telegram-bridge.md` (SSE/webhook → Telegram notification bridge for infrastructure alerting — absorbed from former `event-telegram-bridge` skill), `references/httpx-0.13-api.md` (httpx 0.13.3 API subset reference), `templates/next-step-bot.service.template` (systemd unit), `templates/bridge.py` (SSE→Telegram bridge starter template).

```
~/work/next-step-{user}/
├── bot.py              # Main bot (shared code, env-configured)
├── SOUL.md             # Personality + coaching rules (per-user)
├── .env                # Credentials (gitignored)
├── family.json         # Family birth data (shared, gitignored)
├── skills/             # Per-bot skill files (dopamine-party.md, etc.)
├── data/
│   └── next_step.db    # SQLite: tasks, state, conversations, journals, schedules
├── journals/
│   └── YYYY/MM/DD.md   # Journal entries from [JOURNAL: ...] tags
└── next-step-{user}.service  # systemd unit
```

All instances share the same `bot.py` code. Per-user differentiation comes from environment variables in `.env` and the `SOUL.md` file.

## Environment Variables

```
TELEGRAM_BOT_TOKEN=...       # From @BotFather (required)
NEXTSTEP_API_KEY=...         # AI provider API key (required)
NEXTSTEP_BASE_URL=...        # AI provider base URL
NEXTSTEP_MODEL=...           # Model name (default: deepseek-chat)
NEXTSTEP_NAME=...            # Assistant name (e.g., Jamie, Sage)
NEXTSTEP_PROFILE=...         # Instance ID for logging (e.g., michael, becca)
NEXTSTEP_ACTIVE_PROFILE=...  # Default family.json profile
NEXTSTEP_MCP_SRC=...         # Path to OpenHumanDesignMCP server src/
NEXTSTEP_FAMILY_PATH=...     # Path to shared family.json
NEXTSTEP_DB_PATH=...         # SQLite DB path
NEXTSTEP_JOURNALS_DIR=...    # Journal storage path
OHDMCP_FAMILY_JSON=...       # DUPLICATE of family.json path — required by MCP server's
                             # get_deep_context() / get_relationship_composite().
                             # Without this, HD queries fail with "Profile not found"
                             # even though NEXTSTEP_FAMILY_PATH is correct.
                             # Set to same value as NEXTSTEP_FAMILY_PATH.
```

## Multi-Instance Deployment

### 1. Create bot instance directory
```bash
mkdir -p ~/work/next-step-{user}/{data,journals,skills}
cp ~/work/next-step-bot/bot.py ~/work/next-step-{user}/
```

### 2. Write SOUL.md
See `references/soul-authoring-guide.md` for the full template. Key sections:
- Identity (name, personality, tone)
- User's HD profile (internal, never recited — use to modulate coaching)
- Prime directive (hide the mountain, protect energy, etc.)
- Core behaviors (task ingestion, micro-scoping, dopamine party, journal)
- Tools available, edge cases, family network
- Proactive features section

### 3. Configure .env

**⚠️ HD CRITICAL:** You MUST set BOTH `NEXTSTEP_FAMILY_PATH` AND `OHDMCP_FAMILY_JSON` to the same file. The bot code uses the former; the MCP server (`get_deep_context()`, `get_relationship_composite()`) uses the latter. Missing `OHDMCP_FAMILY_JSON` = all HD profile lookups fail silently with "Profile not found." See the `OHDMCP_FAMILY_JSON` pitfall below.

```bash
cat > .env << 'EOF'
TELEGRAM_BOT_TOKEN=...
NEXTSTEP_API_KEY=...
NEXTSTEP_BASE_URL=https://api.openai.com/v1
NEXTSTEP_MODEL=gpt-5.5
NEXTSTEP_NAME=Sage
NEXTSTEP_PROFILE=becca
NEXTSTEP_ACTIVE_PROFILE=becca
NEXTSTEP_MCP_SRC=${PRISMATIC_HOME}/work/OpenHumanDesignMCP/hd-mcp-server/src
NEXTSTEP_FAMILY_PATH=${PRISMATIC_HOME}/work/next-step-bot/family.json
OHDMCP_FAMILY_JSON=${PRISMATIC_HOME}/work/next-step-bot/family.json   # ← REQUIRED for HD
NEXTSTEP_DB_PATH=${PRISMATIC_HOME}/work/next-step-becca/data/next_step.db
EOF
chmod 600 .env
```

### 4. Create systemd service
Copy and modify from `references/next-step-bot.service.template`.

### 5. Start
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now next-step-{user}
```

## Unified Hermes + Next Step Mode

When Michael is talking to Hermes directly (not through the standalone Jamie bot), the Hermes agent can load the Next Step SOUL.md and skills into its own context to act as a unified assistant — all the power of Hermes (terminal, files, GitHub, MCP) plus all the personality of Jamie/Sage (coaching, HD wisdom, dopamine party).

To enter unified mode:
1. `read_file ~/work/next-step-bot/SOUL.md` (or next-step-becca/SOUL.md)
2. `read_file ~/work/next-step-bot/skills/dopamine-party.md`
3. Operate with both Hermes capabilities AND the SOUL.md personality/coaching rules

This eliminates context switching between Hermes (build things) and Jamie (coach things). The user gets one assistant that does both.

When in unified mode, still follow the SOUL.md's Prime Directive (hide the mountain, never show full task lists, etc.) and dopamine party celebration mechanics.

## Scheduler & Proactive Features

The bot runs a background scheduler thread (30-second poll) that checks `scheduled_messages` SQLite table.

### Commands
- `/remind HH:MM message` — One-time reminder at specified UTC time
- `/daily HH:MM` — Recurring daily check-in
- `/daily off` — Disable daily check-in
- `/daily` (no args) — Show current daily setting

### Daily Message Generation
When a `__DAILY_GENERATE__:profile_hint` placeholder fires, the scheduler:
1. Fetches fresh transits via MCP `calculate_chart_with_transits()`
2. Reads recent journal entries (last 7 days)
3. Pulls recent conversation topics (last 5 messages)
4. Calls the AI model to craft a 2-4 sentence personalized message
5. Falls back to a warm generic message if MCP/AI unavailable

The generated message is never pre-canned — it's fresh each day, grounded in the user's actual transits, journal themes, and recent concerns.

### Adding to SOUL.md
Include a `## Proactive Features` section so the assistant knows it can suggest `/remind` and `/daily` to the user naturally in conversation.

## Silent HD Pre-fetch

The bot injects HD context into the system prompt before the AI sees the user's message — no visible tool call.

### Transit Caching (1-Hour TTL)

A separate `_fetch_silent_transit_context()` function with 1-hour TTL runs alongside the 6-hour natal chart cache. Planetary positions change throughout the day, so transit data must be fresher:

- **Natal chart cache**: 6-hour TTL (rarely changes)
- **Transit cache**: 1-hour TTL (positions shift throughout the day)
- Transit refresh only fires for active users (last message < 1 hour ago) — skip the MCP overhead for inactive users.
- Transit context is injected as a separate `[FRESH TRANSITS — 1-hour window]` block alongside the natal `[SILENT HD CONTEXT]` block. Keep them separate so the AI can distinguish stale natal transits from fresh ones.
- **Topic learning**: Analyzes last 10 user messages, detects dominant topics (relationship, career, energy, direction, emotions) via keyword matching (NO AI call — zero latency). A topic is "dominant" if >20% of recent messages match. Injects adaptive persona hints like `[TOPIC AWARENESS — Learned from recent conversations]`.

All three layers of context are appended to `state_context` before building the messages array.

## Dopamine Party Mechanics

For AuDHD brains, celebration is neurological scaffolding — not optional fluff. See `references/dopamine-party.md` for the full skill file.

Core requirements:
- NEVER repeat celebration style twice in a session (ADHD brain habituates)
- Rotate through categories: emoji explosions, metaphor magic, genuine awe, playful ridiculousness, gentle wisdom, proud friend energy
- Include surprise rewards every 3-5 completions
- Always offer an energy-aware next step after celebration
- Skip celebration for small tasks = pattern fail (small tasks are the HARDEST)

## Journal System

The assistant writes journal entries via `[JOURNAL: one-line summary]` in its response. The bot strips the tag before sending to the user and appends to `journals/YYYY/MM/DD.md`. Recent entries (last 7 days) are injected as context on session start.

## Telegram Output Formatting

When delivering reports or long-form content to Telegram users, NEVER use raw markdown that Telegram can't render. Follow these rules:

**Blocked (causes garbage characters):**
- ❌ Pipe tables (`| col1 | col2 |`) — Telegram has NO table syntax
- ❌ Horizontal rules (`---` or `***`) — renders as raw dashes/asterisks
- ❌ Nested markdown in code blocks — unpredictable on mobile

**Use instead:**
- ✅ Bullet lists with `•` for key-value pairs — clean on all devices
- ✅ Bold headers (`**Section:**`) for labeling
- ✅ Spacing between sections for visual separation
- ✅ Emoji for visual cues (🌱 🎯 ⚠️)
- ✅ Short paragraphs, 2-4 lines max

**Report delivery pattern:**
1. Write the report as a clean markdown file (using the Telegram-safe formatting above)
2. Deliver via `MEDIA:/absolute/path/to/report.md` in the response
3. For PDF: `pandoc input.md -o output.pdf --pdf-engine=wkhtmltopdf` (install via `sudo apt-get install -y pandoc wkhtmltopdf`)
4. Google Docs: see `scripts/gdocs_helper.py` for auth + upload workflow

## Google Docs Delivery

Support files: `scripts/gdocs_helper.py` (Google Docs write helper).

To deliver reports to Google Docs:
1. Ensure `pip install --break-system-packages google-api-python-client google-auth-oauthlib` is installed
2. Run `python3 scripts/gdocs_helper.py auth` — uses existing OAuth keys from `~/.config/mcp-gdrive/gcp-oauth.keys.json`, requests `drive.file` + `docs` write scopes
3. User visits the printed URL, authorizes, receives a redirect to `localhost:PORT` which fails BUT the URL bar contains the `?code=` parameter
4. User copies the entire redirect URL and pastes it; extract the code parameter
5. Exchange code for token: `flow.fetch_token(code=code)` → saves to `~/.config/mcp-gdrive/.gdocs-write-token.pickle`
6. Use `create_doc(title, content)` and `update_doc(doc_id, content)` to push reports

The existing GDrive MCP is read-only (`drive.readonly` scope). GDocs write uses a separate token with `drive.file` scope (only files we create).

## Pitfalls

- **SOUL.md is loaded at startup and cached globally** — restart the bot after SOUL.md changes
- **Both bot.py files must stay in sync** — changes to one must be copied to the other
- **Timezone**: All bot times are UTC. Users in other timezones must adjust `/daily` and `/remind` times accordingly
- **family.json sharing**: Both instances point to the same family.json (in next-step-bot/) via `NEXTSTEP_FAMILY_PATH`. Don't create separate copies
- **Systemd EnvironmentFile**: Secrets go in `.env` (mode 600). Never commit tokens
- **Scheduler thread access**: Uses `asyncio.run_coroutine_threadsafe()` to bridge the background thread to the asyncio event loop. Must call `_start_scheduler(app)` AFTER `app = Application.builder().token().build()` but BEFORE `app.run_polling()`
- **Telegram formatting**: Never use pipe tables or horizontal rules. Use bullet lists, bold labels, and emoji spacing. Test reports by sending to yourself first.
- **Standalone bot ≠ Hermes profile**: The standalone bot (`bot.py` process) is a separate Python process from any Hermes profile gateway. The `~/.hermes/profiles/<name>/config.yaml` does NOT control the standalone bot — only the `.env` file does. When debugging model/provider issues, check the actual env vars in the running process: `cat /proc/<pid>/environ | tr '\\0' '\\n' | grep NEXTSTEP`. The Hermes profile config and the standalone bot `.env` can quietly diverge.
- **`.env` provider overrides default to DeepSeek but can silently point elsewhere**: The bot defaults to `NEXTSTEP_BASE_URL=https://api.deepseek.com` and `NEXTSTEP_MODEL=deepseek-chat`, but the `.env` file can override these to any provider (e.g., `https://api.openai.com/v1` with `gpt-5.5`). If the bot is getting 429 rate-limit errors or 401 auth errors, check `/proc/<pid>/environ` FIRST — don't assume it's using the default provider. Symptoms: 429 from OpenAI rate limits, 401 from expired OAuth tokens.
- **API key must match the provider endpoint — mismatch = silent 401**: The bot uses `OpenAI(api_key=NEXTSTEP_API_KEY, base_url=NEXTSTEP_BASE_URL)` — it sends whatever key is configured to whatever base URL is configured. An OpenAI key (`sk-proj-...`) sent to `https://api.deepseek.com` produces HTTP 401 "Authentication Fails, Your api key is invalid." An DeepSeek key (`sk-0a71...`) sent to `https://api.openai.com/v1` also produces 401. **Diagnostic**: test the key against both endpoints directly: `curl -s https://api.deepseek.com/v1/models -H "Authorization: Bearer $KEY"` and `curl -s https://api.openai.com/v1/models -H "Authorization: Bearer $KEY"`. Whichever returns `"message": "OK"` is the correct provider for that key. Then ensure `NEXTSTEP_BASE_URL` matches. This was the root cause of a persistent 401 that survived multiple bot restarts — Telegram was fine (200 OK on `getMe`, `getUpdates`) but every AI call failed because the OpenAI key was pointed at DeepSeek's endpoint.
- **Dual API key support — `DEEPSEEK_API_KEY` and `NEXTSTEP_API_KEY` both work**: The bot checks `os.environ.get("NEXTSTEP_API_KEY")` first, then falls back to `os.environ.get("DEEPSEEK_API_KEY")`. Either env var name works. Jamie uses `DEEPSEEK_API_KEY`; Sage/Becca uses `NEXTSTEP_API_KEY`. This is NOT an inconsistency — it's by design. When setting up a new bot instance, pick one and be consistent. Don't set both.
- **Multi-instance bot restart hygiene**: When killing + restarting a bot, ensure ALL old instances are dead first. Use `pkill -9 -f "next-step-{user}"` (broader than just `bot.py` — catches bash wrappers too) then verify with `ps aux | grep "next-step-{user}"`. A single lingering old process with the same Telegram token causes `telegram.error.Conflict: terminated by other getUpdates request` (HTTP 409) on the new instance. Multiple competing instances can cascade into 401 auth invalidations as Telegram's server cycles the token. **Always add `drop_pending_updates=True` to `app.run_polling()`** to prevent this class of error entirely: `app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)`. After a kill, wait 30-60 seconds for Telegram's servers to release the old long-poll connection before restarting.\n- **Systemd duplicate-service pitfall**: When a bot has been running for days via systemd and you create a NEW systemd service for the same bot (e.g., `becca-sage.service` replacing `next-step-becca.service`), the OLD service may still be enabled and active. Both services run the same `bot.py` with the same token — producing persistent 409 conflicts that survive every `pkill` because systemd respawns the old service. **Always check for ALL systemd services using the bot token**: `systemctl list-units --all | grep -E "becca|sage|next-step"`. Stop + disable old services BEFORE starting new ones. This was the root cause of a 40-minute debugging session where `ps aux` showed only one process but a second was respawning from the old systemd unit every few seconds.\n- **`terminal(background=true)` spawns bash wrappers that survive kills**: Each `terminal(background=true)` invocation creates a bash wrapper process that spawns the actual Python process. When you `pkill -f "bot.py"`, the Python processes die but the bash wrappers linger and may respawn them. For long-lived services, ALWAYS use systemd instead of `terminal(background=true)`. Systemd provides clean lifecycle management (stop/restart/enable/disable) and prevents the zombie-process cascade. See `references/bot-debugging-409-conflict.md` for the full diagnostic workflow.
- **HD MCP model suitability**: For standalone bots that call HD MCP tools (chart calculation, transits, relationship composites), `deepseek-chat` (deepseek-v4-flash) is fully adequate. The MCP server does the Swiss Ephemeris computation — the LLM only handles tool calling, natural language parsing, and output formatting. A 1M context window easily holds full chart data. No need for a more expensive model unless doing deep multi-chart interpretive synthesis.
- **MCP wiring for standalone bots**: The standalone bot uses `NEXTSTEP_MCP_SRC` env var to import the engine directly (e.g., `sys.path.insert(0, NEXTSTEP_MCP_SRC)` then `from cosmic_calculator import calculate_natal_chart`). This is DIFFERENT from the Hermes profile `mcp_servers` block. The standalone bot does NOT use the Hermes MCP transport — it imports the engine as a Python library. Both paths exist: the profile config wires HD MCP for Hermes gateway profiles; the `.env` `NEXTSTEP_MCP_SRC` wires it for standalone bots. They can and should both be configured.\n- **System-wide MCP deps required**: The standalone bot runs as `ubuntu` user via systemd — NOT in the Hermes orchestrator's sandboxed user site-packages. `pyswisseph` and `mcp` MUST be installed system-wide (`sudo pip install --break-system-packages pyswisseph mcp`). See `references/standalone-bot-mcp-system-deps.md` for the full diagnostic + fix. This was the root cause of Jamie failing to load HD skills for weeks.
- **ALLOWED_CHAT_IDS security**: Add `ALLOWED_CHAT_IDS=<chat_id>` to `.env` to restrict bot access to specific Telegram users. The bot checks `update.effective_chat.id` against this comma-separated list in message handlers. Without it, anyone who discovers the bot username can interact with it.
- **`OHDMCP_FAMILY_JSON` is separate from `NEXTSTEP_FAMILY_PATH` — both required for HD**: The bot code uses `NEXTSTEP_FAMILY_PATH` (for `_get_active_birth()` fallback in the tool handler). But the MCP server's `get_deep_context()` and `get_relationship_composite()` use `OHDMCP_FAMILY_JSON` (via `_load_family_profiles()` at mcp_server.py:344). These are TWO DIFFERENT env vars that should point to the SAME file. Without `OHDMCP_FAMILY_JSON`, HD queries return `"Profile 'becca' not found. Available: []"` — a silent failure because the bot starts fine, just can't do profile lookups through the MCP server path. The bot's own fallback (`_get_active_birth()` + direct calculation) still works for the active profile, but relationship reports, cross-profile lookups, and rich transit context fail. **Always verify**: `cat /proc/<PID>/environ | tr '\\0' '\\n' | grep OHDMCP` — if empty, the bot is HD-blind for non-active profiles.

- **❌ Angle-bracket placeholder syntax in SOUL.md tool examples — models copy them LITERALLY (Jun 2026):** SOUL.md files define tools with placeholder syntax like `[TOOL:chart:<profile>]`. Some models (deepseek-chat, confirmed Jun 2026) copy the angle-bracket placeholders VERBATIM — outputting `[TOOL:chart:<profile>]` instead of substituting the actual profile name. The tool parser receives `<profile>` as the profile argument, lookup fails, and the model falls back to manual estimation (wrong answers). **Two-part fix:** (1) Code: strip `<>` from parsed tool arguments in `_execute_tool()` — `args = [a.strip("<>") for a in args]` — so even literal placeholders resolve. (2) SOUL.md: use REAL profile names in examples (`[TOOL:chart:michael]`) with explicit instruction: "use these EXACTLY, without angle brackets." Apply both — the code fix is defense-in-depth, the SOUL.md fix addresses the root cause. **Diagnostic**: check bot logs for `Jamie requested tool: chart args=['<profile>']` — the literal `<profile>` is the smoking gun. **Test fix**: `python3 -c "import re; m=re.match(r'\[TOOL:(\w+):?(.*?)\]', '[TOOL:chart:<profile>]'); print([a.strip('<>') for a in [x.strip() for x in m.group(2).split(',') if x.strip()] if m.group(2) else []])"` should output `['profile']`, not `['<profile>']`.

- **Token owner identification via `getMe` API (Jun 2026):** When a bot returns HTTP 409 Conflict and you've killed all local processes but the error persists, the token may belong to a DIFFERENT bot entirely. Use the Telegram `getMe` API to identify the actual bot: `curl -s \"https://api.telegram.org/bot<TOKEN>/getMe\"`. The response reveals the bot's `first_name` and `username`. If the name doesn't match the bot you think it is, the token was copied from another bot (e.g., both Autobot and Jeff had the same token). Fix: create a new bot via @BotFather and assign a unique token to each instance.\n- **Swarm notification routing pitfall (Autobot lesson, Jun 2026):** Standalone bots that post automated notifications (SSE feeds, swarm events, dispatch cycles) must use a DEDICATED bot token — not shared with the main chat bot. When `autobot.py` posted to `TELEGRAM_CHAT_ID=8190664947` with a misconfigured token, every agent launch/completion/stall appeared in Jamie's chat. The fix: dedicated @Autob0tautob0t_bot token in the service file's `Environment=`. The bot can only DM a user after `/start` — until then, 403 Forbidden errors are normal and messages queue silently.

- **`swisseph` not `pyswisseph` — HD MCP import pitfall (Jun 2026):** The pip package is `pyswisseph` but the Python import is `swisseph`. The HD MCP server uses `import swisseph as swe` — not `import pyswisseph`. The package installs as `swisseph.cpython-312-x86_64-linux-gnu.so` in dist-packages. Install: `sudo pip install --break-system-packages pyswisseph`. Verify: `python3 -c "import swisseph; print('OK')"`. Without this, all HD chart calculations fail silently.

- **Multi-bot token identity confusion (Jun 2026):** When multiple bots share the same bot ID (e.g., `8983301978`) with different hash suffixes, tools that read `.env` files can silently pick the wrong token. Autobot (`8842175068`), Jeff (`8983301978`), and the shared `next-step-bot/.env` all have different tokens. When writing scripts that need to send Telegram messages, NEVER assume `$PRISMATIC_HOME/work/next-step-bot/.env` is the right source — always use the bot's own profile `.env` at `$PRISMATIC_HOME/.hermes/profiles/<profile>/.env`.
