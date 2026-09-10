"""
Tests for Directive 05: Post-Compression Context & State Preservation (GRO-4852).

Verifies:
1. Operational State Anchor injection (Task ID, SwarmLock leases, Git HEAD, next step).
2. State Extraction Pre-Hook queries SwarmLock and session turns.
3. Post-Compression Verification Gate fail-closed enforcement when model omits header.
4. Token count drops below 10,000 tokens while preserving 100% of operational handles.
5. Dry-run and below-threshold no-op behavior.
6. CLI `prismatic fleet compress` integration.
"""

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from prismatic.cli import run as cli_run
from prismatic.fleet.manager import (
    COMPRESSION_SYSTEM_PROMPT,
    OPERATIONAL_STATE_TEMPLATE,
    PrismaticFleetManager,
    verify_and_anchor_compressed_summary,
)


@pytest.fixture
def mock_compression_env(tmp_path):
    """Set up an isolated Hermes environment with state.db and active session."""
    hermes_root = tmp_path / ".hermes"
    profiles_dir = hermes_root / "profiles"
    profiles_dir.mkdir(parents=True)

    agent_dir = profiles_dir / "test_agent"
    agent_dir.mkdir()

    cfg_path = agent_dir / "config.yaml"
    cfg_path.write_text(
        yaml.dump({
            "model": {"default": "qwen", "context_window": 65536},
            "compression": {"enabled": True, "threshold": 0.75, "threshold_tokens": 49152},
            "plugins": {"enabled": ["prismatic_telemetry"]},
        })
    )

    db_path = agent_dir / "state.db"
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT,
            session_key TEXT,
            started_at REAL,
            ended_at REAL,
            end_reason TEXT,
            message_count INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0,
            archived INTEGER DEFAULT 0,
            title TEXT,
            last_activity_description TEXT,
            last_activity_at REAL
        );
    """)
    c.execute("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role TEXT,
            content TEXT,
            timestamp REAL,
            token_count INTEGER,
            active INTEGER NOT NULL DEFAULT 1,
            compacted INTEGER NOT NULL DEFAULT 0,
            _compressed_summary INTEGER NOT NULL DEFAULT 0
        );
    """)
    c.execute("""
        CREATE TABLE gateway_routing (
            scope TEXT NOT NULL DEFAULT '',
            session_key TEXT NOT NULL,
            entry_json TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (scope, session_key)
        );
    """)

    session_id = "sess_50k_directive05"
    session_key = "telegram:chat:8190664947"
    now_ts = time.time()

    c.execute(
        """INSERT INTO sessions (
            id, source, session_key, started_at, message_count, input_tokens, archived, title
        ) VALUES (?, 'gateway', ?, ?, 5, 50000, 0, 'Directive 05 Task Execution');""",
        (session_id, session_key, now_ts),
    )

    # Insert 5 turns totaling 50,000 tokens with task markers
    messages_data = [
        ("user", "Start task GRO-4852: Post-Compression Context & State Preservation.", 10000),
        ("assistant", "Understood. Beginning investigation of state.db and manager.py for GRO-4852.", 10000),
        ("user", "Ensure SwarmLock leases and next steps are preserved across 75% compactions.", 10000),
        ("assistant", "- [x] Reviewed SQLite WAL pragma setup\n- [x] Verified SwarmLock acquisition API", 10000),
        (
            "assistant",
            "Current execution status for GRO-4852:\n"
            "- [x] State extraction pre-hook implemented\n"
            "- Immediate Next Step: Deliver unit test suite tests/test_compression_preservation.py",
            10000,
        ),
    ]

    for role, content, tokens in messages_data:
        c.execute(
            """INSERT INTO messages (
                session_id, role, content, timestamp, token_count, active, compacted, _compressed_summary
            ) VALUES (?, ?, ?, ?, ?, 1, 0, 0);""",
            (session_id, role, content, now_ts, tokens),
        )

    routing_entry = {
        "session_id": session_id,
        "session_key": session_key,
        "last_prompt_tokens": 50000,
        "total_tokens": 50000,
        "input_tokens": 50000,
        "platform": "telegram",
    }
    c.execute(
        "INSERT INTO gateway_routing (scope, session_key, entry_json, updated_at) VALUES ('', ?, ?, ?);",
        (session_key, json.dumps(routing_entry), now_ts),
    )

    conn.commit()
    conn.close()

    return {
        "hermes_root": hermes_root,
        "agent_dir": agent_dir,
        "db_path": db_path,
        "session_id": session_id,
        "session_key": session_key,
    }


def test_compression_50k_tokens_preserves_operational_state(mock_compression_env):
    """Simulate a 50k token session with active SwarmLock lease and assert 100% operational handle preservation."""
    env = mock_compression_env
    mgr = PrismaticFleetManager(hermes_root=env["hermes_root"])

    # Mock active SwarmLock lease on prismatic/fleet/manager.py
    mock_locks_data = {
        "ok": True,
        "locks": [
            {
                "resource": "prismatic/fleet/manager.py",
                "holder": "test_agent",
                "lease_id": "lease-50k-test-uuid",
                "task_id": "GRO-4852",
                "intention": "Post-Compression Context Preservation",
                "expires_at": time.time() + 3600,
            }
        ],
    }

    with patch("httpx.Client.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_locks_data

        # Execute check_and_compress_profile
        res = mgr.check_and_compress_profile(profile="test_agent")

    assert res["status"] == "COMPRESSED"
    assert res["compressed"] is True
    assert res["pre_tokens"] == 50000
    assert res["post_tokens"] < 10000, f"Expected post-compression tokens < 10,000, got {res['post_tokens']}"
    assert res["verification_gate_passed"] is True

    state_preserved = res["state_preserved"]
    assert state_preserved["task_id"] == "GRO-4852"
    assert "prismatic/fleet/manager.py" in state_preserved["held_locks"]
    assert "Deliver unit test suite tests/test_compression_preservation.py" in state_preserved["next_step"]

    # Verify SQLite state.db integrity
    conn = sqlite3.connect(str(env["db_path"]))
    c = conn.cursor()

    # 1. Old turns compacted
    c.execute("SELECT COUNT(*) FROM messages WHERE session_id = ? AND active = 1 AND compacted = 0 AND _compressed_summary = 0;", (env["session_id"],))
    assert c.fetchone()[0] == 0

    c.execute("SELECT COUNT(*) FROM messages WHERE session_id = ? AND compacted = 1;", (env["session_id"],))
    assert c.fetchone()[0] == 5

    # 2. Exactly one new compressed summary turn
    c.execute("SELECT content, token_count, _compressed_summary, active FROM messages WHERE session_id = ? AND active = 1;", (env["session_id"],))
    rows = c.fetchall()
    assert len(rows) == 1
    content, token_count, is_summary, is_active = rows[0]
    assert is_summary == 1
    assert is_active == 1
    assert token_count < 10000

    # 3. Assert compressed turn contains exact operational handles
    assert "### 📌 CRITICAL OPERATIONAL STATE (DO NOT DISCARD)" in content
    assert "**Active Task ID:** GRO-4852" in content
    assert "prismatic/fleet/manager.py" in content
    assert "Deliver unit test suite tests/test_compression_preservation.py" in content

    # 4. Sessions table updated
    c.execute("SELECT input_tokens, last_activity_description FROM sessions WHERE id = ?;", (env["session_id"],))
    s_tokens, s_desc = c.fetchone()
    assert s_tokens < 10000
    assert s_desc == "compressed_summary"

    # 5. Gateway routing updated
    c.execute("SELECT entry_json FROM gateway_routing WHERE session_key = ?;", (env["session_key"],))
    entry = json.loads(c.fetchone()[0])
    assert entry["last_prompt_tokens"] < 10000
    assert entry["total_tokens"] < 10000

    conn.close()


def test_post_compression_verification_gate_fail_closed(mock_compression_env):
    """When the summarizer LLM drops the operational state header, gate prepends it programmatically."""
    env = mock_compression_env
    mgr = PrismaticFleetManager(hermes_root=env["hermes_root"])

    # Bad summarizer omitting the header
    def bad_summarizer(system_prompt: str, conv_text: str) -> str:
        return "Generic summary: User asked about tasks. Assistant answered. Nothing else to note."

    mock_locks_data = {
        "ok": True,
        "locks": [
            {
                "resource": "prismatic/fleet/manager.py",
                "holder": "test_agent",
                "lease_id": "lease-failclosed",
                "task_id": "GRO-4852",
            }
        ],
    }

    with patch("httpx.Client.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_locks_data

        res = mgr.check_and_compress_profile(
            profile="test_agent",
            force=True,
            summarizer_fn=bad_summarizer,
        )

    assert res["status"] == "COMPRESSED"
    assert res["programmatically_prepended"] is True
    assert res["verification_gate_passed"] is True

    # Check database
    conn = sqlite3.connect(str(env["db_path"]))
    c = conn.cursor()
    c.execute("SELECT content FROM messages WHERE session_id = ? AND active = 1;", (env["session_id"],))
    compressed_content = c.fetchone()[0]
    conn.close()

    assert "### 📌 CRITICAL OPERATIONAL STATE (DO NOT DISCARD)" in compressed_content
    assert "**Active Task ID:** GRO-4852" in compressed_content
    assert "prismatic/fleet/manager.py" in compressed_content
    assert "Deliver unit test suite tests/test_compression_preservation.py" in compressed_content
    assert "Generic summary: User asked about tasks." in compressed_content


def test_cooperative_summarizer_avoids_duplicate_anchor(mock_compression_env):
    """When the summarizer preserves the header verbatim, gate does not double-prepend."""
    env = mock_compression_env
    mgr = PrismaticFleetManager(hermes_root=env["hermes_root"])

    state_anchor = (
        "### 📌 CRITICAL OPERATIONAL STATE (DO NOT DISCARD)\n"
        "- **Active Task ID:** GRO-4852\n"
        "- **Active SwarmLock Leases:** prismatic/fleet/manager.py\n"
        "- **Git Branch & HEAD Commit:** main (abc1234)\n"
        "- **Modified Working Files:** Clean\n"
        "- **Completed Steps:** Setup complete\n"
        "- **Immediate Next Step:** Run tests"
    )

    def cooperative_summarizer(system_prompt: str, conv_text: str) -> str:
        return f"{state_anchor}\n\nConducted extensive architecture review and tests."

    mock_locks_data = {
        "ok": True,
        "locks": [{"resource": "prismatic/fleet/manager.py", "holder": "test_agent", "task_id": "GRO-4852"}],
    }

    with patch("httpx.Client.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_locks_data

        res = mgr.check_and_compress_profile(
            profile="test_agent",
            force=True,
            summarizer_fn=cooperative_summarizer,
        )

    assert res["status"] == "COMPRESSED"
    assert res["programmatically_prepended"] is False
    assert res["verification_gate_passed"] is True

    # Ensure exactly 1 instance of header in DB
    conn = sqlite3.connect(str(env["db_path"]))
    c = conn.cursor()
    c.execute("SELECT content FROM messages WHERE session_id = ? AND active = 1;", (env["session_id"],))
    content = c.fetchone()[0]
    conn.close()

    assert content.count("### 📌 CRITICAL OPERATIONAL STATE (DO NOT DISCARD)") == 1


def test_compression_skipped_when_below_threshold(mock_compression_env):
    """Sessions below 75% threshold remain untouched."""
    env = mock_compression_env
    mgr = PrismaticFleetManager(hermes_root=env["hermes_root"])

    # Update tokens in DB to only 15,000 (well below 49,152 threshold)
    conn = sqlite3.connect(str(env["db_path"]))
    c = conn.cursor()
    c.execute("UPDATE messages SET token_count = 3000 WHERE session_id = ?;", (env["session_id"],))
    c.execute("UPDATE sessions SET input_tokens = 15000 WHERE id = ?;", (env["session_id"],))
    entry = {"session_id": env["session_id"], "last_prompt_tokens": 15000, "total_tokens": 15000}
    c.execute("UPDATE gateway_routing SET entry_json = ? WHERE session_key = ?;", (json.dumps(entry), env["session_key"]))
    conn.commit()
    conn.close()

    res = mgr.check_and_compress_profile(profile="test_agent")

    assert res["status"] == "HEALTHY"
    assert res["compressed"] is False
    assert res["tokens"] == 15000

    # Assert no messages compacted
    conn = sqlite3.connect(str(env["db_path"]))
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM messages WHERE session_id = ? AND active = 1;", (env["session_id"],))
    assert c.fetchone()[0] == 5
    conn.close()


def test_dry_run_mode_does_not_mutate_db(mock_compression_env):
    """Dry-run calculates compressed tokens without mutating SQLite state."""
    env = mock_compression_env
    mgr = PrismaticFleetManager(hermes_root=env["hermes_root"])

    mock_locks_data = {
        "ok": True,
        "locks": [{"resource": "prismatic/fleet/manager.py", "holder": "test_agent", "task_id": "GRO-4852"}],
    }

    with patch("httpx.Client.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_locks_data

        res = mgr.check_and_compress_profile(profile="test_agent", dry_run=True)

    assert res["status"] == "DRY_RUN"
    assert res["compressed"] is True
    assert res["post_tokens"] < 10000

    # Ensure no rows in messages table were compacted
    conn = sqlite3.connect(str(env["db_path"]))
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM messages WHERE session_id = ? AND active = 1;", (env["session_id"],))
    assert c.fetchone()[0] == 5
    c.execute("SELECT COUNT(*) FROM messages WHERE _compressed_summary = 1;")
    assert c.fetchone()[0] == 0
    conn.close()


def test_verify_and_anchor_compressed_summary_direct():
    """Direct unit test of verify_and_anchor_compressed_summary."""
    header = "### 📌 CRITICAL OPERATIONAL STATE (DO NOT DISCARD)\n- **Active Task ID:** GRO-4852"

    # 1. Summary missing header -> prepend
    res, prepended = verify_and_anchor_compressed_summary(header, "GRO-4852", "Some summary")
    assert prepended is True
    assert res.startswith(header)

    # 2. Summary missing task_id -> prepend
    res, prepended = verify_and_anchor_compressed_summary(
        header, "GRO-4852", "### 📌 CRITICAL OPERATIONAL STATE (DO NOT DISCARD)\nOther info"
    )
    assert prepended is True

    # 3. Summary containing both -> keep
    cooperative = f"{header}\n- Other details\n\nExecutive Summary"
    res, prepended = verify_and_anchor_compressed_summary(header, "GRO-4852", cooperative)
    assert prepended is False
    assert res == cooperative


def test_cli_fleet_compress_execution(mock_compression_env, capsys):
    """Verify CLI `prismatic fleet compress <profile>` executes cleanly."""
    env = mock_compression_env

    mock_locks_data = {
        "ok": True,
        "locks": [{"resource": "prismatic/fleet/manager.py", "holder": "test_agent", "task_id": "GRO-4852"}],
    }

    with patch("httpx.Client.get") as mock_get, \
         patch.object(PrismaticFleetManager, "DEFAULT_HERMES_ROOT", env["hermes_root"]):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_locks_data

        exit_code = cli_run(["fleet", "compress", "test_agent", "--json"])

    assert exit_code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["status"] == "COMPRESSED"
    assert data["compressed"] is True
    assert data["post_tokens"] < 10000
    assert data["state_preserved"]["task_id"] == "GRO-4852"
