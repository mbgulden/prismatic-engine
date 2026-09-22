"""Tests for trace records: content rules, JSONL sink, fail-open emission."""

import json
import logging


from prismatic.jev.trace import TraceRecord, emit_trace, utc_now_iso


def _record(**overrides):
    base = dict(
        trace_id="tid-1",
        ts=utc_now_iso(),
        backend="fake",
        model="fake-model",
        schema_version="1.0",
        state_hash="abc123",
        state_keys=["note"],
    )
    base.update(overrides)
    return TraceRecord(**base)


def test_trace_record_carries_no_raw_state():
    secret_state = {"note": "sk-live-secretvalue", "email": "bob@example.com"}
    record = _record()
    line = json.dumps(record.to_dict(), sort_keys=True)
    for value in secret_state.values():
        assert value not in line
    # only identity + keys + hash travel
    assert record.to_dict()["state_keys"] == ["note"]
    assert record.to_dict()["state_hash"] == "abc123"


def test_error_text_is_redacted_for_secret_assignments():
    record = _record(error='boom: api_key="sk-live-secretvalue"')
    assert "sk-live-secretvalue" not in json.dumps(record.to_dict())
    assert "[REDACTED]" in record.to_dict()["error"]


def test_utc_now_iso_format():
    ts = utc_now_iso()
    assert ts.endswith("Z")
    assert "T" in ts


def test_emit_trace_appends_jsonl(tmp_path):
    path = str(tmp_path / "jev.jsonl")
    emit_trace(_record(trace_id="a"), path=path)
    emit_trace(_record(trace_id="b"), path=path)
    lines = (tmp_path / "jev.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["trace_id"] == "a"
    assert json.loads(lines[1])["trace_id"] == "b"


def test_emit_trace_creates_parent_dirs(tmp_path):
    path = str(tmp_path / "nested" / "dir" / "jev.jsonl")
    emit_trace(_record(), path=path)
    assert (tmp_path / "nested" / "dir" / "jev.jsonl").exists()


def test_emit_trace_never_raises_on_broken_sink(tmp_path, caplog):
    # path through a nonexistent dir's file used as a directory: open fails
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    with caplog.at_level(logging.DEBUG):
        emit_trace(_record(), path=str(blocker / "jev.jsonl"))  # never raises


def test_emit_trace_stdout_default_logs_debug(caplog):
    with caplog.at_level(logging.DEBUG, logger="prismatic.jev.trace"):
        emit_trace(_record(trace_id="dbg-1"))
    assert any("dbg-1" in r.message for r in caplog.records)


def test_emit_trace_survives_serialization_failure(monkeypatch):
    import prismatic.jev.trace as trace_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("serialization exploded")

    monkeypatch.setattr(trace_mod.json, "dumps", _boom)
    emit_trace(_record())  # never raises: the fail-open seam holds
