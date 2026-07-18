from __future__ import annotations

import json
from pathlib import Path

import pytest

from prismatic.agent_packet_normalizer import RAW_AGENT_OUTPUT_REPAIR_QUEUE_MARKER
from prismatic.agent_raw_output_queue import RawAgentOutputStore, queue_counts


_GATE_ACCEPTED_SOURCE_PATH = str(Path.home() / "work" / "agy-gro-3952-proof")


def valid_packet(**overrides):
    packet = {
        "agent": "agy",
        "source_branch": "feature/gro-3952-proof",
        "source_path": _GATE_ACCEPTED_SOURCE_PATH,
        "base_branch": "main",
        "changed_files": ["prismatic/agent_raw_output_queue.py"],
        "result_summary": "raw output queue implemented",
        "proof": {
            "command": "python -m pytest tests/test_agent_raw_output_queue.py",
            "result": "PASS",
            "log": "/tmp/fred-raw-agent-output-repair-queue-verify.log",
            "scope": "raw output queue",
            "ad_hoc_or_canonical": "ad-hoc targeted",
            "marker": RAW_AGENT_OUTPUT_REPAIR_QUEUE_MARKER,
            "non_claims": [
                "auto_rerun_enabled",
                "auto_merge_enabled",
                "production_deploy",
            ],
        },
        "lane_scope": {
            "allowed_paths": ["prismatic/"],
            "touched_paths": ["prismatic/agent_raw_output_queue.py"],
        },
    }
    packet.update(overrides)
    return packet


def test_store_persists_valid_packet_with_canonical_id(tmp_path: Path) -> None:
    store = RawAgentOutputStore(tmp_path / "raw.sqlite3")

    row = store.persist(
        raw_text=json.dumps(valid_packet()),
        agent="agy",
        task_id="GRO-3952",
        source_event_id="evt-valid",
        expected_agent="agy",
    )

    assert row.raw_output_id.startswith("raw_")
    assert row.normalization_status == "accepted"
    assert row.canonical_packet_id and row.canonical_packet_id.startswith("packet_")
    assert row.repair_hint is None
    assert row.rerun_allowed is False
    assert row.rerun_requested is False
    assert store.get(row.raw_output_id) == row
    assert store.counts()["accepted"] == 1


def test_missing_original_proof_log_stays_repairable_without_canonical_id(
    tmp_path: Path,
) -> None:
    store = RawAgentOutputStore(tmp_path / "raw.sqlite3")
    packet = valid_packet()
    packet["proof"].pop("log")

    row = store.persist(
        raw_text=json.dumps(packet),
        agent="agy",
        task_id="GRO-3952",
        expected_agent="agy",
    )

    assert row.normalization_status == "rejected_repairable"
    assert row.repair_hint == "missing_proof_log"
    assert row.canonical_packet_id is None
    assert row.rerun_allowed is False
    assert store.counts()["repairable"] == 1


def test_repair_preview_is_read_only_and_does_not_enable_rerun(tmp_path: Path) -> None:
    store = RawAgentOutputStore(tmp_path / "raw.sqlite3")
    row = store.persist(
        raw_text="I finished it but emitted prose instead of JSON.",
        agent="agy",
        task_id="GRO-3952",
    )

    preview = store.repair_preview(row.raw_output_id)
    reread = store.get(row.raw_output_id)

    assert preview["marker"] == RAW_AGENT_OUTPUT_REPAIR_QUEUE_MARKER
    assert preview["status"] == "preview"
    assert preview["normalization_status"] == "rejected_repairable"
    assert preview["repair_hint"] == "agent_prose_only"
    assert preview["would_auto_repair"] is False
    assert preview["would_auto_rerun"] is False
    assert preview["raw_output"]["raw_output_id"] == row.raw_output_id
    assert reread.rerun_requested is False
    assert reread.rerun_requested_at is None


def test_rerun_request_only_allowed_for_rerun_required_rows(tmp_path: Path) -> None:
    store = RawAgentOutputStore(tmp_path / "raw.sqlite3")
    packet = valid_packet()
    packet.pop("source_path")
    row = store.persist(
        raw_text=json.dumps(packet),
        agent="agy",
        task_id="GRO-3952",
        expected_agent="agy",
    )

    updated = store.mark_rerun_requested(row.raw_output_id)

    assert row.normalization_status == "rejected_rerun_required"
    assert row.repair_hint == "missing_source_path"
    assert updated.rerun_allowed is True
    assert updated.rerun_requested is True
    assert updated.rerun_requested_at


def test_rerun_request_rejects_repairable_rows(tmp_path: Path) -> None:
    store = RawAgentOutputStore(tmp_path / "raw.sqlite3")
    packet = valid_packet()
    packet["proof"].pop("non_claims")
    row = store.persist(
        raw_text=json.dumps(packet),
        agent="agy",
        task_id="GRO-3952",
        expected_agent="agy",
    )

    with pytest.raises(ValueError, match="rerun is not allowed"):
        store.mark_rerun_requested(row.raw_output_id)

    assert store.get(row.raw_output_id).rerun_requested is False


def test_queue_counts_uses_explicit_db_path(tmp_path: Path) -> None:
    db_path = tmp_path / "raw.sqlite3"
    store = RawAgentOutputStore(db_path)
    store.persist(
        raw_text=json.dumps(valid_packet()), agent="agy", expected_agent="agy"
    )
    secret_like_output = "".join(
        ["OPENAI_API", "_KEY=", "sk", "-", "test-secret-like-token"]
    )
    store.persist(raw_text=secret_like_output, agent="agy")

    counts = queue_counts(db_path=db_path)

    assert counts["accepted"] == 1
    assert counts["policy_violation"] == 1
    assert counts["rejected"] == 1
