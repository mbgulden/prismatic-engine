"""
Unit test suite for Telegram Multi-Bot Rate Limiting & Daemon Collision Prevention (Directive 04).

Verifies:
1. Dynamic cadence scaling: 0.8s for 1 bot, 1.6s for 2 bots, 2.5s for 3+ bots.
2. HTTP 429 flood recovery: parsing parameters.retry_after (default 3.0s) + 0.5s backoff without crashing.
3. Daemon collision prevention: detecting active systemd gateway units before CLI interactive sessions.
4. Auto-pause and safe restoration lifecycle for hermes-gateway@<profile>.service.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, call, patch
import pytest
import httpx

from prismatic.cli import (
    check_hermes_daemon_collision,
    daemon_collision_guard,
    run as cli_run,
    run_hermes_chat_with_guard,
)
from prismatic.fleet.telegram import (
    DynamicTelegramThrottler,
    TelegramStreamer,
)


@pytest.fixture(autouse=True)
def reset_throttler():
    """Ensure clean throttler state before and after every test."""
    DynamicTelegramThrottler.reset()
    yield
    DynamicTelegramThrottler.reset()


# ---------------------------------------------------------------------------
# 1. DYNAMIC CADENCE SCALING TESTS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_throttler_single_streamer_cadence():
    """Asserts cadence is 0.8s with a single registered streamer."""
    assert DynamicTelegramThrottler.get_cadence_seconds() == 0.8

    await DynamicTelegramThrottler.register_active("bot_fred")
    assert DynamicTelegramThrottler.get_active_count() == 1
    assert DynamicTelegramThrottler.get_cadence_seconds() == 0.8

    await DynamicTelegramThrottler.unregister_active("bot_fred")
    assert DynamicTelegramThrottler.get_active_count() == 0
    assert DynamicTelegramThrottler.get_cadence_seconds() == 0.8


@pytest.mark.asyncio
async def test_throttler_two_streamers_cadence():
    """Asserts cadence scales to 1.6s with 2 registered streamers."""
    await DynamicTelegramThrottler.register_active("bot_fred")
    await DynamicTelegramThrottler.register_active("bot_george")

    assert DynamicTelegramThrottler.get_active_count() == 2
    assert DynamicTelegramThrottler.get_cadence_seconds() == 1.6


@pytest.mark.asyncio
async def test_throttler_three_or_more_streamers_cadence():
    """Asserts cadence scales to >= 2.0s (specifically 2.5s) with 3+ streamers."""
    await DynamicTelegramThrottler.register_active("bot_fred")
    await DynamicTelegramThrottler.register_active("bot_george")
    await DynamicTelegramThrottler.register_active("bot_kai")

    assert DynamicTelegramThrottler.get_active_count() == 3
    cadence = DynamicTelegramThrottler.get_cadence_seconds()
    assert cadence >= 2.0
    assert cadence == 2.5

    # 4 streamers also remains protected at 2.5s
    await DynamicTelegramThrottler.register_active("bot_ned")
    assert DynamicTelegramThrottler.get_active_count() == 4
    cadence_4 = DynamicTelegramThrottler.get_cadence_seconds()
    assert cadence_4 >= 2.0
    assert cadence_4 == 2.5


def test_throttler_sync_context_manager():
    """Asserts synchronous context manager updates active count and cadence."""
    assert DynamicTelegramThrottler.get_cadence_seconds() == 0.8

    with DynamicTelegramThrottler.active("bot_sync_1"):
        assert DynamicTelegramThrottler.get_active_count() == 1
        assert DynamicTelegramThrottler.get_cadence_seconds() == 0.8

        with DynamicTelegramThrottler.active("bot_sync_2"):
            assert DynamicTelegramThrottler.get_active_count() == 2
            assert DynamicTelegramThrottler.get_cadence_seconds() == 1.6

            with DynamicTelegramThrottler.active("bot_sync_3"):
                assert DynamicTelegramThrottler.get_active_count() == 3
                assert DynamicTelegramThrottler.get_cadence_seconds() >= 2.0

        assert DynamicTelegramThrottler.get_active_count() == 1

    assert DynamicTelegramThrottler.get_active_count() == 0


@pytest.mark.asyncio
async def test_throttler_async_context_manager():
    """Asserts asynchronous context manager updates active count and cadence."""
    async with DynamicTelegramThrottler.active("bot_async_1"):
        assert DynamicTelegramThrottler.get_active_count() == 1
        async with DynamicTelegramThrottler.active("bot_async_2"):
            assert DynamicTelegramThrottler.get_active_count() == 2
            assert DynamicTelegramThrottler.get_cadence_seconds() == 1.6

    assert DynamicTelegramThrottler.get_active_count() == 0


# ---------------------------------------------------------------------------
# 2. TELEGRAM STREAMER & HTTP 429 FLOOD RECOVERY TESTS
# ---------------------------------------------------------------------------


def test_streamer_update_uses_dynamic_throttler_cadence():
    """Asserts TelegramStreamer.update sleeps according to throttler count."""
    sleep_durations = []

    def mock_sleep(d: float):
        sleep_durations.append(d)

    mock_client = MagicMock(spec=httpx.Client)
    # Mock successful edit
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "result": {"message_id": 12345}}
    mock_client.post.return_value = mock_resp

    streamer = TelegramStreamer(
        token="123456:FAKE_TOKEN",
        chat_id="8190664947",
        prefix="[TEST]",
        bot_id="fred",
        client=mock_client,
        sleep_fn=mock_sleep,
    )
    streamer.message_id = 12345

    # With 1 streamer active
    with streamer:
        assert DynamicTelegramThrottler.get_cadence_seconds() == 0.8
        streamer.update("Line 1")
        assert len(sleep_durations) == 1
        assert sleep_durations[-1] == 0.8

        # Simulate 2 other bots joining
        DynamicTelegramThrottler.register_active_sync("george")
        DynamicTelegramThrottler.register_active_sync("kai")
        assert DynamicTelegramThrottler.get_cadence_seconds() >= 2.0

        streamer.update("Line 2")
        assert len(sleep_durations) == 2
        assert sleep_durations[-1] >= 2.0
        assert sleep_durations[-1] == 2.5


def test_streamer_429_parsed_retry_after_backoff():
    """Asserts 429 with parameters.retry_after is parsed, sleeps retry_after + 0.5s, and retries."""
    sleep_durations = []

    def mock_sleep(d: float):
        sleep_durations.append(d)

    mock_client = MagicMock(spec=httpx.Client)

    # First call: 429 with retry_after=4.0
    resp_429 = MagicMock(spec=httpx.Response)
    resp_429.status_code = 429
    resp_429.json.return_value = {
        "ok": False,
        "error_code": 429,
        "description": "Too Many Requests: retry after 4",
        "parameters": {"retry_after": 4.0},
    }

    # Second call: 200 OK
    resp_200 = MagicMock(spec=httpx.Response)
    resp_200.status_code = 200
    resp_200.json.return_value = {"ok": True, "result": {"message_id": 999}}

    mock_client.post.side_effect = [resp_429, resp_200]

    streamer = TelegramStreamer(
        token="123456:FAKE_TOKEN",
        chat_id="8190664947",
        prefix="[TEST]",
        client=mock_client,
        sleep_fn=mock_sleep,
    )
    streamer.message_id = 999

    res = streamer.edit_message("Updated test line")

    # Verify backoff was honored: 4.0s + 0.5s = 4.5s
    assert len(sleep_durations) == 1
    assert sleep_durations[0] == pytest.approx(4.5, 0.01)

    # Verify retry succeeded
    assert res == {"ok": True, "result": {"message_id": 999}}
    assert mock_client.post.call_count == 2


def test_streamer_429_default_retry_after_when_missing():
    """Asserts 429 without parameters.retry_after defaults to 3.0s (+0.5s = 3.5s)."""
    sleep_durations = []

    def mock_sleep(d: float):
        sleep_durations.append(d)

    mock_client = MagicMock(spec=httpx.Client)

    # First call: 429 with no parameters
    resp_429 = MagicMock(spec=httpx.Response)
    resp_429.status_code = 429
    resp_429.json.return_value = {"ok": False, "error_code": 429, "description": "Too Many Requests"}

    # Second call: 200 OK
    resp_200 = MagicMock(spec=httpx.Response)
    resp_200.status_code = 200
    resp_200.json.return_value = {"ok": True, "result": {"message_id": 999}}

    mock_client.post.side_effect = [resp_429, resp_200]

    streamer = TelegramStreamer(
        token="123456:FAKE_TOKEN",
        chat_id="8190664947",
        prefix="[TEST]",
        client=mock_client,
        sleep_fn=mock_sleep,
    )
    streamer.message_id = 999

    res = streamer.edit_message("Updated test line")

    # Default 3.0s + 0.5s = 3.5s
    assert len(sleep_durations) == 1
    assert sleep_durations[0] == pytest.approx(3.5, 0.01)
    assert res == {"ok": True, "result": {"message_id": 999}}


def test_streamer_429_httpx_status_error_recovery():
    """Asserts httpx.HTTPStatusError on 429 is caught and backed off without crashing."""
    sleep_durations = []

    def mock_sleep(d: float):
        sleep_durations.append(d)

    mock_client = MagicMock(spec=httpx.Client)

    resp_429 = MagicMock(spec=httpx.Response)
    resp_429.status_code = 429
    resp_429.json.return_value = {"parameters": {"retry_after": 2.5}}
    err_429 = httpx.HTTPStatusError("429 Too Many Requests", request=MagicMock(), response=resp_429)

    resp_200 = MagicMock(spec=httpx.Response)
    resp_200.status_code = 200
    resp_200.json.return_value = {"ok": True, "result": {"message_id": 888}}

    mock_client.post.side_effect = [err_429, resp_200]

    streamer = TelegramStreamer(
        token="123456:FAKE_TOKEN",
        chat_id="8190664947",
        prefix="[TEST]",
        client=mock_client,
        sleep_fn=mock_sleep,
    )
    streamer.message_id = 888

    res = streamer.edit_message("Line with HTTPStatusError")

    # 2.5s + 0.5s = 3.0s
    assert len(sleep_durations) == 1
    assert sleep_durations[0] == pytest.approx(3.0, 0.01)
    assert res == {"ok": True, "result": {"message_id": 888}}


def test_streamer_server_error_fails_gracefully():
    """Asserts 500 error logs and returns None without raising an unhandled exception."""
    mock_client = MagicMock(spec=httpx.Client)
    resp_500 = MagicMock(spec=httpx.Response)
    resp_500.status_code = 500
    resp_500.raise_for_status.side_effect = httpx.HTTPStatusError("500 Server Error", request=MagicMock(), response=resp_500)
    mock_client.post.return_value = resp_500

    streamer = TelegramStreamer(
        token="123456:FAKE_TOKEN",
        chat_id="8190664947",
        prefix="[TEST]",
        client=mock_client,
    )
    streamer.message_id = 777

    # Should not raise exception
    res = streamer.edit_message("Line triggering 500")
    assert res is None


# ---------------------------------------------------------------------------
# 3. DAEMON VS CLI COLLISION PREVENTION (HTTP 409) TESTS
# ---------------------------------------------------------------------------


def test_check_hermes_daemon_collision_active():
    """Asserts check_hermes_daemon_collision detects active systemd unit."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="active\n")

        is_active, svc_name = check_hermes_daemon_collision("george")
        assert is_active is True
        assert svc_name == "hermes-gateway@george.service"
        mock_run.assert_called_with(
            ["systemctl", "is-active", "hermes-gateway@george.service"],
            capture_output=True,
            text=True,
            check=False,
        )


def test_check_hermes_daemon_collision_inactive():
    """Asserts check_hermes_daemon_collision returns False when unit is inactive."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=3, stdout="inactive\n")

        is_active, svc_name = check_hermes_daemon_collision("kai")
        assert is_active is False
        assert svc_name == "hermes-gateway@kai.service"


def test_daemon_collision_guard_auto_pause_lifecycle():
    """Asserts daemon_collision_guard stops active service on enter and restarts on exit."""
    with patch("prismatic.fleet.telegram.check_hermes_daemon_collision") as mock_check, \
         patch("subprocess.run") as mock_run:

        mock_check.return_value = (True, "hermes-gateway@orchestrator.service")
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with daemon_collision_guard("orchestrator", auto_pause=True) as (is_active, svc, stopped):
            assert is_active is True
            assert svc == "hermes-gateway@orchestrator.service"
            assert stopped is True
            # Stop command called
            mock_run.assert_any_call(
                ["sudo", "systemctl", "stop", "hermes-gateway@orchestrator.service"],
                capture_output=True,
                text=True,
            )

        # Start command called on exit
        mock_run.assert_any_call(
            ["sudo", "systemctl", "start", "hermes-gateway@orchestrator.service"],
            capture_output=True,
            text=True,
        )


def test_daemon_collision_guard_no_auto_pause_warns():
    """Asserts daemon_collision_guard does not stop service when auto_pause is False."""
    with patch("prismatic.fleet.telegram.check_hermes_daemon_collision") as mock_check, \
         patch("subprocess.run") as mock_run, \
         patch("sys.stderr") as mock_stderr:

        mock_check.return_value = (True, "hermes-gateway@ned.service")

        with daemon_collision_guard("ned", auto_pause=False) as (is_active, svc, stopped):
            assert is_active is True
            assert svc == "hermes-gateway@ned.service"
            assert stopped is False

        # No systemctl stop should have been executed
        mock_run.assert_not_called()


def test_cli_chat_checks_daemon_collision():
    """Asserts prismatic chat invokes collision guard before launching subprocess."""
    with patch("prismatic.fleet.telegram.run_hermes_chat_with_guard") as mock_guard_run:
        mock_guard_run.return_value = 0

        # Direct prismatic chat
        ret1 = cli_run(["chat", "--profile", "george", "--no-auto-pause", "-q", "hello"])
        assert ret1 == 0
        mock_guard_run.assert_called_with(
            profile="george",
            extra_args=["-q", "hello"],
            auto_pause=False,
        )

        # Via prismatic fleet chat
        ret2 = cli_run(["fleet", "chat", "--profile", "kai", "-v"])
        assert ret2 == 0
        mock_guard_run.assert_called_with(
            profile="kai",
            extra_args=["-v"],
            auto_pause=True,
        )


def test_gateway_telegram_throttler_api():
    """Asserts Gateway REST API endpoints accurately inspect and manipulate throttler state."""
    from fastapi.testclient import TestClient
    from prismatic.gateway.server import app

    client = TestClient(app)

    # Initial state
    resp = client.get("/api/gateway/telegram/throttler/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["active_count"] == 0
    assert data["cadence_seconds"] == 0.8

    # Register 1st bot
    resp = client.post("/api/gateway/telegram/throttler/register", json={"bot_id": "fred"})
    assert resp.status_code == 200
    assert resp.json()["active_count"] == 1
    assert resp.json()["cadence_seconds"] == 0.8

    # Register 2nd bot -> scales to 1.6s
    resp = client.post("/api/gateway/telegram/throttler/register", json={"bot_id": "george"})
    assert resp.status_code == 200
    assert resp.json()["active_count"] == 2
    assert resp.json()["cadence_seconds"] == 1.6

    # Register 3rd bot -> scales to 2.5s
    resp = client.post("/api/gateway/telegram/throttler/register", json={"bot_id": "kai"})
    assert resp.status_code == 200
    assert resp.json()["active_count"] == 3
    assert resp.json()["cadence_seconds"] == 2.5

    # Unregister bot -> downscales to 1.6s
    resp = client.post("/api/gateway/telegram/throttler/unregister", json={"bot_id": "george"})
    assert resp.status_code == 200
    assert resp.json()["active_count"] == 2
    assert resp.json()["cadence_seconds"] == 1.6

    # Validation on missing parameter
    resp = client.post("/api/gateway/telegram/throttler/register", json={})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 4. UNCLOSED MARKDOWN DELIMITER RESILIENCE TESTS (HTTP 400 FALLBACK)
# ---------------------------------------------------------------------------


def test_streamer_edit_message_unclosed_markdown_fallback_to_plain_text():
    """Asserts that HTTP 400 Bad Request on unclosed markdown entities falls back to plain text."""
    mock_client = MagicMock(spec=httpx.Client)

    # First call: 400 Bad Request from Telegram entity parser
    resp_400 = MagicMock(spec=httpx.Response)
    resp_400.status_code = 400
    resp_400.json.return_value = {
        "ok": False,
        "error_code": 400,
        "description": "Bad Request: can't parse entities: Can't find end of the entity starting at byte offset 24",
    }

    # Second call (plain-text fallback without parse_mode): 200 OK
    resp_200 = MagicMock(spec=httpx.Response)
    resp_200.status_code = 200
    resp_200.json.return_value = {"ok": True, "result": {"message_id": 555}}

    mock_client.post.side_effect = [resp_400, resp_200]

    streamer = TelegramStreamer(
        token="123456:FAKE_TOKEN",
        chat_id="8190664947",
        prefix="[TEST]",
        client=mock_client,
    )
    streamer.message_id = 555

    # Text containing unclosed code block: ```python\ndef hello():
    unclosed_code_snippet = "```python\ndef hello():\n    return 'world'"
    res = streamer.edit_message(unclosed_code_snippet)

    assert res == {"ok": True, "result": {"message_id": 555}}
    assert mock_client.post.call_count == 2

    # Verify first call had parse_mode="Markdown"
    first_call_json = mock_client.post.call_args_list[0].kwargs["json"]
    assert first_call_json.get("parse_mode") == "Markdown"

    # Verify fallback call stripped parse_mode to preserve delivery as plain text
    fallback_call_json = mock_client.post.call_args_list[1].kwargs["json"]
    assert "parse_mode" not in fallback_call_json
    assert fallback_call_json["text"] == unclosed_code_snippet


def test_streamer_edit_message_400_httpx_status_error_fallback():
    """Asserts that httpx.HTTPStatusError with 400 also triggers plain-text fallback."""
    mock_client = MagicMock(spec=httpx.Client)

    resp_400 = MagicMock(spec=httpx.Response)
    resp_400.status_code = 400
    resp_400.json.return_value = {"error_code": 400, "description": "can't parse entities"}
    err_400 = httpx.HTTPStatusError("400 Bad Request", request=MagicMock(), response=resp_400)

    resp_200 = MagicMock(spec=httpx.Response)
    resp_200.status_code = 200
    resp_200.json.return_value = {"ok": True, "result": {"message_id": 555}}

    mock_client.post.side_effect = [err_400, resp_200]

    streamer = TelegramStreamer(
        token="123456:FAKE_TOKEN",
        chat_id="8190664947",
        prefix="[TEST]",
        client=mock_client,
    )
    streamer.message_id = 555

    res = streamer.edit_message("*unclosed bold entity")
    assert res == {"ok": True, "result": {"message_id": 555}}
    assert mock_client.post.call_count == 2

    fallback_call_json = mock_client.post.call_args_list[1].kwargs["json"]
    assert "parse_mode" not in fallback_call_json


def test_streamer_start_unclosed_markdown_fallback():
    """Asserts that start() also falls back to plain text if initial text has entity errors."""
    mock_client = MagicMock(spec=httpx.Client)

    resp_400 = MagicMock(spec=httpx.Response)
    resp_400.status_code = 400
    resp_400.json.return_value = {"error_code": 400, "description": "can't parse entities"}

    resp_200 = MagicMock(spec=httpx.Response)
    resp_200.status_code = 200
    resp_200.json.return_value = {"ok": True, "result": {"message_id": 666}}

    mock_client.post.side_effect = [resp_400, resp_200]

    streamer = TelegramStreamer(
        token="123456:FAKE_TOKEN",
        chat_id="8190664947",
        prefix="[TEST]",
        client=mock_client,
    )

    msg_id = streamer.start("Initial text with unclosed `code block")
    assert msg_id == 666
    assert streamer.message_id == 666
    assert mock_client.post.call_count == 2

    fallback_call_json = mock_client.post.call_args_list[1].kwargs["json"]
    assert "parse_mode" not in fallback_call_json
