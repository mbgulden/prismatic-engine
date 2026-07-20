import importlib.util
import json
from pathlib import Path
import sqlite3


def load_module():
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "assigned_agent_result_writeback.py"
    )
    spec = importlib.util.spec_from_file_location("prompt4_writeback_test", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_db(path: Path, log_path: Path):
    con = sqlite3.connect(path)
    con.execute(
        "create table launch_records(run_id text primary key, identifier text, issue_id text, agent_name text, pid integer, status text, created_at text, command_json text)"
    )
    con.execute(
        "insert into launch_records values(?,?,?,?,?,?,?,?)",
        (
            "launch-terminal-pass",
            "GRO-3954",
            "GRO-3954",
            "agy",
            99999999,
            "completed",
            "2026-07-18T09:46:05Z",
            json.dumps(["agy", "--print", "task", "--log-file", str(log_path)]),
        ),
    )
    con.commit()
    con.close()


def test_terminal_agy_packet_is_reconciled_when_comment_writeback_was_missed(
    tmp_path, monkeypatch
):
    mod = load_module()
    log_path = tmp_path / "agy.log"
    log_path.write_text(
        "COMMAND=pytest tests/\nRESULT=PASS\nLOG=/tmp/agy.log\nSCOPE=GRO-3954\nAD_HOC_OR_CANONICAL=ad-hoc targeted\nNOT_CLAIMING=Prompt5 unlocked\nMARKER=AGY_PACKET_FIXTURES_REPAIR_HINTS_OK\n"
    )
    db = tmp_path / "event_router.db"
    make_db(db, log_path)
    monkeypatch.setattr(mod, "LAUNCH_DB", db)
    comments = []
    events = []
    monkeypatch.setattr(mod, "has_marker", lambda identifier, marker: False)
    monkeypatch.setattr(
        mod,
        "add_comment",
        lambda identifier, body: comments.append((identifier, body)) or True,
    )
    monkeypatch.setattr(
        mod,
        "emit_visible_result_event",
        lambda *args, **kwargs: events.append((args, kwargs)) or {"ok": True},
    )

    result = mod.reconcile_agy()

    assert result == [
        "AGY_PACKET_WRITTEN GRO-3954 AGY_PACKET_FIXTURES_REPAIR_HINTS_OK result=PASS"
    ]
    assert comments and comments[0][0] == "GRO-3954"
    assert "RESULT=PASS" in comments[0][1]
    assert events and events[0][0][2] == "WORK_RESULT_PACKET"


def test_terminal_agy_packet_reconciliation_is_idempotent_when_marker_exists(
    tmp_path, monkeypatch
):
    mod = load_module()
    log_path = tmp_path / "agy.log"
    log_path.write_text("RESULT=PASS\nMARKER=AGY_PACKET_FIXTURES_REPAIR_HINTS_OK\n")
    db = tmp_path / "event_router.db"
    make_db(db, log_path)
    monkeypatch.setattr(mod, "LAUNCH_DB", db)
    monkeypatch.setattr(mod, "has_marker", lambda identifier, marker: True)
    monkeypatch.setattr(
        mod,
        "add_comment",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("duplicate comment")
        ),
    )

    assert mod.reconcile_agy() == []
