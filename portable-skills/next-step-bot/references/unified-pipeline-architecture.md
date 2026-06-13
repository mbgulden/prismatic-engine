---
name: unified-agent-conversation-pipeline
description: Build AI chat agents with a single unified conversation pipeline instead of rigid classifier-based routing. Covers single-system-prompt architecture, conversation history, tool-detection loops, and natural multi-domain fluidity.
triggers:
  - building or refactoring a Telegram/Discord/Slack chat bot powered by an LLM API
  - user complains their bot feels "fragmented," "schizophrenic," or "switches modes"
  - chatbot architecture uses classifier → route → handler pattern and needs unification
  - embedding multiple domains (task coaching + domain expertise like Human Design, finance, health) into one assistant
  - user says "I want it to flow between X and Y naturally"
---

# Unified Agent Conversation Pipeline

Replace rigid classifier-based message routing with a single conversation flow where the agent organically decides how to respond. One system prompt, one personality, one brain.

## The Anti-Pattern (What to Avoid)

```
User message → Classifier (separate LLM call) → 
  "task_dump" → Parse prompt (separate LLM call) → Task handler
  "chatter" → Chatter prompt (separate LLM call) → Chatter handler  
  "birth_query" → Birth handler (MCP call)
  "relationship_query" → Relationship handler
```

**Why it fails:**
- Each LLM call is stateless — no memory of what just happened
- Domain expertise (e.g., Human Design) lives in completely separate code paths
- The agent literally IS multiple bots sharing one Telegram handle
- User feels the "mode switch" because there IS a mode switch

## The Pattern (What to Build Instead)

```
User message → 
  1. Load SOUL.md (single system prompt: persona + tools + coaching style)
  2. Inject current state (active profile, current task, date, family list)
  3. Append conversation history (last N turns from SQLite)
  4. Append user message
  5. Call LLM (single call per turn, no classifier step)
  6. Check response for [TOOL:name:args] → execute → inject → call LLM again
  7. Save exchange to history
  8. Send response
```

## Core Components

### 1. The Soul File (SOUL.md)

A single markdown file loaded at startup. Contains:

- **Identity**: who the assistant is, name, basic personality
- **Core philosophy**: how the assistant thinks about the user holistically — not separate domains
- **User profile** (internal, never recited): HD type, communication preferences, coaching style
- **Tool declarations** with format and usage rules: `[TOOL:name:arg1,arg2]`
- **Prime directives**: behavioral rules like "never show the full task list"
- **Core behaviors**: task ingestion, micro-scoping, celebration, persistence
- **Natural domain integration**: how domain knowledge shapes coaching without "switching modes"
- **Tone guide**: personality rules that apply regardless of what's being discussed
- **Edge cases**: empty queue, overwhelmed user, stuck user

Key principle: **"Never say 'as a Projector' — just do it."** Domain knowledge should shape behavior invisibly, not be announced.

### 2. Conversation History

```sql
CREATE TABLE conversation_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL,        -- 'user' or 'assistant'
    content TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
```

- Keep last 20 exchanges per user (configurable)
- Prune on every save: delete oldest beyond limit
- Clear on /start for fresh sessions
- Inject between system prompt and current user message

### 3. Tool Detection Loop

The agent outputs `[TOOL:name:arg1,arg2]` on its own line when it needs data:

```
[TOOL:deep_context:michael]           — Full chart + transits + variables
[TOOL:deep_context:michael,becca]     — Same + relationship composite
[TOOL:transits:michael]               — Current transit conditioning only
[TOOL:relate:michael,becca]           — Relationship composite only
[TOOL:map:michael]                    — Astrocartography
[TOOL:list]                           — Pending tasks
[TOOL:done]                           — Mark task complete, get next
```

Implementation:
```python
while "[TOOL:" in response and loop_guard < 4:
    # Extract first tool line
    tool_line = response[tool_start:tool_end]
    # Execute tool → get result
    tool_result = execute_tool(tool_line, user_id, name)
    # Inject into conversation
    messages.append({"role": "assistant", "content": response})
    messages.append({"role": "user", "content": f"[Tool result]\n{tool_result}"})
    # Call LLM again with full context
    response = call_llm(messages, max_tokens=600)
```

### 4. Fast-Paths for Common Commands

Some commands are so common they shouldn't need an LLM roundtrip:

```python
# Before the unified pipeline, check for:
if text in ["/list", "list"]:
    return format_task_list(get_all_pending(conn, user_id))
if text in ["/status", "status"]:
    return format_current_task(conn, user_id)
if looks_like_done(text):
    next_task = complete_current(conn, user_id)
    if not next_task:
        return "🎉 Queue cleared!"
    # else: fall through to unified pipeline for celebration
```

### 5. State Injection into System Prompt

Append live state to the system prompt each turn:

```python
state_context = f"""
[CURRENT STATE]
Active profile: {active_profile} ({display_name})
Current task: {current_task_desc or "No current task. Queue is empty."}
Family profiles available: {', '.join(family_keys)}
Today is {datetime.now().strftime('%A, %B %d %Y, %H:%M UTC')}
"""
messages = [{"role": "system", "content": soul + state_context}]
```

### 6. Background Task Management

The unified pipeline handles tasks implicitly. Don't route "done" to a separate handler — let Jamie celebrate naturally within the conversation:

```python
# After Jamie responds, check if message looked like a dump:
if looks_like_task_dump(text):
    tasks = extract_tasks_from_text(text)
    save_tasks_to_db(conn, user_id, tasks)

# For "done" → Jamie naturally celebrates + transitions
# The done signal updates DB state, Jamie's system prompt shows the new task
```

## Pitfalls

- **Double-classifying**: don't classify AND use unified pipeline. If Jamie decides what to do, the bot shouldn't pre-classify.
- **Duplicate done handling**: if you handle "done" in a fast-path AND in post-processing, tasks get completed twice.
- **async/sync confusion**: LLM API calls are sync but handlers are async. Either use `asyncio.to_thread()` or accept blocking in the handler (Telegram's python-telegram-bot tolerates this).
- **Tool loop infinite recursion**: always cap the tool loop (max 4 iterations). Never let a tool response trigger another tool request from Jamie without a guard.
- **History bloat**: that 20-turn limit matters. Without pruning, DeepSeek calls grow expensive and conversation degrades.
- **SOUL.md not read at startup**: if SOUL.md is loaded lazily, the first message uses a fallback prompt. Load it eagerly.
- **Missing API keys**: the systemd EnvironmentFile must include LLM API keys (DEEPSEEK_API_KEY, OPENAI_API_KEY, etc.), not just the bot token. The "no AI" fallback silently produces terrible responses.

## The Long Game: Belief Work & Deconditioning Integration

A unified pipeline doesn't just coach tasks — it can actively work toward user independence. The **deconditioning pattern** uses domain data (Human Design, CBT frameworks, habit tracking) to identify when the user is operating from conditioning rather than design, and celebrates evidence of growth.

### Philosophy

Every intervention should make the NEXT intervention less necessary. The agent is a coach, not a dependency. Frame everything as: "Here's the pattern. Here's the experiment. Let's see what happens. Over time, you'll catch this yourself."

### Integration Points

| Pipeline Stage | Belief-Work Hook |
|---|---|
| State injection | Include open-center conditioning signals from transit data |
| After deep_context fetch | Scan for Not-Self themes matching current user language |
| Task dump processing | Before scoping, check for open-center overcommit patterns |
| "Feeling stuck" messages | Check transits for active conditioning before offering task help |
| Task completion | Check if user caught a pattern without prompting → celebrate growth |
| Periodic check-in | Track: "Three weeks ago this frozen you. Today you handled it." |

### What the SOUL.md Needs

```markdown
## The Long Game: Belief Work & Deconditioning
Your ultimate purpose is not to be a permanent crutch — it's to help the user grow
out of needing one.

- When they dump tasks: scan for open-center overcommit signals
- When they feel stuck: check transits for conditioning
- When they say "I don't know why I keep doing this": connect to mechanics without jargon
- When they handle something without you: "You caught that before I could flag it. That's growth."
```

### Skills That Implement This

- `skills/read-hd-context.md` — Silent HD data injection with jargon→human translation
- `skills/deconditioning-coach.md` — Open-center surveillance, Not-Self detection, electromagnetic spark detection, growth tracking

Both follow the agentskills.io standard (YAML frontmatter + markdown body) and are portable across any Hermes profile.

## Architecture Extensibility: Leave Hooks for Future Channels

When building a text-bot pipeline, don't close the door to future input channels (voice, screen sharing, desktop daemons). The standalone bot pattern naturally supports this:

```python
# Today: Telegram text messages
async def handle_telegram_message(update, context):
    text = update.message.text
    await unified_pipeline(text, user_id, name)

# Tomorrow: voice message → STT → same pipeline
async def handle_voice_message(update, context):
    audio = await update.message.voice.get_file()
    text = transcribe(audio)        # ← new input channel
    await unified_pipeline(text, user_id, name)  # ← same pipeline

# Tomorrow: screen daemon → MCP → same pipeline
def screen_daemon_callback(screenshot_context):
    text = analyze_screen(screenshot_context)
    # Feed into the same unified pipeline via MCP tool
```

The key: the unified pipeline (`unified_pipeline(text, user_id, name)`) doesn't care where the text came from. Keep it that way.

## Making the Pipeline Portable (Capability Package)

To make the bot installable as a self-serve plugin (not managed multi-tenancy), every path and credential must come from environment variables. Zero hardcoded paths.

### Portable Config Pattern

```python
# All configurable via env vars with sensible defaults relative to bot.py
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is required")

MCP_SRC = os.environ.get(
    "NEXTSTEP_MCP_SRC",
    str(Path(__file__).parent / "mcp-server" / "src")
)
FAMILY_PATH = Path(os.environ.get(
    "NEXTSTEP_FAMILY_PATH",
    str(Path(__file__).parent / "family.json")
))
DB_PATH = Path(os.environ.get(
    "NEXTSTEP_DB_PATH",
    str(Path(__file__).parent / "data" / "next_step.db")
))
ASSISTANT_NAME = os.environ.get("NEXTSTEP_NAME", "Jamie")
INSTANCE_PROFILE = os.environ.get("NEXTSTEP_PROFILE", "next-step")
```

### Lazy MCP Imports

Don't `sys.path.insert` at module load — do it lazily when tools are first used:

```python
_mcp_path_added = False
def _ensure_mcp_path():
    global _mcp_path_added
    if not _mcp_path_added and Path(MCP_SRC).is_dir():
        sys.path.insert(0, MCP_SRC)
        _mcp_path_added = True
```

### Capability Package Structure

```
next-step-capability-package/
├── install.sh                  # One-command interactive installer
├── bot.py                      # Portable bot (same code, env-driven)
├── SOUL.md                     # Main persona (belief-work + skills ref)
├── souls/jamie.md              # Persona soul with full deconditioning framework
├── family.json.template        # Starter birth data template
├── Dockerfile                  # Container deployment
├── docker-compose.yml          # Multi-instance (Michael + Becca side by side)
├── hermes-profile/
│   └── config.yaml.template    # Hermes-native profile template
├── skills/
│   ├── task-atomicizer.md      # Task micro-scoping ("hide the mountain")
│   ├── read-hd-context.md      # Silent HD injection (agentskills.io)
│   └── deconditioning-coach.md # Belief work + growth tracking
└── README.md                   # Docs for all 3 deployment paths
```

### Deployment Paths

1. **install.sh**: Walks user through token/API key config, sets up systemd or Docker
2. **Docker**: `docker run -d -e TELEGRAM_BOT_TOKEN=xxx -e DEEPSEEK_API_KEY=yyy next-step-bot`
3. **Hermes-native**: `hermes profile create next-step` + drop SOUL.md and skills/ + `hermes gateway install`

Key principle: **Self-serve plugin, not managed multi-tenancy.** The user hosts their own bot with their own tokens. The package provides the engine; the user owns the instance.

### Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | — | Bot token from @BotFather |
| `DEEPSEEK_API_KEY` | Yes | — | DeepSeek API key |
| `NEXTSTEP_MCP_SRC` | No | `./mcp-server/src` | Path to MCP server |
| `NEXTSTEP_DB_PATH` | No | `./data/next_step.db` | SQLite database |
| `NEXTSTEP_NAME` | No | `Jamie` | Assistant display name |
| `NEXTSTEP_PROFILE` | No | `next-step` | Instance ID for logs |

See `references/portable-capability-package-pattern.md` for the full packaging walkthrough.

## Verification

After conversion:
1. Send a message that mixes two domains (task + chart question) in one message
2. Verify Jamie responds to BOTH naturally without announcing a mode switch
3. Verify conversation history persists: "Jamie, remember what we talked about?" → she references earlier exchange
4. Verify tool loop: ask about transits → Jamie requests [TOOL:transits] → bot executes → Jamie synthesizes
5. Verify fast-paths: /list, /status, "done" respond instantly (no AI cost)

## References

- `references/jamie-bot-py-architecture.md` — Full annotated walkthrough of the unified pipeline implementation in bot.py, with the before/after architecture comparison and the specific patterns extracted from this refactor.
- `references/belief-work-deconditioning-architecture.md` — How to integrate belief-work/deconditioning coaching into a unified pipeline: scan dimensions, tone rules, pitfall avoidance, and why this works better as system-prompt rules than hardcoded logic.
- `hermes-agent-profiles-and-swarms/references/portable-capability-package-pattern.md` — How to package the pipeline as a self-serve installable plugin (install.sh, Docker, Hermes profile template).
