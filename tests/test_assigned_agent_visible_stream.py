from __future__ import annotations

import importlib
import gc
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
from types import SimpleNamespace


def canonical_result_text(
    *,
    agent: str = "agy",
    identifier: str = "GRO-3954",
    marker: str = "AGY_PACKET_FIXTURES_REPAIR_HINTS_OK",
    result: str = "PASS",
    run_id: str = "agy-run-1",
) -> str:
    changed = ["prismatic/agy_completed_work.py"]
    repo = Path.cwd().resolve()

    def git_value(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], text=True
        ).strip()

    packet = {
        "agent": agent,
        "issue_identifier": identifier,
        "run_id": run_id,
        "source_branch": f"feature/{agent}-{identifier.lower()}",
        "source_path": str(repo),
        "base_branch": "origin/main",
        "source_commit_sha": git_value("rev-parse", "HEAD^{commit}"),
        "base_commit_sha": git_value("rev-parse", "origin/main^{commit}"),
        "changed_files": changed,
        "result_summary": "canonical completed-work fixture",
        "verification_lane": "ad-hoc targeted",
        "result": result,
        "classification": {
            "PASS": "merge_ready",
            "BLOCKED": "blocked",
            "FAIL": "failed",
        }[result],
        "lane_scope": {"allowed_paths": ["prismatic/"], "touched_paths": changed},
        "proof": {
            "command": "python3 -m pytest -q tests/test_agy_completed_work.py",
            "result": result,
            "log": f"/tmp/{agent}-completed-work.log",
            "scope": "canonical completed-work fixture",
            "ad_hoc_or_canonical": "ad-hoc targeted",
            "non_claims": ["production_deployed", "auto_merge"],
            "marker": marker,
        },
        "artifacts": [{"path": f"/tmp/{agent}-completed-work.log"}],
        "non_claims": ["production_deployed", "auto_merge"],
        "marker": marker,
    }
    return "```json\n" + json.dumps(packet, sort_keys=True) + "\n```\n"


def durable_capture(**kwargs):
    return {
        "ok": True,
        "raw_output_id": "raw-fixture-1",
        "agent": kwargs["agent"],
        "task_id": kwargs["identifier"],
        "raw_bytes_sha256": hashlib.sha256(kwargs["raw_bytes"]).hexdigest(),
        "raw_bytes_length": len(kwargs["raw_bytes"]),
        "source_event_id": ":".join(
            (
                "assigned_agent_result_writeback_log",
                kwargs["agent"],
                kwargs["identifier"],
                kwargs["run_id"],
            )
        ),
    }


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

    monkeypatch.setenv("HOME", str(Path.cwd().resolve().parent))
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location(
        "assigned_agent_result_writeback_george_test",
        Path("scripts/assigned_agent_result_writeback.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)

    log_path = tmp_path / "george.log"
    log_path.write_text(
        canonical_result_text(
            agent="george",
            identifier="GRO-GEORGE",
            marker="GEORGE_ASSIGNED_DISPATCH_OK",
            run_id="george-run-1",
        ),
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
    monkeypatch.undo()
    del mod
    gc.collect()


def test_visible_reconcile_captures_raw_text_before_compact_packet(
    tmp_path: Path, monkeypatch
):
    import importlib.util
    import sqlite3

    spec = importlib.util.spec_from_file_location(
        "assigned_agent_result_writeback_capture_order_test",
        Path("scripts/assigned_agent_result_writeback.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    log_path = tmp_path / "agy.log"
    log_path.write_text(canonical_result_text(), encoding="utf-8")
    launch_db = tmp_path / "event_router.db"
    con = sqlite3.connect(launch_db)
    con.execute(
        "create table launch_records (run_id text, agent_name text, issue_id text, identifier text, status text, pid integer, created_at text, command_json text)"
    )
    con.execute(
        "insert into launch_records values (?,?,?,?,?,?,?,?)",
        (
            "agy-run-1",
            "agy",
            "GRO-3954",
            "GRO-3954",
            "launched",
            999999,
            "2026-07-18T10:00:00Z",
            '["agy", "--log-file", "{}"]'.format(log_path),
        ),
    )
    con.commit()
    con.close()
    calls = []
    monkeypatch.setattr(mod, "LAUNCH_DB", launch_db)
    monkeypatch.setattr(mod, "has_marker", lambda ident, marker: True)
    monkeypatch.setattr(mod, "emit_visible_result_event", lambda *a, **k: None)
    monkeypatch.setattr(
        mod,
        "capture_raw_result_output",
        lambda **kwargs: (
            calls.append(("capture", kwargs["text"])) or durable_capture(**kwargs)
        ),
    )
    original_canonical = mod.canonical_packet_from_text
    monkeypatch.setattr(
        mod,
        "canonical_packet_from_text",
        lambda text, **kwargs: (
            calls.append(("canonical", text)) or original_canonical(text, **kwargs)
        ),
    )

    mod.reconcile_agy()

    assert [name for name, _ in calls[:2]] == ["capture", "canonical"]
    monkeypatch.undo()
    del mod
    gc.collect()


def test_writeback_capture_preserves_exact_launcher_bytes(tmp_path: Path, monkeypatch):
    import scripts.assigned_agent_result_writeback as mod

    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))
    raw_bytes = b"RESULT=PASS\r\ninvalid=\xff\r\n"
    capture = mod.capture_raw_result_output(
        agent="agy",
        identifier="GRO-BYTES",
        run_id="agy-run-bytes",
        text=raw_bytes.decode("utf-8", errors="replace"),
        raw_bytes=raw_bytes,
        artifact_path=tmp_path / "launcher.log",
    )

    assert (
        mod.durable_capture_error(
            capture,
            agent="agy",
            identifier="GRO-BYTES",
            run_id="agy-run-bytes",
            raw_bytes=raw_bytes,
        )
        is None
    )
    db_path = tmp_path / "agent_raw_output_queue.sqlite3"
    with sqlite3.connect(db_path) as conn:
        stored = conn.execute(
            "SELECT raw_bytes, raw_bytes_sha256, raw_bytes_length "
            "FROM agent_raw_output_queue WHERE raw_output_id = ?",
            (capture["raw_output_id"],),
        ).fetchone()
    assert stored == (
        raw_bytes,
        hashlib.sha256(raw_bytes).hexdigest(),
        len(raw_bytes),
    )

    key_name = ("OPENAI" + "_API" + "_KEY").encode()
    token = ("sk" + "-" + "test-secret-like-token").encode()
    secret_bytes = key_name + b"=" + token
    rejected = mod.capture_raw_result_output(
        agent="agy",
        identifier="GRO-BYTES",
        run_id="agy-run-secret-bytes",
        text=secret_bytes.decode(),
        raw_bytes=secret_bytes,
        artifact_path=tmp_path / "secret-launcher.log",
    )
    assert (
        mod.durable_capture_error(
            rejected,
            agent="agy",
            identifier="GRO-BYTES",
            run_id="agy-run-secret-bytes",
            raw_bytes=secret_bytes,
        )
        == "raw_capture_bytes_sha256_mismatch"
    )
    with sqlite3.connect(db_path) as conn:
        secret_stored = conn.execute(
            "SELECT raw_bytes FROM agent_raw_output_queue WHERE raw_output_id = ?",
            (rejected["raw_output_id"],),
        ).fetchone()
    assert secret_stored == (None,)


def test_raw_capture_failure_blocks_ingest_and_side_effects(
    tmp_path: Path, monkeypatch
):
    import importlib.util
    import sqlite3

    spec = importlib.util.spec_from_file_location(
        "assigned_agent_result_writeback_raw_capture_test",
        Path("scripts/assigned_agent_result_writeback.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))

    log_path = tmp_path / "agy.log"
    log_path.write_text(canonical_result_text(), encoding="utf-8")
    launch_db = tmp_path / "event_router.db"
    con = sqlite3.connect(launch_db)
    con.execute(
        "create table launch_records (run_id text, agent_name text, issue_id text, identifier text, status text, pid integer, created_at text, command_json text)"
    )
    con.execute(
        "insert into launch_records values (?,?,?,?,?,?,?,?)",
        (
            "agy-run-fail-capture",
            "agy",
            "GRO-3954",
            "GRO-3954",
            "launched",
            999999,
            "2026-07-18T10:00:00Z",
            '["agy", "--log-file", "{}"]'.format(log_path),
        ),
    )
    con.commit()
    con.close()

    comments = []
    events = []
    monkeypatch.setattr(mod, "LAUNCH_DB", launch_db)
    monkeypatch.setattr(mod, "has_marker", lambda ident, marker: False)
    monkeypatch.setattr(
        mod, "add_comment", lambda ident, body: comments.append((ident, body))
    )
    monkeypatch.setattr(
        mod, "emit_visible_result_event", lambda *a, **k: events.append((a, k))
    )
    monkeypatch.setattr(
        mod,
        "capture_raw_result_output",
        lambda **kwargs: {"ok": False, "reason": "queue disk write error"},
    )

    out = mod.reconcile_agy()

    assert out == [
        "AGY_RAW_CAPTURE_FAILED GRO-3954 agy-run-fail-capture queue disk write error"
    ]
    assert comments == []
    assert events == []

    con = sqlite3.connect(launch_db)
    status = con.execute(
        "select status from launch_records where run_id='agy-run-fail-capture'"
    ).fetchone()[0]
    con.close()
    assert status == "launched"


def test_agent_or_issue_mismatch_fails_closed(tmp_path: Path, monkeypatch):
    import importlib.util
    import sqlite3

    spec = importlib.util.spec_from_file_location(
        "assigned_agent_result_writeback_mismatch_test",
        Path("scripts/assigned_agent_result_writeback.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))

    log_path = tmp_path / "agy.log"
    log_path.write_text(
        canonical_result_text(agent="fred", identifier="GRO-9999"), encoding="utf-8"
    )
    launch_db = tmp_path / "event_router.db"
    con = sqlite3.connect(launch_db)
    con.execute(
        "create table launch_records (run_id text, agent_name text, issue_id text, identifier text, status text, pid integer, created_at text, command_json text)"
    )
    con.execute(
        "insert into launch_records values (?,?,?,?,?,?,?,?)",
        (
            "agy-run-mismatch",
            "agy",
            "GRO-3954",
            "GRO-3954",
            "launched",
            999999,
            "2026-07-18T10:00:00Z",
            '["agy", "--log-file", "{}"]'.format(log_path),
        ),
    )
    con.commit()
    con.close()

    monkeypatch.setattr(mod, "LAUNCH_DB", launch_db)
    monkeypatch.setattr(mod, "has_marker", lambda ident, marker: False)
    monkeypatch.setattr(
        mod,
        "capture_raw_result_output",
        durable_capture,
    )

    out = mod.reconcile_agy()

    assert out == [
        "AGY_PACKET_PARSE_FAILED GRO-3954 agy-run-mismatch canonical_packet_agent_mismatch"
    ]

    con = sqlite3.connect(launch_db)
    status = con.execute(
        "select status from launch_records where run_id='agy-run-mismatch'"
    ).fetchone()[0]
    con.close()
    assert status == "launched"


def test_persistence_failure_prevents_terminal_completion(tmp_path: Path, monkeypatch):
    import importlib.util
    import sqlite3

    spec = importlib.util.spec_from_file_location(
        "assigned_agent_result_writeback_persistence_fail_test",
        Path("scripts/assigned_agent_result_writeback.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))

    log_path = tmp_path / "agy.log"
    log_path.write_text(
        canonical_result_text(run_id="agy-run-persist-fail"), encoding="utf-8"
    )
    launch_db = tmp_path / "event_router.db"
    con = sqlite3.connect(launch_db)
    con.execute(
        "create table launch_records (run_id text, agent_name text, issue_id text, identifier text, status text, pid integer, created_at text, command_json text)"
    )
    con.execute(
        "insert into launch_records values (?,?,?,?,?,?,?,?)",
        (
            "agy-run-persist-fail",
            "agy",
            "GRO-3954",
            "GRO-3954",
            "launched",
            999999,
            "2026-07-18T10:00:00Z",
            '["agy", "--log-file", "{}"]'.format(log_path),
        ),
    )
    con.commit()
    con.close()

    monkeypatch.setattr(mod, "LAUNCH_DB", launch_db)
    monkeypatch.setattr(mod, "has_marker", lambda ident, marker: False)
    monkeypatch.setattr(
        mod,
        "capture_raw_result_output",
        durable_capture,
    )
    monkeypatch.setattr(
        mod.AgyCompletedWorkStore,
        "ingest",
        lambda self, packet: (_ for _ in ()).throw(RuntimeError("db error")),
    )

    out = mod.reconcile_agy()

    assert out == ["AGY_PERSISTENCE_FAILED GRO-3954 agy-run-persist-fail db error"]

    con = sqlite3.connect(launch_db)
    status = con.execute(
        "select status from launch_records where run_id='agy-run-persist-fail'"
    ).fetchone()[0]
    con.close()
    assert status == "launched"


def test_repeated_reconciliation_duplicates_no_comment_event_or_status_mutation(
    tmp_path: Path, monkeypatch
):
    import importlib.util
    import sqlite3

    spec = importlib.util.spec_from_file_location(
        "assigned_agent_result_writeback_idempotency_test",
        Path("scripts/assigned_agent_result_writeback.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    monkeypatch.setenv("HOME", str(Path.cwd().resolve().parent))
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path))

    log_path = tmp_path / "agy.log"
    log_path.write_text(
        canonical_result_text(run_id="agy-run-idempotency"), encoding="utf-8"
    )
    launch_db = tmp_path / "event_router.db"
    con = sqlite3.connect(launch_db)
    con.execute(
        "create table launch_records (run_id text, agent_name text, issue_id text, identifier text, status text, pid integer, created_at text, command_json text)"
    )
    con.execute(
        "insert into launch_records values (?,?,?,?,?,?,?,?)",
        (
            "agy-run-idempotency",
            "agy",
            "GRO-3954",
            "GRO-3954",
            "launched",
            999999,
            "2026-07-18T10:00:00Z",
            '["agy", "--log-file", "{}"]'.format(log_path),
        ),
    )
    con.commit()
    con.close()

    comments = []
    events = []
    posted_markers = set()

    monkeypatch.setattr(mod, "LAUNCH_DB", launch_db)
    monkeypatch.setattr(
        mod, "has_marker", lambda ident, marker: marker in posted_markers
    )

    def fake_add_comment(ident, body):
        comments.append((ident, body))
        posted_markers.add("AGY_PACKET_FIXTURES_REPAIR_HINTS_OK")
        return True

    monkeypatch.setattr(mod, "add_comment", fake_add_comment)
    monkeypatch.setattr(
        mod, "emit_visible_result_event", lambda *a, **k: events.append((a, k))
    )
    monkeypatch.setattr(mod, "capture_raw_result_output", durable_capture)

    first = mod.reconcile_agy()
    assert first == [
        "AGY_PACKET_WRITTEN GRO-3954 AGY_PACKET_FIXTURES_REPAIR_HINTS_OK result=PASS"
    ]
    assert len(comments) == 1
    assert len(events) == 1

    # Second run — launch record is now terminal ("completed") and marker exists
    second = mod.reconcile_agy()
    assert second == []
    assert len(comments) == 1
    assert len(events) == 1
    monkeypatch.undo()
    del mod
    gc.collect()


def test_compact_only_and_invalid_raw_identity_fail_closed(tmp_path: Path, monkeypatch):
    import scripts.assigned_agent_result_writeback as mod

    compact = "RESULT=PASS\nMARKER=AGY_PACKET_FIXTURES_REPAIR_HINTS_OK\n"
    try:
        mod.canonical_packet_from_text(
            compact,
            expected_agent="agy",
            expected_identifier="GRO-3954",
            expected_run_id="agy-run-identity",
        )
    except ValueError as exc:
        assert str(exc) == "canonical_packet_missing_or_ambiguous"
    else:
        raise AssertionError("compact-only output established canonical completion")

    canonical = canonical_result_text(run_id="agy-run-identity")
    base_packet = json.loads(mod._CANONICAL_JSON_BLOCK.findall(canonical)[0])
    variants = []
    missing_provenance = dict(base_packet)
    for field in (
        "run_id",
        "source_commit_sha",
        "base_commit_sha",
        "classification",
    ):
        missing_provenance.pop(field)
    variants.append((missing_provenance, "canonical_packet_provenance_incomplete"))
    empty_lane = dict(base_packet)
    empty_lane["lane_scope"] = {}
    variants.append((empty_lane, "canonical_packet_lane_scope_incomplete"))
    invalid_artifact = dict(base_packet)
    invalid_artifact["artifacts"] = [{}]
    variants.append((invalid_artifact, "canonical_packet_artifacts_invalid"))
    revision_base = dict(base_packet)
    revision_base["base_branch"] = "HEAD~1"
    revision_base["base_commit_sha"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD~1^{commit}"], text=True
    ).strip()
    variants.append((revision_base, "canonical_packet_base_branch_invalid"))
    source_subdirectory = dict(base_packet)
    source_subdirectory["source_path"] = str((Path.cwd() / "tests").resolve())
    variants.append(
        (
            source_subdirectory,
            "canonical_packet_source_path_not_repository_root",
        )
    )
    for packet, expected_error in variants:
        try:
            mod.canonical_packet_from_text(
                "```json\n" + json.dumps(packet) + "\n```",
                expected_agent="agy",
                expected_identifier="GRO-3954",
                expected_run_id="agy-run-identity",
            )
        except ValueError as exc:
            assert str(exc) == expected_error
        else:
            raise AssertionError(f"invalid canonical packet accepted: {expected_error}")
    try:
        mod.canonical_packet_from_text(
            canonical + "```json\n{}\n```\n",
            expected_agent="agy",
            expected_identifier="GRO-3954",
            expected_run_id="agy-run-identity",
        )
    except ValueError as exc:
        assert str(exc) == "canonical_packet_missing_or_ambiguous"
    else:
        raise AssertionError("second JSON fence did not make output ambiguous")

    raw_bytes = b"raw-capture-identity"
    base = durable_capture(
        agent="agy",
        identifier="GRO-3954",
        run_id="agy-run-identity",
        raw_bytes=raw_bytes,
    )
    for field in (
        "raw_output_id",
        "agent",
        "task_id",
        "source_event_id",
        "raw_bytes_sha256",
        "raw_bytes_length",
    ):
        invalid = dict(base)
        invalid[field] = "" if field != "raw_bytes_length" else -1
        assert mod.durable_capture_error(
            invalid,
            agent="agy",
            identifier="GRO-3954",
            run_id="agy-run-identity",
            raw_bytes=raw_bytes,
        )
    assert (
        mod.durable_capture_error(
            {"ok": False, "skipped": True},
            agent="agy",
            identifier="GRO-3954",
            run_id="agy-run-identity",
            raw_bytes=raw_bytes,
        )
        == "raw_capture_unavailable"
    )

    log_path = tmp_path / "compact.log"
    log_path.write_text(compact, encoding="utf-8")
    launch_db = tmp_path / "compact-event-router.db"
    con = sqlite3.connect(launch_db)
    con.execute(
        "create table launch_records (run_id text, agent_name text, issue_id text, identifier text, status text, pid integer, created_at text, command_json text)"
    )
    con.execute(
        "insert into launch_records values (?,?,?,?,?,?,?,?)",
        (
            "agy-run-compact-only",
            "agy",
            "GRO-3954",
            "GRO-3954",
            "launched",
            999999,
            "2026-07-18T10:00:00Z",
            json.dumps(["agy", "--log-file", str(log_path)]),
        ),
    )
    con.commit()
    con.close()
    comments = []
    events = []
    monkeypatch.setattr(mod, "LAUNCH_DB", launch_db)
    monkeypatch.setattr(mod, "capture_raw_result_output", durable_capture)
    monkeypatch.setattr(
        mod.AgyCompletedWorkStore,
        "ingest",
        lambda self, packet: (_ for _ in ()).throw(
            AssertionError("compact packet reached persistence")
        ),
    )
    monkeypatch.setattr(mod, "add_comment", lambda *args: comments.append(args))
    monkeypatch.setattr(
        mod, "emit_visible_result_event", lambda *args, **kwargs: events.append(args)
    )
    assert mod.reconcile_agy() == [
        "AGY_PACKET_PARSE_FAILED GRO-3954 agy-run-compact-only canonical_packet_missing_or_ambiguous"
    ]
    con = sqlite3.connect(launch_db)
    assert (
        con.execute(
            "select status from launch_records where run_id='agy-run-compact-only'"
        ).fetchone()[0]
        == "launched"
    )
    con.close()
    assert comments == []
    assert events == []


def test_persisted_rejected_pass_cannot_complete(tmp_path: Path, monkeypatch):
    import sqlite3
    import scripts.assigned_agent_result_writeback as mod

    log_path = tmp_path / "agy.log"
    log_path.write_text(
        canonical_result_text(run_id="agy-run-rejected-pass"), encoding="utf-8"
    )
    launch_db = tmp_path / "event_router.db"
    con = sqlite3.connect(launch_db)
    con.execute(
        "create table launch_records (run_id text, agent_name text, issue_id text, identifier text, status text, pid integer, created_at text, command_json text)"
    )
    con.execute(
        "insert into launch_records values (?,?,?,?,?,?,?,?)",
        (
            "agy-run-rejected-pass",
            "agy",
            "GRO-3954",
            "GRO-3954",
            "launched",
            999999,
            "2026-07-18T10:00:00Z",
            json.dumps(["agy", "--log-file", str(log_path)]),
        ),
    )
    con.commit()
    con.close()

    rejected = SimpleNamespace(
        id="agy-cw-rejected",
        ingestion_marker=mod.AGY_COMPLETED_WORK_INGESTION_MARKER,
        proof_result="PASS",
        proof_marker="AGY_PACKET_FIXTURES_REPAIR_HINTS_OK",
        classification="rejected",
        eligible_for_merge=False,
        packet=json.loads(mod._CANONICAL_JSON_BLOCK.findall(log_path.read_text())[0]),
        as_dict=lambda marker=mod.AGY_COMPLETED_WORK_INTEGRATION_GATE_MARKER: {
            "integration_marker": marker
        },
    )
    comments = []
    events = []
    monkeypatch.setattr(mod, "LAUNCH_DB", launch_db)
    monkeypatch.setattr(mod, "capture_raw_result_output", durable_capture)
    monkeypatch.setattr(
        mod.AgyCompletedWorkStore, "ingest", lambda self, packet: rejected
    )
    monkeypatch.setattr(mod, "add_comment", lambda *args: comments.append(args))
    monkeypatch.setattr(
        mod, "emit_visible_result_event", lambda *args, **kwargs: events.append(args)
    )

    assert mod.reconcile_agy() == [
        "AGY_PERSISTED_NOT_MERGE_READY GRO-3954 agy-run-rejected-pass classification=rejected"
    ]
    con = sqlite3.connect(launch_db)
    assert (
        con.execute(
            "select status from launch_records where run_id='agy-run-rejected-pass'"
        ).fetchone()[0]
        == "launched"
    )
    con.close()
    assert comments == []
    assert events == []
    monkeypatch.undo()
    del mod
    gc.collect()
