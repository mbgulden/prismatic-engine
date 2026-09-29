# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_JS = (
    REPO_ROOT
    / "prismatic"
    / "shipped_plugins"
    / "hermes-plugin-realtime-activity-stream"
    / "dashboard"
    / "dist"
    / "index.js"
)


def _source() -> str:
    return DASHBOARD_JS.read_text()


def test_mobile_reconnect_state_is_persisted() -> None:
    source = _source()

    assert "prismatic_stream_events" in source
    assert "prismatic_stream_filter" in source
    assert "prismatic_stream_paused" in source
    assert "prismatic_stream_scroll" in source
    assert "window.innerWidth <= 768" in source


def test_reconnect_fetches_history_and_deduplicates_events() -> None:
    source = _source()

    assert "/api/events/history?limit=100" in source
    assert "/events/history?limit=100" in source
    assert "new Map()" in source
    assert "map.set(norm._event_id, norm)" in source
    assert "merged.slice(0, 200)" in source


def test_reconnecting_banner_is_visible_for_disconnected_state() -> None:
    source = _source()

    assert "Connection lost. Reconnecting..." in source
    assert "handshake (GRO-711)" in source
    assert "!connected && h('div'" in source
