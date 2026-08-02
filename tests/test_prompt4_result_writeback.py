import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess


def canonical_agy_result_text() -> str:
    marker = "AGY_PACKET_FIXTURES_REPAIR_HINTS_OK"
    changed = ["prismatic/agy_completed_work.py"]
    repo = Path.cwd().resolve()

    def git_value(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], text=True
        ).strip()

    packet = {
        "agent": "agy",
        "issue_identifier": "GRO-3954",
        "run_id": "launch-terminal-pass",
        "source_branch": "feature/agy-gro-3954",
        "source_path": str(repo),
        "base_branch": "origin/main",
        "source_commit_sha": git_value("rev-parse", "HEAD^{commit}"),
        "base_commit_sha": git_value("rev-parse", "origin/main^{commit}"),
        "changed_files": changed,
        "result_summary": "canonical completed-work fixture",
        "verification_lane": "ad-hoc targeted",
        "result": "PASS",
        "classification": "merge_ready",
        "lane_scope": {"allowed_paths": ["prismatic/"], "touched_paths": changed},
        "proof": {
            "command": "python3 -m pytest -q tests",
            "result": "PASS",
            "log": "/tmp/agy.log",
            "scope": "GRO-3954",
            "ad_hoc_or_canonical": "ad-hoc targeted",
            "non_claims": ["Prompt5 unlocked", "production_deployed"],
            "marker": marker,
        },
        "artifacts": [{"path": "/tmp/agy.log"}],
        "non_claims": ["Prompt5 unlocked", "production_deployed"],
        "marker": marker,
    }
    return "```json\n" + json.dumps(packet, sort_keys=True) + "\n```\n"


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
    monkeypatch.setenv("HOME", str(Path.cwd().resolve().parent))
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    log_path = tmp_path / "agy.log"
    log_path.write_text(canonical_agy_result_text())
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
    assert '"result": "PASS"' in comments[0][1]
    assert "COMPLETED_WORK_ID=agy-cw-" in comments[0][1]
    assert events and events[0][0][2] == "WORK_RESULT_PACKET"


def test_terminal_agy_packet_reconciliation_is_idempotent_when_marker_exists(
    tmp_path, monkeypatch
):
    mod = load_module()
    monkeypatch.setenv("HOME", str(Path.cwd().resolve().parent))
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    log_path = tmp_path / "agy.log"
    log_path.write_text(canonical_agy_result_text())
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
