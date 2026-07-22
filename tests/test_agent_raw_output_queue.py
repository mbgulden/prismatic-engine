from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
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


def test_secret_like_input_is_not_stored_verbatim(tmp_path: Path) -> None:
    db_path = tmp_path / "private" / "raw.sqlite3"
    store = RawAgentOutputStore(db_path)
    key_name = "OPENAI" + "_API" + "_KEY"
    token = "sk" + "-" + "test-secret-like-token"
    secret = f"{key_name}={token}"

    row = store.persist(raw_text=secret, agent="agy")

    assert row.normalization_status == "rejected_policy_violation"
    assert row.repair_hint == "secret_like_content_detected"
    db_bytes = db_path.read_bytes()
    assert token.encode() not in db_bytes
    assert "raw_text" not in row.as_dict()


def test_queue_local_secret_detector_rejects_required_classes_before_storage(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "private" / "raw.sqlite3"
    store = RawAgentOutputStore(db_path)
    cases = {
        "generic_password_redacted": "password=[REDACTED]",
        "generic_bot_token": "BOT" + "_TOKEN=" + "abc123xyz",
        "generic_api_key": "service_api" + "_key=abc123xyz",
        "generic_private_key": "PRIVATE" + "_KEY=abc123xyz",
        "authorization_bearer": "Authorization: Bearer abc123xyz",
        "authorization_basic_redacted": "Authorization=[REDACTED] Basic abc123xyz",
        "uri_userinfo": "https://user:password@example.com/path",
        "provider_specific": "OPENAI" + "_API_KEY=abc123xyz",
    }

    for source_event_id, secret_text in cases.items():
        row = store.persist(
            raw_text=secret_text,
            agent="agy",
            source_event_id=source_event_id,
        )
        assert row.normalization_status == "rejected_policy_violation"
        assert row.repair_hint == "secret_like_content_detected"

    with sqlite3.connect(db_path) as conn:
        stored_raw_texts = [
            row[0]
            for row in conn.execute(
                "SELECT raw_text FROM agent_raw_output_queue ORDER BY source_event_id"
            ).fetchall()
        ]

    assert len(stored_raw_texts) == len(cases)
    for stored in stored_raw_texts:
        envelope = json.loads(stored)
        assert envelope["raw_output_storage"] == "rejected"
        assert envelope["reason"] == "secret_like_content_detected"
        assert envelope["payload_sha256"]
        assert envelope["payload_bytes"] > 0
    full_db_text = "\n".join(stored_raw_texts)
    for secret_text in cases.values():
        assert secret_text not in full_db_text


def test_queue_local_secret_detector_avoids_prose_and_field_name_false_positives(
    tmp_path: Path,
) -> None:
    store = RawAgentOutputStore(tmp_path / "raw.sqlite3")

    not_claiming = store.persist(
        raw_text="NOT_CLAIMING=secrets", agent="agy", source_event_id="not-claiming"
    )
    field_names_only = store.persist(
        raw_text="The password token credential fields were intentionally omitted.",
        agent="agy",
        source_event_id="field-names-only",
    )

    assert not_claiming.repair_hint == "agent_prose_only"
    assert field_names_only.repair_hint == "agent_prose_only"


def test_private_permissions_oversized_payload_and_retention(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PRISMATIC_AGENT_RAW_OUTPUT_MAX_BYTES", "20")
    monkeypatch.setenv("PRISMATIC_AGENT_RAW_OUTPUT_RETENTION_ROWS", "2")
    db_path = tmp_path / "private" / "raw.sqlite3"
    store = RawAgentOutputStore(db_path)

    oversized = store.persist(raw_text="x" * 21, agent="agy", source_event_id="evt-big")
    store.persist(raw_text="plain one", agent="agy", source_event_id="evt-one")
    newest = store.persist(raw_text="plain two", agent="agy", source_event_id="evt-two")

    assert oversized.normalization_status == "rejected_policy_violation"
    assert oversized.repair_hint == "oversized_payload"
    assert oct(db_path.parent.stat().st_mode & 0o777) == "0o700"
    assert oct(db_path.stat().st_mode & 0o777) == "0o600"
    rows = store.list(limit=10)
    # The cap never discards undelivered source-of-truth rows.
    assert len(rows) == 3
    assert newest.raw_output_id in {row.raw_output_id for row in rows}


def test_duplicate_source_event_is_idempotent_and_locator_is_sanitized(
    tmp_path: Path,
) -> None:
    store = RawAgentOutputStore(tmp_path / "raw.sqlite3")

    first = store.persist(
        raw_text="first prose",
        agent="agy",
        task_id="GRO-IDEMPOTENT",
        source_event_id="launch-123",
        raw_text_or_artifact_path="https://user:pass@example.test/proof.log?token=abc123#frag",
    )
    second = store.persist(
        raw_text="second prose",
        agent="agy",
        task_id="GRO-IDEMPOTENT",
        source_event_id="launch-123",
        raw_text_or_artifact_path="https://user:pass@example.test/proof.log?token=abc123#frag",
    )

    assert first.raw_output_id == second.raw_output_id
    assert store.counts()["rejected"] == 1
    reread = store.get(first.raw_output_id)
    assert "abc123" not in reread.raw_text_or_artifact_path
    assert "pass@example" not in reread.raw_text_or_artifact_path


def _delivery_row(store, event):
    return store.persist(
        raw_text=f"plain {event}",
        agent="agy",
        task_id="GRO-3952",
        source_event_id=event,
    )


def test_delivery_rejects_unsafe_public_lease_owner(tmp_path):
    store = RawAgentOutputStore(tmp_path / "raw.sqlite3")
    _delivery_row(store, "unsafe-owner")
    with pytest.raises(ValueError, match="safe lease_owner"):
        store.claim_pending_deliveries(lease_owner="Bearer secret-value")
    with pytest.raises(ValueError, match="safe lease_owner"):
        store.claim_pending_deliveries(lease_owner="worker\nforged")


def test_delivery_backfill_and_atomic_pending_creation(tmp_path):
    db = tmp_path / "raw.sqlite3"
    store = RawAgentOutputStore(db)
    legacy = _delivery_row(store, "legacy")
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM agent_raw_output_delivery")
    reopened = RawAgentOutputStore(db)
    assert reopened.get_delivery(legacy.raw_output_id).status == "pending"
    fresh = _delivery_row(reopened, "fresh")
    assert reopened.get_delivery(fresh.raw_output_id).status == "pending"


def test_delivery_claim_order_leases_cas_and_safe_serialization(tmp_path):
    store = RawAgentOutputStore(tmp_path / "raw.sqlite3")
    first = _delivery_row(store, "a")
    second = _delivery_row(store, "b")
    now = datetime(2026, 7, 22, tzinfo=timezone.utc)
    claims = store.claim_pending_deliveries(
        limit=1, lease_owner="worker-a", lease_seconds=60, now=now
    )
    assert [claim.raw_output_id for claim in claims] == [first.raw_output_id]
    claim = claims[0]
    assert "lease_token" not in claim.as_dict()
    assert "raw_text" not in store.get_delivery(first.raw_output_id).as_dict()
    assert (
        store.claim_delivery(first.raw_output_id, lease_owner="worker-b", now=now)
        is None
    )
    replacement = store.claim_delivery(
        first.raw_output_id, lease_owner="worker-b", now=now + timedelta(seconds=61)
    )
    assert replacement is not None and replacement.lease_token != claim.lease_token
    assert not store.mark_delivery_succeeded(
        claim, completed_work_id="agy-cw-stale", now=now
    )
    assert store.mark_delivery_succeeded(
        replacement, completed_work_id="agy-cw-good", now=now + timedelta(seconds=61)
    )
    assert store.get_delivery(first.raw_output_id).completed_work_id == "agy-cw-good"
    assert (
        store.list_pending_deliveries(now=now)[0].raw_output_id == second.raw_output_id
    )


def test_concurrent_store_claims_are_disjoint(tmp_path):
    db = tmp_path / "raw.sqlite3"
    store = RawAgentOutputStore(db)
    rows = [_delivery_row(store, str(index)) for index in range(8)]
    barrier = threading.Barrier(2)
    claimed = []

    def worker(owner):
        local = RawAgentOutputStore(db)
        barrier.wait()
        claimed.extend(local.claim_pending_deliveries(limit=4, lease_owner=owner))

    threads = [threading.Thread(target=worker, args=(f"w-{i}",)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    ids = [claim.raw_output_id for claim in claimed]
    assert len(ids) == len(set(ids)) == len(rows)


def test_duplicate_does_not_reset_delivery_and_retry_exhausts(tmp_path):
    store = RawAgentOutputStore(tmp_path / "raw.sqlite3")
    row = _delivery_row(store, "duplicate")
    now = datetime(2026, 7, 22, tzinfo=timezone.utc)
    claim = store.claim_delivery(row.raw_output_id, lease_owner="worker", now=now)
    assert claim is not None
    assert store.mark_delivery_failed(
        claim,
        error_code="completed_work_storage_failed",
        retry_at=now + timedelta(seconds=30),
        now=now,
    )
    duplicate = _delivery_row(store, "duplicate")
    assert duplicate.raw_output_id == row.raw_output_id
    delivery = store.get_delivery(row.raw_output_id)
    assert delivery.status == "retry_wait" and delivery.retry_count == 1
    for count in range(1, 5):
        instant = now + timedelta(hours=count)
        claim = store.claim_delivery(
            row.raw_output_id, lease_owner="worker", now=instant
        )
        assert claim is not None
        assert store.mark_delivery_failed(
            claim,
            error_code="completed_work_storage_failed",
            retry_at=instant + timedelta(seconds=30),
            now=instant,
        )
    delivery = store.get_delivery(row.raw_output_id)
    assert delivery.status == "terminal_failed"
    assert delivery.retry_count == 5
    assert (
        delivery.last_error_code == delivery.terminal_disposition == "retry_exhausted"
    )


def test_retention_prunes_only_old_terminal_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_AGENT_RAW_OUTPUT_RETENTION_ROWS", "2")
    store = RawAgentOutputStore(tmp_path / "raw.sqlite3")
    rows = [_delivery_row(store, str(index)) for index in range(5)]
    for row in rows[:3]:
        claim = store.claim_delivery(row.raw_output_id, lease_owner="worker")
        assert claim is not None
        assert store.mark_delivery_failed(
            claim, error_code="raw_json_invalid", terminal_disposition="malformed"
        )
    retained = {item.raw_output_id for item in store.list(limit=10)}
    # The cap applies to terminal audit rows only; undelivered rows never consume it.
    assert retained == {
        rows[1].raw_output_id,
        rows[2].raw_output_id,
        rows[3].raw_output_id,
        rows[4].raw_output_id,
    }
