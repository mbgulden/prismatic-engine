"""Standalone-first integration tests for Prismatic Engine.

Proves the engine's core capabilities work without Hermes, AGY, or any
orchestrator dependency. These tests:

1. Are isolated (don't write to ~/.hermes or /tmp/bot-delegation)
2. Don't require systemd (don't call systemctl, journalctl)
3. Don't require network (no Linear API calls — all mocked)
4. Don't depend on other agent processes running
5. Use only the engine's own public APIs

Per the standalone-first principle (see prismatic-web-plugin skill):
"Prismatic Engine should be able to do everything as a standalone app."

What we test:
- Engine imports + version works
- CheckResult namedtuple works
- All check_* functions return valid CheckResult (not just happy path)
- render_report is silent on green
- render_report shows action taken on yellow/red
- --json output is machine-readable
- Webhook handler accepts valid HMAC, rejects bad
- Dispatcher SQLite uses busy_timeout + WAL
- FileSignalProvider writes the right shape
- Bot-delegation bridge can be imported standalone
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
import subprocess
import sys
from pathlib import Path

import pytest

# These tests must work even if Hermes is NOT installed.
# Only depend on the engine itself.

HOME = os.environ.get("HOME", "")
PE_ROOT = Path(os.environ.get("PRISMATIC_HOME", os.path.join(HOME, "work", "prismatic-engine")) if HOME else "")
sys.path.insert(0, str(PE_ROOT))
sys.path.insert(0, str(PE_ROOT / ".venv_dev" / "lib" / "python3.12" / "site-packages"))


# ── 1. Engine imports ─────────────────────────────────────────────────
def test_engine_imports_without_hermes():
    """prismatic package + key submodules import successfully."""
    import prismatic  # noqa: F401
    import prismatic.fleet_watchdog  # noqa: F401
    import prismatic.fleet_actions  # noqa: E401
    import prismatic.dispatcher  # noqa: F401
    import prismatic.gateway.server  # noqa: F401
    import prismatic.providers.signals  # noqa: F401
    assert hasattr(prismatic, "__version__")


def test_engine_version_is_string():
    """prismatic.__version__ is a string (not a tuple or None)."""
    import prismatic
    assert isinstance(prismatic.__version__, str)
    assert len(prismatic.__version__) > 0


# ── 2. Watchdog public API ────────────────────────────────────────────
def test_fleet_watchdog_runs_without_hermes():
    """fleet_watchdog.check_all() returns list of CheckResult without external deps."""
    from prismatic.fleet_watchdog import check_all, CheckResult
    results = check_all()
    assert isinstance(results, list)
    assert len(results) >= 5  # At least 5 checks (services + freshness + size + locks)
    for r in results:
        assert isinstance(r, CheckResult)
        assert r.status in ("ok", "warn", "fail")
        assert isinstance(r.name, str)
        assert isinstance(r.message, str)
        assert isinstance(r.actionable, bool)


def test_fleet_watchdog_silent_on_green():
    """When all checks return ok, render_report produces empty string (silent on green)."""
    from prismatic.fleet_watchdog import render_report, CheckResult
    results = [CheckResult("test", "ok", "all good", False)]
    report, failed = render_report(results)
    assert report == ""
    assert failed == 0


def test_fleet_watchdog_shows_action_on_alert():
    """When an actionable check fails, render_report includes the action result."""
    from prismatic.fleet_watchdog import render_report, CheckResult
    # This alert will match action_restart_drain_timer if the service exists,
    # or "no auto-action" otherwise. Either way, the report should NOT be empty.
    results = [
        CheckResult("service:test", "fail",
                     "prismatic-test.service not active", True),
    ]
    report, failed = render_report(results, dry_run=True)
    assert "Fleet Watchdog" in report
    assert "prismatic-test.service" in report
    assert "dry-run" in report
    assert isinstance(failed, int)


def test_fleet_watchdog_json_output():
    """--json mode produces valid machine-readable output."""
    from prismatic.fleet_watchdog import render_json, CheckResult
    results = [
        CheckResult("a", "ok", "fine", False),
        CheckResult("b", "fail", "broke", True),
    ]
    out = render_json(results)
    data = json.loads(out)
    assert "timestamp" in data
    assert "results" in data
    assert "summary" in data
    assert data["summary"] == {"ok": 1, "warn": 0, "fail": 1}


# ── 3. Webhook handler (HMAC validation) ───────────────────────────────
def test_webhook_handler_accepts_valid_hmac(monkeypatch):
    """Linear webhook handler validates HMAC correctly without Hermes."""
    import hashlib
    import hmac
    import asyncio
    monkeypatch.setenv("PRISMATIC_LINEAR_WEBHOOK_SECRET", "standalone-secret-12345")
    monkeypatch.delenv("PRISMATIC_LINEAR_WEBHOOK_SECRET_NEXT", raising=False)

    from prismatic.gateway.server import linear_webhook
    from starlette.requests import Request
    from starlette.datastructures import Headers

    body = json.dumps({
        "action": "update",
        "type": "Issue",
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": {
            "identifier": "GRO-STANDALONE-1",
            "labels": {"nodes": [{"name": "agent:fred"}]},
        },
    }).encode()
    sig = hmac.new(b"standalone-secret-12345", body, hashlib.sha256).hexdigest()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http", "method": "POST", "path": "/api/gateway/linear",
        "headers": [(b"linear-signature", sig.encode())],
        "query_string": b"", "scheme": "http", "server": ("test", 9000),
    }
    request = Request(scope, receive)
    response = asyncio.run(linear_webhook(request))
    # Success → dict
    if isinstance(response, dict):
        result = response
    else:
        body_bytes = getattr(response, "body", b"")
        result = json.loads(body_bytes) if body_bytes else {}
    assert result.get("identifier") == "GRO-STANDALONE-1"
    assert result.get("status") in ("queued", "dispatched")


def test_webhook_handler_rejects_bad_signature(monkeypatch):
    """Webhook handler returns 401 for bad signature."""
    import asyncio
    monkeypatch.setenv("PRISMATIC_LINEAR_WEBHOOK_SECRET", "real-secret")
    monkeypatch.delenv("PRISMATIC_LINEAR_WEBHOOK_SECRET_NEXT", raising=False)

    from prismatic.gateway.server import linear_webhook
    from starlette.requests import Request

    body = json.dumps({
        "action": "update", "type": "Issue",
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "data": {"identifier": "GRO-X", "labels": {"nodes": []}},
    }).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http", "method": "POST", "path": "/api/gateway/linear",
        "headers": [(b"linear-signature", b"0" * 64)],
        "query_string": b"", "scheme": "http", "server": ("test", 9000),
    }
    request = Request(scope, receive)
    response = asyncio.run(linear_webhook(request))
    body_bytes = getattr(response, "body", b"")
    result = json.loads(body_bytes)
    assert result.get("status") == "rejected"
    assert "bad signature" in result.get("reason", "").lower()


# ── 4. Signal provider (file-based) ────────────────────────────────────
def test_file_signal_provider_writes_correct_shape(tmp_path, monkeypatch):
    """FileSignalProvider writes the JSON shape the bridge expects."""
    from prismatic.providers.signals.file import FileSignalProvider
    provider = FileSignalProvider(directory=str(tmp_path))
    # The class expects specific args; test via the public send_work method
    payload = {
        "target": "fred",
        "action": "work",
        "issue_id": "GRO-TEST",
        "title": "Standalone test",
        "priority": 3,
        "metadata": {},
        "signal_id": "test-signal-123",
        "created_at": 1234567890.0,
    }
    provider.send_work(**{k: v for k, v in payload.items() if k in ("target", "issue_id", "title", "priority")})

    # File was written with the expected shape
    nudge_file = tmp_path / "nudge-fred"
    assert nudge_file.exists()
    written = json.loads(nudge_file.read_text())
    # Bridge consumes these fields
    assert written["target"] == "fred"
    assert written["issue_id"] == "GRO-TEST"
    assert written["title"] == "Standalone test"
    assert "signal_id" in written  # generated by the provider


# ── 5. Dispatcher SQLite WAL + busy_timeout ────────────────────────────
def test_event_router_dedup_uses_wal_and_busy_timeout(tmp_path, monkeypatch):
    """The dedup connection has busy_timeout + WAL pragmas set (GRO-2401 fix)."""
    # Construct with a custom DB path
    from prismatic.dedup import EventRouterDedup

    # Redirect the db_path to tmp_path via monkeypatch
    test_db = tmp_path / "dedup_test.db"
    dedup = EventRouterDedup(db_path=str(test_db))
    try:
        # Query pragmas
        conn = dedup._conn
        assert conn is not None
        cur = conn.cursor()
        busy = cur.execute("PRAGMA busy_timeout").fetchone()[0]
        journal = cur.execute("PRAGMA journal_mode").fetchone()[0]
        # busy_timeout should be 5000ms (or -1 if WAL mode)
        # journal_mode should be 'wal'
        assert busy == 5000, f"Expected busy_timeout=5000, got {busy}"
        assert journal.lower() == "wal", f"Expected journal_mode=wal, got {journal}"
    finally:
        dedup._conn.close()


# ── 6. Self-containment ───────────────────────────────────────────────
def test_no_hermes_import_in_critical_paths():
    """Critical engine modules must NOT import from hermes or its subpackages."""
    import importlib
    critical_modules = [
        "prismatic.fleet_watchdog",
        "prismatic.fleet_actions",
        "prismatic.providers.signals.file",
        "prismatic.providers.signals.http",
        "prismatic.providers.signals.redis",
        "prismatic.gateway.server",
    ]
    for mod_name in critical_modules:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError as exc:
            pytest.skip(f"module {mod_name} not available: {exc}")
        # Inspect the module's __dict__ for any hermes-related imports
        mod_dict = getattr(mod, "__dict__", {})
        suspicious = []
        for key in mod_dict:
            key_lower = key.lower()
            if "hermes" in key_lower:
                suspicious.append(key)
        assert not suspicious, (
            f"{mod_name} has hermes-related symbols: {suspicious}"
        )


def test_bridge_module_importable():
    """The prismatic->Hermes bridge script can be imported standalone."""
    # Use absolute path resolution to find the script in either location
    bridge_paths = [
        Path(HOME) / ".hermes" / "profiles" / "orchestrator" / "scripts" / "prismatic_nudge_bridge.py",
        Path(os.path.join(PE_ROOT, "scripts", "prismatic_nudge_bridge.py")),
    ]
    bridge_path = next((p for p in bridge_paths if p.exists()), None)
    if bridge_path is None:
        # Try the env-resolved path
        candidate = PE_ROOT / "scripts" / "prismatic_nudge_bridge.py" if PE_ROOT else None
        if candidate and candidate.exists():
            bridge_path = candidate
        else:
            import pytest
            pytest.skip("bridge script not installed in either location")
    sys.path.insert(0, str(bridge_path.parent))
    import prismatic_nudge_bridge
    assert hasattr(prismatic_nudge_bridge, "forward_one")
    assert hasattr(prismatic_nudge_bridge, "process_pending")
    assert hasattr(prismatic_nudge_bridge, "main")


# ── 7. CLI subcommand ──────────────────────────────────────────────────
def test_cli_dispatch_invokes_fleet_watchdog():
    """`prismatic fleet-watchdog` CLI works (returns 0 = silent on green, 1 = alert)."""
    import subprocess
    result = subprocess.run(
        [
            sys.executable,
            "-m", "prismatic.cli",
            "fleet-watchdog",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(PE_ROOT),
        env={**os.environ, "PYTHONPATH": str(PE_ROOT / ".venv_dev" / "lib" / "python3.12" / "site-packages") + ":" + str(PE_ROOT)},
    )
    # Exit 0 (silent or all-actions-succeeded) or 1 (failed action)
    assert result.returncode in (0, 1)
    # Should have produced some output OR was silent on green
    assert result.stdout or result.returncode == 0