"""tests/test_native_cron_run_recorder.py — WI-1: run-recorder wrapper for native crons.

Scheduled cron runs must write back last_run_at / last_status / last_exit_code
(+ truncated outputs) to the store so the dashboard "Last Run" column reflects
reality. The export wraps each crontab line with:

    {schedule} <wrapper> {cron-id} -- cd {cwd} && {command}

where the wrapper is `python -m prismatic.native_crons record-run {id}`.
The wrapper runs the job's own shell command, records best-effort, and always
propagates the job's exit code.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from prismatic import native_crons
from prismatic.native_crons import (
    NativeCron,
    NativeCronStore,
    export_system_crontab_lines,
    record_cron_run,
)


def _lines_for(store: NativeCronStore, cron_id: str) -> list[str]:
    return [line for line in export_system_crontab_lines(store) if cron_id in line]


def _store_with_cron(tmp_path: Path, **overrides) -> tuple[NativeCronStore, NativeCron]:
    store = NativeCronStore(path=tmp_path / "native_crons.json")
    kwargs: dict = {
        "id": "test.wrapper-cron",
        "name": "Wrapper test cron",
        "schedule": "*/5 * * * *",
        "command": ["echo", "hello"],
    }
    kwargs.update(overrides)
    cron = NativeCron(**kwargs)
    store.save([cron])
    return store, cron


# ── export wrapping ───────────────────────────────────────────────────


def test_export_lines_use_record_run_wrapper(tmp_path: Path) -> None:
    """Fail-first: pre-fix export emits bare `cd {cwd} && {command}` with no wrapper."""
    store, cron = _store_with_cron(tmp_path)
    lines = _lines_for(store, cron.id)
    assert len(lines) == 1
    line = lines[0]
    assert line.startswith("*/5 * * * * ")
    assert "prismatic.native_crons record-run" in line
    assert shlex.quote(cron.id) in line
    assert " -- " in line


def test_export_line_preserves_job_tail(tmp_path: Path) -> None:
    """The text after `--` must keep the exact old execution semantics."""
    store, cron = _store_with_cron(tmp_path)
    line = _lines_for(store, cron.id)[0]
    tail = line.rsplit(" -- ", 1)[1]
    cwd = native_crons.repo_root()
    # single shell-quoted word: the wrapper receives it verbatim
    assert tail == shlex.quote(f"cd {shlex.quote(str(cwd))} && echo hello")


def test_exported_line_round_trips_quoted_arguments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Arguments containing spaces must survive the wrapper verbatim."""
    import os

    store_path = tmp_path / "native_crons.json"
    monkeypatch.setenv("PRISMATIC_NATIVE_CRON_STORE", str(store_path))
    store, cron = _store_with_cron(tmp_path, command=["echo", "hello world"])
    line = _lines_for(store, cron.id)[0]
    command_field = line.split(" ", 5)[5]
    completed = subprocess.run(
        ["sh", "-c", command_field], capture_output=True, text=True, env=dict(os.environ)
    )
    assert completed.returncode == 0, completed.stderr
    updated = NativeCronStore(path=store_path).get(cron.id)
    assert "hello world" in (updated.last_stdout or "")


def test_exported_command_field_is_valid_shell(tmp_path: Path) -> None:
    """The crontab command field (after the schedule) must parse as valid sh."""
    store, cron = _store_with_cron(tmp_path, command=["python3", "scripts/do a thing.py", "--flag"])
    line = _lines_for(store, cron.id)[0]
    command_field = line.split(" ", 5)[5]
    completed = subprocess.run(["sh", "-n"], input=command_field, text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr


def test_exported_line_runs_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Strongest WI-1 proof: take the literal exported line, run its command
    field through sh the way cron would, and confirm the store records it."""
    import os

    store_path = tmp_path / "native_crons.json"
    monkeypatch.setenv("PRISMATIC_NATIVE_CRON_STORE", str(store_path))
    store, cron = _store_with_cron(tmp_path, command=["echo", "end-to-end"])
    line = _lines_for(store, cron.id)[0]
    command_field = line.split(" ", 5)[5]

    completed = subprocess.run(
        ["sh", "-c", command_field], capture_output=True, text=True, env=dict(os.environ)
    )
    assert completed.returncode == 0, completed.stderr

    updated = NativeCronStore(path=store_path).get(cron.id)
    assert updated.last_status == "success"
    assert updated.last_exit_code == 0
    assert "end-to-end" in (updated.last_stdout or "")


def test_export_skips_manual_and_inactive(tmp_path: Path) -> None:
    store = NativeCronStore(path=tmp_path / "native_crons.json")
    store.save([
        NativeCron(id="a.manual", name="m", schedule="manual", command=["true"]),
        NativeCron(id="a.paused", name="p", schedule="* * * * *", command=["true"],
                   state="paused"),
        NativeCron(id="a.active", name="x", schedule="* * * * *", command=["true"]),
    ])
    lines = export_system_crontab_lines(store)
    joined = "\n".join(lines)
    assert "record-run a.active --" in joined
    assert "record-run a.manual --" not in joined
    assert "record-run a.paused --" not in joined


# ── record_cron_run ───────────────────────────────────────────────────


def test_record_run_records_success(tmp_path: Path) -> None:
    store, cron = _store_with_cron(tmp_path)
    code = record_cron_run(cron.id, "echo hello", store=store)
    assert code == 0
    updated = store.get(cron.id)
    assert updated.last_status == "success"
    assert updated.last_exit_code == 0
    assert "hello" in (updated.last_stdout or "")
    assert updated.last_run_at is not None
    assert (updated.last_duration_s or 0.0) >= 0.0


def test_record_run_records_failure_with_exit_code(tmp_path: Path) -> None:
    store, cron = _store_with_cron(tmp_path)
    code = record_cron_run(cron.id, "exit 3", store=store)
    assert code == 3
    updated = store.get(cron.id)
    assert updated.last_status == "failed"
    assert updated.last_exit_code == 3
    assert updated.last_run_at is not None


def test_record_run_truncates_long_output(tmp_path: Path) -> None:
    store, cron = _store_with_cron(tmp_path)
    big = "x" * 9000
    code = record_cron_run(cron.id, f"{sys.executable} -c \"print('{big}')\"", store=store)
    assert code == 0
    updated = store.get(cron.id)
    assert updated.last_stdout is not None
    assert len(updated.last_stdout) <= 4000
    # truncation keeps the tail, same policy as the `run` action
    assert updated.last_stdout.endswith("x\n")


def test_record_run_records_stderr(tmp_path: Path) -> None:
    store, cron = _store_with_cron(tmp_path)
    code = record_cron_run(cron.id, "echo oops >&2; exit 1", store=store)
    assert code == 1
    updated = store.get(cron.id)
    assert "oops" in (updated.last_stderr or "")


def test_record_run_unknown_cron_id_is_non_fatal(tmp_path: Path, capsys) -> None:
    """Unknown id: the job still runs, its exit code propagates, nothing raises."""
    store, _ = _store_with_cron(tmp_path)
    code = record_cron_run("no.such.cron", "echo hi; exit 7", store=store)
    assert code == 7
    assert "no.such.cron" in capsys.readouterr().err


def test_record_run_store_failure_never_masks_exit_code(tmp_path: Path, capsys) -> None:
    """A broken store must not change the job's reported status."""
    store, cron = _store_with_cron(tmp_path)

    def _boom(crons):
        raise OSError("disk gone")

    store.save = _boom  # type: ignore[method-assign]
    code = record_cron_run(cron.id, "exit 5", store=store)
    assert code == 5
    assert "failed to record" in capsys.readouterr().err


# ── CLI: record-run ───────────────────────────────────────────────────


def test_cli_record_run_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store_path = tmp_path / "native_crons.json"
    monkeypatch.setenv("PRISMATIC_NATIVE_CRON_STORE", str(store_path))
    store = NativeCronStore(path=store_path)
    cron = NativeCron(id="test.cli-cron", name="CLI", schedule="* * * * *",
                      command=["echo", "cli-ok"])
    store.save([cron])

    rc = native_crons.main(["record-run", cron.id, "--", "echo", "cli-ok"])
    assert rc == 0
    updated = NativeCronStore(path=store_path).get(cron.id)
    assert updated.last_status == "success"
    assert "cli-ok" in (updated.last_stdout or "")


def test_cli_record_run_propagates_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store_path = tmp_path / "native_crons.json"
    monkeypatch.setenv("PRISMATIC_NATIVE_CRON_STORE", str(store_path))
    NativeCronStore(path=store_path).save([
        NativeCron(id="test.cli-fail", name="CLIF", schedule="* * * * *", command=["false"]),
    ])
    rc = native_crons.main(["record-run", "test.cli-fail", "--", "exit", "9"])
    assert rc == 9
    updated = NativeCronStore(path=store_path).get("test.cli-fail")
    assert updated.last_status == "failed"
    assert updated.last_exit_code == 9


# ── rendered block crontab syntax ─────────────────────────────────────


def test_rendered_block_lines_have_valid_schedule_fields(tmp_path: Path) -> None:
    """Every non-comment line of the rendered managed block must start with a
    valid 5-field schedule (WI-1 must not corrupt the crontab schedule field)."""
    from prismatic.native_crons import render_crontab_block, validate_cron_schedule

    store, _ = _store_with_cron(tmp_path)
    block = render_crontab_block(store)
    lines = [
        line
        for line in block.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert lines, "managed block rendered no cron lines"
    for line in lines:
        schedule = " ".join(line.split(" ", 5)[:5])
        validate_cron_schedule(schedule)  # raises on anything malformed


# ── WI-9: signed run receipts ─────────────────────────────────────────
"""The WI-1 recorder additionally appends a CronRunReceipt-shaped JSONL entry
(per prismatic/cron_receipts/cron-run-receipt-v1.schema.json — reused in
place) to ~/.prismatic/audit/cron-run-receipts.jsonl. v1 has no signature
infra: signing_key_id is "unsigned-local" and the signature is the explicit
"unsigned" placeholder (the schema requires signature minLength 1, so a
truly empty string would not validate).
"""


def _receipt_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the receipt log at a tmp file; never touch real ~/.prismatic."""
    log_path = tmp_path / "audit" / "cron-run-receipts.jsonl"
    monkeypatch.setenv("PRISMATIC_CRON_RECEIPT_LOG", str(log_path))
    return log_path


def _read_receipts(log_path: Path) -> list[dict]:
    import json as _json

    assert log_path.exists(), f"no receipt log written at {log_path}"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert lines, "receipt log is empty"
    return [_json.loads(line) for line in lines]


def test_record_run_appends_schema_valid_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail-first: pre-WI-9 record_cron_run writes no receipt log at all."""
    import hashlib
    from prismatic.cron_receipts.schema import validate_receipt_dict

    log_path = _receipt_env(monkeypatch, tmp_path)
    store, cron = _store_with_cron(tmp_path)

    code = record_cron_run(cron.id, "echo receipt-ok", store=store)
    assert code == 0

    receipts = _read_receipts(log_path)
    assert len(receipts) == 1
    receipt = receipts[0]
    validate_receipt_dict(receipt)  # gate: schema validation green

    assert receipt["schema_version"] == 1
    assert receipt["cron_id"] == cron.id
    assert receipt["receipt_id"], "receipt_id must be non-empty"
    assert receipt["execution_id"], "execution_id must be non-empty"
    assert receipt["outcome"] == "succeeded"
    assert receipt["attempt"] == 1
    assert receipt["error_classification"] is None
    assert receipt["signing_key_id"] == "unsigned-local"
    assert receipt["signature"] == "unsigned"
    assert receipt["runner_id"], "runner_id must be non-empty"
    assert len(receipt["runner_release_digest"]) == 64
    # evidence digest covers the run's captured outputs (joined by newline)
    expected_evidence = hashlib.sha256(b"receipt-ok\n\n").hexdigest()
    assert receipt["evidence_digest"] == expected_evidence
    # timestamps: RFC 3339 UTC with Z, started <= finished
    assert receipt["started_at"].endswith("Z")
    assert receipt["finished_at"].endswith("Z")
    assert receipt["started_at"] <= receipt["finished_at"]


def test_receipt_fields_match_failed_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from prismatic.cron_receipts.schema import validate_receipt_dict

    log_path = _receipt_env(monkeypatch, tmp_path)
    store, cron = _store_with_cron(tmp_path)

    code = record_cron_run(cron.id, "echo boom >&2; exit 3", store=store)
    assert code == 3

    receipt = _read_receipts(log_path)[0]
    validate_receipt_dict(receipt)
    assert receipt["outcome"] == "failed"
    assert receipt["error_classification"] == "exit_code:3"
    assert receipt["evidence_digest"] is not None


def test_receipt_ids_unique_across_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = _receipt_env(monkeypatch, tmp_path)
    store, cron = _store_with_cron(tmp_path)

    assert record_cron_run(cron.id, "true", store=store) == 0
    assert record_cron_run(cron.id, "true", store=store) == 0

    receipts = _read_receipts(log_path)
    assert len(receipts) == 2
    assert receipts[0]["receipt_id"] != receipts[1]["receipt_id"]
    assert receipts[0]["execution_id"] != receipts[1]["execution_id"]


def test_receipt_written_for_unknown_cron_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown id: the job ran, so the run still gets a receipt."""
    log_path = _receipt_env(monkeypatch, tmp_path)
    store, _ = _store_with_cron(tmp_path)

    code = record_cron_run("no.such.cron", "exit 7", store=store)
    assert code == 7

    receipt = _read_receipts(log_path)[0]
    assert receipt["cron_id"] == "no.such.cron"
    assert receipt["outcome"] == "failed"
    assert receipt["error_classification"] == "exit_code:7"


def test_receipt_append_failure_never_masks_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """A receipt log that cannot be written must not change the job's status."""
    # point the log at a directory: open(..., "a") raises IsADirectoryError
    bad_dir = tmp_path / "unwritable"
    bad_dir.mkdir()
    monkeypatch.setenv("PRISMATIC_CRON_RECEIPT_LOG", str(bad_dir))
    store, cron = _store_with_cron(tmp_path)

    code = record_cron_run(cron.id, "exit 5", store=store)
    assert code == 5
    assert "receipt" in capsys.readouterr().err.lower()
    # store writeback still happened
    assert store.get(cron.id).last_exit_code == 5


def test_receipt_still_written_when_store_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Broken store (run still executed): receipt is independent of store writeback."""
    from prismatic.cron_receipts.schema import validate_receipt_dict

    log_path = _receipt_env(monkeypatch, tmp_path)
    store, cron = _store_with_cron(tmp_path)

    def _boom(crons):
        raise OSError("disk gone")

    store.save = _boom  # type: ignore[method-assign]
    code = record_cron_run(cron.id, "exit 5", store=store)
    assert code == 5

    receipt = _read_receipts(log_path)[0]
    validate_receipt_dict(receipt)
    assert receipt["cron_id"] == cron.id
    assert receipt["outcome"] == "failed"


def test_default_receipt_log_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Default path is ~/.prismatic/audit/cron-run-receipts.jsonl; env overrides it."""
    from prismatic.native_crons import default_cron_receipt_log_path

    monkeypatch.delenv("PRISMATIC_CRON_RECEIPT_LOG", raising=False)
    default = default_cron_receipt_log_path()
    assert default == Path.home() / ".prismatic" / "audit" / "cron-run-receipts.jsonl"

    custom = tmp_path / "custom.jsonl"
    monkeypatch.setenv("PRISMATIC_CRON_RECEIPT_LOG", str(custom))
    assert default_cron_receipt_log_path() == custom
