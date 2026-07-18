from __future__ import annotations

import importlib
from pathlib import Path


def test_result_writeback_visible_event_dry_run_logs_message(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("PRISMATIC_VISIBLE_WAKE_DRY_RUN", "1")
    log_path = tmp_path / "visible-result.log"
    monkeypatch.setenv("PRISMATIC_VISIBLE_WAKE_LOG", str(log_path))

    import scripts.assigned_agent_result_writeback as writeback

    writeback = importlib.reload(writeback)
    result = writeback.emit_visible_result_event(
        "agy",
        "GRO-VISIBLE-RESULT",
        "WORK_BLOCKED",
        marker="AGY_PACKET_FIXTURES_REPAIR_HINTS_BLOCKED",
        log="/tmp/agy.log",
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    text = log_path.read_text()
    assert "Prismatic assigned-agent stream: WORK_BLOCKED" in text
    assert "agent=agy" in text
    assert "issue=GRO-VISIBLE-RESULT" in text
    assert "marker=AGY_PACKET_FIXTURES_REPAIR_HINTS_BLOCKED" in text
    assert "Linear remains source of truth" in text


def test_agy_output_log_path_and_skill_pack_lines_are_classified():
    import importlib.util
    from pathlib import Path

    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "assigned_agent_result_writeback.py"
    )
    spec = importlib.util.spec_from_file_location(
        "assigned_agent_result_writeback_for_test", script
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)

    assert mod.command_log_path(
        ["env", "PRISMATIC_AGY_OUTPUT_LOG=/tmp/agy-output.log", "bash", "-lc", "true"]
    ) == Path("/tmp/agy-output.log")
    packet = mod.compact_packet_from_text("""
skill_pack_state=loaded
shared_skill_packs=shared/prismatic-completed-work-contract,shared/prismatic-proof-packet
agent_skill_packs=agy/agy-structured-result-packet,agy/agy-one-task-scope,agy/agy-model-preflight
packet_contract_version=prismatic-completed-work-v1
packet_validation=passed
COMMAND=agy --print <prompt>
RESULT=BLOCKED
LOG=/tmp/agy-output.log
SCOPE=AGY print-mode completed-work packet capture
AD_HOC_OR_CANONICAL=ad-hoc targeted
NOT_CLAIMING=Prompt4 green
MARKER=AGY_PACKET_FIXTURES_REPAIR_HINTS_BLOCKED
""")
    assert packet is not None
    assert "skill_pack_state=loaded" in packet
    assert "packet_contract_version=prismatic-completed-work-v1" in packet
    assert "RESULT=BLOCKED" in packet
    assert "MARKER=AGY_PACKET_FIXTURES_REPAIR_HINTS_BLOCKED" in packet


def test_george_visible_packet_parser_status_lines(tmp_path: Path, monkeypatch):
    import importlib.util
    import sqlite3
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "assigned_agent_result_writeback_george_test",
        Path("scripts/assigned_agent_result_writeback.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)

    log_path = tmp_path / "george.log"
    log_path.write_text(
        """
COMMAND=hermes --profile george -z <prompt>
RESULT=PASS
LOG=/tmp/george.log
SCOPE=George dashboard/API/writeback verification
AD_HOC_OR_CANONICAL=ad-hoc targeted
NOT_CLAIMING=Prompt5 unlocked
MARKER=GEORGE_ASSIGNED_DISPATCH_OK
""",
        encoding="utf-8",
    )
    launch_db = tmp_path / "event_router.db"
    con = sqlite3.connect(launch_db)
    con.execute(
        "create table launch_records (run_id text, agent_name text, issue_id text, identifier text, status text, pid integer, created_at text, command_json text)"
    )
    con.execute(
        "insert into launch_records values (?,?,?,?,?,?,?,?)",
        (
            "george-run-1",
            "george",
            "GRO-GEORGE",
            "GRO-GEORGE",
            "launched",
            999999,
            "2026-07-18T10:00:00Z",
            '["/usr/bin/hermes", "--profile", "george", "-z", "prompt", "--log-file", "{}"]'.format(
                log_path
            ),
        ),
    )
    con.commit()
    con.close()
    monkeypatch.setattr(mod, "LAUNCH_DB", launch_db)
    monkeypatch.setattr(mod, "has_marker", lambda ident, marker: True)
    monkeypatch.setattr(
        mod,
        "add_comment",
        lambda ident, body: (_ for _ in ()).throw(
            AssertionError("unexpected Linear write")
        ),
    )
    events = []
    monkeypatch.setattr(
        mod,
        "emit_visible_result_event",
        lambda agent, ident, event, marker="", log="": events.append(
            (agent, ident, event, marker, log)
        ),
    )

    out = mod.reconcile_george_visible_launches()

    assert out == [
        "GEORGE_VISIBLE_PACKET_WRITTEN GRO-GEORGE GEORGE_ASSIGNED_DISPATCH_OK result=PASS"
    ]
    assert events == [
        (
            "george",
            "GRO-GEORGE",
            "WORK_RESULT_PACKET",
            "GEORGE_ASSIGNED_DISPATCH_OK",
            str(log_path),
        )
    ]
    con = sqlite3.connect(launch_db)
    status = con.execute(
        "select status from launch_records where run_id='george-run-1'"
    ).fetchone()[0]
    con.close()
    assert status == "completed"
