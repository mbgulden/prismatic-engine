"""
Prismatic Fleet Telegram Streaming Engine.

Coordinates multi-bot progressive edit streaming to prevent Telegram API flood limits:
1. Dynamic Telegram Throttler: dynamically scales edit cadences across multiple bots
   streaming to the same chat (0.8s for 1 bot, 1.6s for 2 bots, 2.5s for 3+ bots).
2. HTTP 429 Flood Recovery: detects Telegram rate limits, parses `parameters.retry_after`,
   and cleanly backs off (retry_after + 0.5s) without crashing agent loops.
3. Daemon Collision Prevention: prevents HTTP 409 Conflict crash loops when launching
   interactive CLI chat sessions while a systemd gateway service is polling Telegram.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import httpx

logger = logging.getLogger("prismatic.fleet.telegram")


class DynamicTelegramThrottler:
    """Coordinates edit cadences across multiple bots streaming to the same chat.

    Dynamically scales edit interval based on concurrent streamer count:
    - 0 or 1 active bot: 0.8s (smooth single-bot typing)
    - 2 active bots:     1.6s (~1.25 aggregate edits/sec)
    - 3+ active bots:    2.5s (<1.5 aggregate edits/sec, safely under Telegram limits)
    """

    _active_streamers: Set[str] = set()
    _lock = asyncio.Lock()
    _sync_lock = threading.Lock()
    _last_edit_ts: Dict[str, float] = {}

    @classmethod
    async def register_active(cls, bot_id: str) -> None:
        """Register an active streamer asynchronously."""
        async with cls._lock:
            cls._active_streamers.add(str(bot_id))

    @classmethod
    async def unregister_active(cls, bot_id: str) -> None:
        """Unregister an active streamer asynchronously."""
        async with cls._lock:
            cls._active_streamers.discard(str(bot_id))

    @classmethod
    def register_active_sync(cls, bot_id: str) -> None:
        """Register an active streamer synchronously."""
        with cls._sync_lock:
            cls._active_streamers.add(str(bot_id))

    @classmethod
    def unregister_active_sync(cls, bot_id: str) -> None:
        """Unregister an active streamer synchronously."""
        with cls._sync_lock:
            cls._active_streamers.discard(str(bot_id))

    @classmethod
    def get_cadence_seconds(cls) -> float:
        """Dynamically scale edit interval based on concurrent streamer count."""
        count = len(cls._active_streamers)
        if count <= 1:
            return 0.8  # Smooth single-bot typing
        elif count == 2:
            return 1.6  # 2 bots -> aggregate 1.25 edits/sec
        else:
            return 2.5  # 3+ bots -> aggregate safe under 1.5 edits/sec

    @classmethod
    def get_active_count(cls) -> int:
        """Return the number of currently registered active streamers."""
        return len(cls._active_streamers)

    @classmethod
    def get_active_streamers(cls) -> List[str]:
        """Return a sorted list of registered active streamer identifiers."""
        return sorted(list(cls._active_streamers))

    @classmethod
    def reset(cls) -> None:
        """Reset all active streamer state (useful for test isolation)."""
        with cls._sync_lock:
            cls._active_streamers.clear()
            cls._last_edit_ts.clear()

    @classmethod
    def active(cls, bot_id: str) -> _ThrottlerActiveContext:
        """Context manager supporting both synchronous and asynchronous execution."""
        return _ThrottlerActiveContext(cls, bot_id)


class _ThrottlerActiveContext:
    """Context manager for DynamicTelegramThrottler registration."""

    def __init__(self, throttler_cls: type[DynamicTelegramThrottler], bot_id: str):
        self.throttler_cls = throttler_cls
        self.bot_id = str(bot_id)

    def __enter__(self) -> _ThrottlerActiveContext:
        self.throttler_cls.register_active_sync(self.bot_id)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.throttler_cls.unregister_active_sync(self.bot_id)

    async def __aenter__(self) -> _ThrottlerActiveContext:
        await self.throttler_cls.register_active(self.bot_id)
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.throttler_cls.unregister_active(self.bot_id)


class TelegramStreamer:
    """Streams live progressive updates to Telegram via message edits with 429 recovery."""

    def __init__(
        self,
        token: str,
        chat_id: str | int,
        prefix: str,
        bot_id: Optional[str] = None,
        client: Optional[httpx.Client] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self.token = token
        self.chat_id = str(chat_id)
        self.prefix = prefix
        self.bot_id = bot_id or (token.split(":")[0] if ":" in token else prefix)
        self.message_id: Optional[int] = None
        self.lines: List[str] = []
        self._client = client
        self._owns_client = client is None
        self._sleep_fn = sleep_fn
        self._last_edit_time: float = 0.0

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=5.0)
            self._owns_client = True
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def __enter__(self) -> TelegramStreamer:
        DynamicTelegramThrottler.register_active_sync(self.bot_id)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        DynamicTelegramThrottler.unregister_active_sync(self.bot_id)
        self.close()

    async def __aenter__(self) -> TelegramStreamer:
        await DynamicTelegramThrottler.register_active(self.bot_id)
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await DynamicTelegramThrottler.unregister_active(self.bot_id)
        self.close()

    def start(self, initial_text: str) -> Optional[int]:
        """Send the initial message to Telegram to acquire a message_id."""
        self.lines = [initial_text]
        text = f"{self.prefix}\n" + "\n".join(self.lines)
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        client = self._get_client()
        try:
            resp = client.post(
                url,
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"},
            )
            if resp.status_code == 200:
                data = resp.json()
                self.message_id = data.get("result", {}).get("message_id")
                return self.message_id
            else:
                logger.warning(
                    "[Telegram] Failed to start stream for %s: HTTP %s",
                    self.prefix,
                    resp.status_code,
                )
        except Exception as e:
            logger.error("[Telegram] Exception starting stream for %s: %s", self.prefix, e)
        return None

    def edit_message(
        self, text: Optional[str] = None, max_retries: int = 2
    ) -> Optional[Dict[str, Any]]:
        """Edit the streaming message with HTTP 429 backoff and recovery."""
        if not self.message_id:
            return None

        if text is None:
            text = f"{self.prefix}\n" + "\n".join(self.lines)

        url = f"https://api.telegram.org/bot{self.token}/editMessageText"
        payload = {
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        client = self._get_client()

        for attempt in range(max_retries + 1):
            try:
                resp = client.post(url, json=payload)
                if resp.status_code == 429:
                    retry_after = 3.0
                    try:
                        resp_json = resp.json()
                        params = resp_json.get("parameters", {})
                        retry_after = float(params.get("retry_after", 3.0))
                    except Exception:
                        pass
                    backoff = retry_after + 0.5
                    logger.warning(
                        "[Telegram] HTTP 429 Too Many Requests for %s; backing off %.1fs (attempt %d/%d)",
                        self.prefix,
                        backoff,
                        attempt + 1,
                        max_retries + 1,
                    )
                    self._sleep_fn(backoff)
                    continue

                resp.raise_for_status()
                self._last_edit_time = time.time()
                try:
                    return resp.json()
                except Exception:
                    return {"ok": True}

            except httpx.HTTPStatusError as exc:
                if exc.response is not None and exc.response.status_code == 429:
                    retry_after = 3.0
                    try:
                        resp_json = exc.response.json()
                        params = resp_json.get("parameters", {})
                        retry_after = float(params.get("retry_after", 3.0))
                    except Exception:
                        pass
                    backoff = retry_after + 0.5
                    logger.warning(
                        "[Telegram] HTTP 429 StatusError for %s; backing off %.1fs (attempt %d/%d)",
                        self.prefix,
                        backoff,
                        attempt + 1,
                        max_retries + 1,
                    )
                    self._sleep_fn(backoff)
                    continue

                logger.error("[Telegram] HTTPStatusError editing message for %s: %s", self.prefix, exc)
                return None

            except Exception as e:
                logger.error("[Telegram] Unexpected edit error for %s: %s", self.prefix, e)
                return None

        return None

    def update(
        self, new_line: str, delay_s: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """Append a new line, sleep according to dynamic throttler cadence, and edit message."""
        self.lines.append(new_line)
        if not self.message_id:
            return None

        if delay_s is None:
            delay_s = DynamicTelegramThrottler.get_cadence_seconds()

        if delay_s > 0:
            self._sleep_fn(delay_s)

        return self.edit_message()


def check_hermes_daemon_collision(profile: str) -> Tuple[bool, str]:
    """Check whether a systemd gateway service is currently active for the profile.

    Returns:
        (is_active: bool, service_name: str)
    """
    service_name = f"hermes-gateway@{profile}.service"
    legacy_candidates = [
        service_name,
        f"hermes-{profile}-gateway.service",
        f"hermes-gateway-{profile}.service",
    ]
    if profile == "orchestrator":
        legacy_candidates.insert(0, "hermes-orchestrator-gateway.service")

    for svc in legacy_candidates:
        try:
            res = subprocess.run(
                ["systemctl", "is-active", svc],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip() == "active":
                return True, svc
        except Exception:
            pass

    return False, service_name


@contextlib.contextmanager
def daemon_collision_guard(profile: str, auto_pause: bool = True):
    """Guard against Telegram HTTP 409 Conflict polling collisions.

    If the systemd gateway service for the profile is active:
      - If auto_pause is True: stop the service before entering, restart in finally block.
      - If auto_pause is False: print a warning to stderr.
    """
    is_active, svc = check_hermes_daemon_collision(profile)
    stopped_by_guard = False

    if is_active:
        if auto_pause:
            print(
                f"[COLLISION GUARD] Active daemon detected: {svc} is polling Telegram.",
                file=sys.stderr,
            )
            print(
                f"[COLLISION GUARD] Pausing {svc} to prevent Telegram HTTP 409 Conflict crash loops...",
                file=sys.stderr,
            )
            # Try sudo systemctl stop, fallback to systemctl stop
            res = subprocess.run(["sudo", "systemctl", "stop", svc], capture_output=True, text=True)
            if res.returncode != 0:
                res = subprocess.run(["systemctl", "stop", svc], capture_output=True, text=True)
            stopped_by_guard = (res.returncode == 0)
            if stopped_by_guard:
                print(f"[COLLISION GUARD] Successfully paused {svc}.", file=sys.stderr)
            else:
                print(
                    f"[COLLISION GUARD] Warning: could not pause {svc} ({res.stderr.strip()}). "
                    "Proceeding with caution.",
                    file=sys.stderr,
                )
        else:
            print(
                f"[WARNING] {svc} is currently active and polling Telegram. "
                "Running an interactive session concurrently will trigger Telegram HTTP 409 Conflict crash loops!",
                file=sys.stderr,
            )

    try:
        yield is_active, svc, stopped_by_guard
    finally:
        if stopped_by_guard:
            print(f"[COLLISION GUARD] Restoring {svc} daemon...", file=sys.stderr)
            res = subprocess.run(["sudo", "systemctl", "start", svc], capture_output=True, text=True)
            if res.returncode != 0:
                subprocess.run(["systemctl", "start", svc], capture_output=True, text=True)
            print(f"[COLLISION GUARD] {svc} restored successfully.", file=sys.stderr)


def run_hermes_chat_with_guard(
    profile: str = "default",
    extra_args: Optional[List[str]] = None,
    auto_pause: bool = True,
    hermes_bin: Optional[str] = None,
) -> int:
    """Execute hermes chat session wrapped in the daemon collision guard."""
    if hermes_bin is None:
        hermes_bin = shutil.which("hermes") or "/home/ubuntu/.local/bin/hermes"

    with daemon_collision_guard(profile, auto_pause=auto_pause):
        cmd = [hermes_bin, "chat"]
        env = dict(os.environ)
        if profile and profile != "default":
            profile_dir = os.path.expanduser(f"~/.hermes/profiles/{profile}")
            if os.path.isdir(profile_dir):
                env["HERMES_HOME"] = profile_dir
        if extra_args:
            cmd.extend(extra_args)

        try:
            proc = subprocess.run(cmd, env=env)
            return proc.returncode
        except Exception as e:
            print(f"[COLLISION GUARD] Failed to run hermes chat: {e}", file=sys.stderr)
            return 1
