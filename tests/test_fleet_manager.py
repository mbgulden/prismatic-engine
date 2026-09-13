"""
Tests for Prismatic Fleet Management and Automated Session Hygiene.
"""

import datetime
import json
import sqlite3
import uuid
from pathlib import Path
import pytest
import yaml

from prismatic.fleet.manager import (
    PrismaticFleetManager,
    SessionHealth,
    SessionInfo,
    ProfileStatus,
)


@pytest.fixture
def mock_hermes_env(tmp_path):
    hermes_root = tmp_path / ".hermes"
    profiles_dir = hermes_root / "profiles"
    profiles_dir.mkdir(parents=True)

    # 1. Healthy profile (prompt_tokens=15000 is healthy under 48000 limit)
    healthy_dir = profiles_dir / "healthy_prof"
    healthy_dir.mkdir()
    healthy_cfg = healthy_dir / "config.yaml"
    healthy_cfg.write_text(
        yaml.dump({
            "model": {"default": "qwen", "context_window": 65536},
            "compression": {"enabled": True, "threshold_tokens": 48000, "threshold": 0.75},
            "plugins": {"enabled": ["prismatic_telemetry"]},
        })
    )
    _create_mock_state_db(healthy_dir / "state.db", prompt_tokens=15000, msg_count=10)

    # 2. Bloated profile (prompt_tokens=52000 exceeds 48000 limit)
    bloated_dir = profiles_dir / "bloated_prof"
    bloated_dir.mkdir()
    bloated_cfg = bloated_dir / "config.yaml"
    bloated_cfg.write_text(yaml.dump({"model": {"default": "qwen", "context_window": 65536}}))
    _create_mock_state_db(bloated_dir / "state.db", prompt_tokens=52000, msg_count=45)

    # 3. Degenerate profile (>65k tokens)
    degen_dir = profiles_dir / "degen_prof"
    degen_dir.mkdir()
    degen_cfg = degen_dir / "config.yaml"
    degen_cfg.write_text(yaml.dump({"model": "qwen"}))
    _create_mock_state_db(degen_dir / "state.db", prompt_tokens=120000, msg_count=300)

    # 4. 32k model profile (context_window = 32000)
    p32_dir = profiles_dir / "prof_32k"
    p32_dir.mkdir()
    (p32_dir / "config.yaml").write_text(
        yaml.dump({"model": {"default": "qwen-7b", "context_window": 32000}})
    )

    # 5. 128k model profile (context_window = 128000)
    p128_dir = profiles_dir / "prof_128k"
    p128_dir.mkdir()
    (p128_dir / "config.yaml").write_text(
        yaml.dump({"model": {"default": "qwen-128k", "context_window": 128000}})
    )

    # Mock fork dir
    fork_dir = tmp_path / "hermes-agent-fork"
    plugin_target = fork_dir / "plugins" / "observability" / "prismatic_telemetry"
    plugin_target.mkdir(parents=True)
    (plugin_target / "plugin.yaml").write_text("name: prismatic_telemetry\n")

    return {
        "root": hermes_root,
        "fork": fork_dir,
        "healthy": healthy_dir,
        "bloated": bloated_dir,
        "degen": degen_dir,
        "p32": p32_dir,
        "p128": p128_dir,
    }


def _create_mock_state_db(db_path: Path, prompt_tokens: int, msg_count: int):
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
            archived INTEGER DEFAULT 0
        );
    """)
    c.execute("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            role TEXT,
            content TEXT
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

    sid = f"session_{uuid.uuid4().hex[:8]}"
    now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
    skey = "agent:main:telegram:dm:12345"

    c.execute(
        "INSERT INTO sessions (id, source, session_key, started_at) VALUES (?, ?, ?, ?);",
        (sid, "telegram", skey, now_ts),
    )
    for i in range(msg_count):
        c.execute(
            "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?);",
            (sid, "user", f"message {i}"),
        )

    entry_data = {
        "session_key": skey,
        "session_id": sid,
        "platform": "telegram",
        "last_prompt_tokens": prompt_tokens,
        "total_tokens": prompt_tokens + 500,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    c.execute(
        "INSERT INTO gateway_routing (scope, session_key, entry_json, updated_at) VALUES (?, ?, ?, ?);",
        ("test", skey, json.dumps(entry_data), now_ts),
    )
    conn.commit()
    conn.close()


def test_fleet_discovery_and_health_inspection(mock_hermes_env):
    mgr = PrismaticFleetManager(
        hermes_root=mock_hermes_env["root"],
        hermes_fork_dir=mock_hermes_env["fork"],
    )
    # Test with default 48k threshold
    profiles = mgr.discover_profiles()
    p_map = {p.name: p for p in profiles}

    assert "healthy_prof" in p_map
    assert "bloated_prof" in p_map
    assert "degen_prof" in p_map

    assert p_map["healthy_prof"].sessions[0].health == SessionHealth.HEALTHY
    assert p_map["bloated_prof"].sessions[0].health == SessionHealth.BLOATED
    assert p_map["degen_prof"].sessions[0].health == SessionHealth.DEGENERATE


def test_dynamic_threshold_resolution(mock_hermes_env):
    mgr = PrismaticFleetManager(
        hermes_root=mock_hermes_env["root"],
        hermes_fork_dir=mock_hermes_env["fork"],
    )
    # 1. 32k model profile -> resolves to 24,000 (75%)
    t_32k, ctx_32k, deg_32k = mgr.resolve_profile_thresholds("prof_32k")
    assert ctx_32k == 32000
    assert t_32k == 24000
    assert deg_32k == int(32000 * 0.95)

    # 2. 65k model profile -> resolves to 49,152 (75%)
    t_65k, ctx_65k, deg_65k = mgr.resolve_profile_thresholds("healthy_prof")
    assert ctx_65k == 65536
    assert t_65k == 49152
    assert deg_65k == int(65536 * 0.95)

    # 3. 128k model profile -> resolves to 96,000 (75%)
    t_128k, ctx_128k, deg_128k = mgr.resolve_profile_thresholds("prof_128k")
    assert ctx_128k == 128000
    assert t_128k == 96000
    assert deg_128k == int(128000 * 0.95)

    # 4. Explicit override below 90%
    t_ov, ctx_ov, _ = mgr.resolve_profile_thresholds("healthy_prof", threshold_tokens=30000)
    assert t_ov == 30000
    assert ctx_ov == 65536

    # 5. Degenerate threshold >90% rejected (raises ValueError)
    with pytest.raises(ValueError, match="exceeds 90%"):
        mgr.resolve_profile_thresholds("healthy_prof", threshold_tokens=60000)

    # 6. Degenerate threshold >90% clamped when clamp_degenerate=True
    t_clamped, ctx_clamped, _ = mgr.resolve_profile_thresholds(
        "healthy_prof", threshold_tokens=60000, clamp_degenerate=True
    )
    assert t_clamped == 49152  # clamped to 75%


def test_dynamic_vllm_context_discovery(mock_hermes_env, monkeypatch):
    mgr = PrismaticFleetManager(
        hermes_root=mock_hermes_env["root"],
        hermes_fork_dir=mock_hermes_env["fork"],
    )

    # Mock query_model_context_window_sync returning 262,144
    monkeypatch.setattr(
        mgr,
        "query_model_context_window_sync",
        lambda model_name, base_url, api_key: 262144,
    )

    t_dyn, ctx_dyn, deg_dyn = mgr.resolve_profile_thresholds("degen_prof", dynamic=True)
    assert ctx_dyn == 262144
    assert t_dyn == int(262144 * 0.75)  # 196608
    assert deg_dyn == int(262144 * 0.95)  # 249036


def test_sync_profile_config(mock_hermes_env):
    mgr = PrismaticFleetManager(
        hermes_root=mock_hermes_env["root"],
        hermes_fork_dir=mock_hermes_env["fork"],
    )
    res = mgr.sync_profile_config("bloated_prof", context_window=65536)
    assert res["status"] == "UPDATED"
    assert res["threshold_tokens"] == 49152
    assert res["context_window"] == 65536
    assert res["headroom_tokens"] == 16384

    cfg_file = mock_hermes_env["bloated"] / "config.yaml"
    with open(cfg_file) as f:
        cfg = yaml.safe_load(f)

    assert cfg["compression"]["enabled"] is True
    assert cfg["compression"]["threshold_tokens"] == 49152
    assert cfg["compression"]["context_window"] == 65536
    assert cfg["compression"]["threshold"] == 0.75
    assert cfg["compression"]["headroom_tokens"] == 16384
    assert cfg["model"]["context_window"] == 65536
    assert cfg["model"]["compression_threshold"] == 49152
    assert cfg["model"]["compression_headroom"] == 16384
    assert "prismatic_telemetry" in cfg["plugins"]["enabled"]
    assert cfg["environment"]["HERMES_AUTONOMOUS_MODE"] == "1"
    assert cfg["environment"]["PRISMATIC_AUTONOMOUS"] == "1"


def test_reset_profile_session(mock_hermes_env):
    mgr = PrismaticFleetManager(
        hermes_root=mock_hermes_env["root"],
        hermes_fork_dir=mock_hermes_env["fork"],
    )
    res = mgr.reset_profile_session("degen_prof", restart_gateway=False)
    assert res["status"] == "SUCCESS"
    assert len(res["resets"]) == 1

    old_sid = res["resets"][0]["old_session_id"]
    new_sid = res["resets"][0]["new_session_id"]
    assert old_sid != new_sid

    # Verify database state
    db_file = mock_hermes_env["degen"] / "state.db"
    conn = sqlite3.connect(str(db_file))
    c = conn.cursor()

    # Old session is archived
    c.execute("SELECT archived, end_reason FROM sessions WHERE id = ?;", (old_sid,))
    row = c.fetchone()
    assert row[0] == 1
    assert row[1] == "fleet_hygiene_reset"

    # Routing is reset to 0 tokens
    c.execute("SELECT entry_json FROM gateway_routing;")
    entry = json.loads(c.fetchone()[0])
    assert entry["session_id"] == new_sid
    assert entry["last_prompt_tokens"] == 0
    assert entry["total_tokens"] == 0
    assert entry["is_fresh_reset"] is True
    conn.close()


def test_ensure_global_plugins(mock_hermes_env):
    mgr = PrismaticFleetManager(
        hermes_root=mock_hermes_env["root"],
        hermes_fork_dir=mock_hermes_env["fork"],
    )
    res = mgr.ensure_global_plugins()
    assert res["status"] in ("CREATED", "OK")

    link = mock_hermes_env["root"] / "plugins" / "prismatic_telemetry"
    assert link.is_symlink()
    assert link.resolve() == (mock_hermes_env["fork"] / "plugins" / "observability" / "prismatic_telemetry").resolve()
