#!/usr/bin/env python3
"""
<NAME> — SSE-to-Telegram bridge for <PURPOSE>.

Connects to <EVENT_SOURCE_URL>, parses incoming events,
formats them as Telegram messages, and pushes to <CHAT_ID>.

Usage:
  ./<name>.py [--feed-url URL] [--chat-id ID] [--no-banner]

Environment:
  TELEGRAM_BOT_TOKEN   (required)  Telegram bot token
  TELEGRAM_CHAT_ID     (optional)  Override chat ID
  EVENT_FEED_URL       (optional)  SSE feed URL
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone

import httpx

logger = logging.getLogger("<NAME>")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

# ── Dotenv Loading ──────────────────────────────────────────

def _load_dotenv():
    """Load environment from .env files if present."""
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        # Add known .env paths here
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                with open(path) as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        key, _, value = line.partition("=")
                        key, value = key.strip(), value.strip()
                        if key and value and key not in os.environ:
                            os.environ[key] = value
                logger.info(f"Loaded .env from {path}")
                break
            except OSError:
                pass

_load_dotenv()

# ── Configuration ───────────────────────────────────────────

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = int(os.environ.get("TELEGRAM_CHAT_ID", "0"))
FEED_URL = os.environ.get("EVENT_FEED_URL", "http://127.0.0.1:8098/api/events/stream")
TELEGRAM_API = "https://api.telegram.org"

INITIAL_BACKOFF = 1.0
MAX_BACKOFF = 60.0
BACKOFF_MULTIPLIER = 2.0

# ── Telegram Sender ─────────────────────────────────────────

async def send_telegram(text: str) -> bool:
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        return False
    url = f"{TELEGRAM_API}/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            logger.warning(f"Telegram API returned {resp.status_code}: {resp.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"Failed to send Telegram: {e}")
        return False

# ── HTML Escape ─────────────────────────────────────────────

def html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# ── Event Formatting ────────────────────────────────────────

def format_event(event: dict) -> str | None:
    """Format an event for Telegram. Return None to skip."""
    event_type = event.get("event", "unknown")
    agent = html_escape(event.get("agent_name", "unknown"))
    issue_id = html_escape(event.get("issue_id", ""))
    title = html_escape(event.get("title", ""))
    message = html_escape(event.get("message", ""))
    timestamp = event.get("timestamp", "")

    try:
        dt = datetime.fromisoformat(timestamp)
        time_str = dt.strftime("%H:%M:%S")
    except (ValueError, TypeError):
        time_str = ""

    # TODO: Customize formatting per event type
    if event_type == "launched":
        issue_str = f" <code>[{issue_id}]</code>" if issue_id else ""
        return f"🚀 <b>Launched</b> <i>{agent}</i>{issue_str} {title}\n   <i>{time_str}</i>"

    elif event_type == "completed":
        issue_str = f" <code>[{issue_id}]</code>" if issue_id else ""
        return f"✅ <b>Completed</b> <i>{agent}</i>{issue_str} {title}\n   <i>{time_str}</i>"

    elif event_type == "error":
        issue_str = f" <code>[{issue_id}]</code>" if issue_id else ""
        msg = f"❌ <b>Error</b> <i>{agent}</i>{issue_str} {title}\n   <i>{time_str}</i>"
        if message:
            msg += f"\n   <code>{message[:200]}</code>"
        return msg

    # Add more event types as needed...

    else:
        logger.debug(f"Unknown event type: {event_type}")
        return f"📢 <b>{html_escape(event_type)}</b> <i>{agent}</i>\n   <i>{time_str}</i>"

# ── SSE Reader ──────────────────────────────────────────────

SSE_LINE_RE = re.compile(r"^(event|data|id|retry):\s*(.*)$")

async def sse_reader(client: httpx.AsyncClient, url: str):
    """Async generator yielding parsed events from an SSE stream."""
    buffer = ""
    event_type = None

    async with client.stream("GET", url, timeout=None) as response:
        if response.status_code != 200:
            raise ConnectionError(f"SSE feed returned {response.status_code}")

        async for chunk in response.aiter_text():
            buffer += chunk
            while "\n\n" in buffer:
                raw_event, buffer = buffer.split("\n\n", 1)
                data_str = ""
                for line in raw_event.split("\n"):
                    if line.startswith(":"):
                        continue
                    match = SSE_LINE_RE.match(line)
                    if match:
                        field, value = match.groups()
                        if field == "event":
                            event_type = value
                        elif field == "data":
                            data_str += value
                if data_str:
                    try:
                        data = json.loads(data_str)
                        if event_type:
                            data.setdefault("event", event_type)
                        yield data
                    except json.JSONDecodeError:
                        logger.debug(f"Failed to parse SSE data: {data_str[:100]}")

# ── Connection Loop ─────────────────────────────────────────

async def connect_and_stream(feed_url: str):
    backoff = INITIAL_BACKOFF
    while True:
        try:
            logger.info(f"Connecting to SSE feed: {feed_url}")
            async with httpx.AsyncClient(timeout=30.0) as client:
                event_count = 0
                async for event in sse_reader(client, feed_url):
                    event_count += 1
                    formatted = format_event(event)
                    if formatted:
                        await send_telegram(formatted)
                    backoff = INITIAL_BACKOFF
                logger.info(f"Stream ended after {event_count} events. Reconnecting...")
        except (httpx.ConnectTimeout, httpx.NetworkError, httpx.ProtocolError,
                ConnectionError, OSError) as e:
            logger.warning(f"Connection failed: {e}. Reconnecting in {backoff:.1f}s...")
        except Exception as e:
            logger.error(f"Unexpected error: {e}. Reconnecting in {backoff:.1f}s...")
        await asyncio.sleep(backoff)
        backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF)

# ── Startup Banner ──────────────────────────────────────────

async def send_startup_banner():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    await send_telegram(
        f"🤖 <b><NAME> Online</b>\n"
        f"   Watching: <code>{FEED_URL}</code>\n"
        f"   <i>{now}</i>"
    )

# ── Entrypoint ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="<NAME> — SSE to Telegram bridge")
    parser.add_argument("--feed-url", type=str, default=FEED_URL)
    parser.add_argument("--chat-id", type=int, default=None)
    parser.add_argument("--no-banner", action="store_true")
    args = parser.parse_args()

    chat_id = args.chat_id if args.chat_id is not None else CHAT_ID
    feed_url = args.feed_url

    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is required")
        sys.exit(1)

    logger.info(f"<NAME> starting — chat_id={chat_id}, feed={feed_url}")

    import <name>
    <name>.CHAT_ID = chat_id

    asyncio.run(connect_and_stream(feed_url))

if __name__ == "__main__":
    main()
