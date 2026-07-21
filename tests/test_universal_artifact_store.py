"""Tests for durable content-addressed artifact store, quarantine, retention, and receipts."""

from __future__ import annotations

import concurrent.futures
from pathlib import Path

import pytest

from prismatic.universal_artifact_store import (
    UNIVERSAL_ARTIFACT_STORE_MARKER,
    AccessClass,
    ArtifactAccessDeniedError,
    ArtifactIngestionError,
    ArtifactNotFoundError,
    ArtifactStatus,
    UniversalArtifactStore,
    load_universal_artifact_store_schema,
    validate_artifact_receipt,
)


@pytest.fixture
def temp_store_dir(tmp_path: Path) -> Path:
    store_dir = tmp_path / "artifact_store"
    store_dir.mkdir()
    return store_dir


@pytest.fixture
def store(temp_store_dir: Path) -> UniversalArtifactStore:
    return UniversalArtifactStore(temp_store_dir)


def test_universal_artifact_store_marker() -> None:
    assert UNIVERSAL_ARTIFACT_STORE_MARKER == "UNIVERSAL_ARTIFACT_STORE_OK"


def test_schema_loading_and_validation() -> None:
    schema = load_universal_artifact_store_schema()
    assert isinstance(schema, dict)
    assert schema.get("title") == "Universal Artifact Store Receipt Schema"

    valid_receipt = {
        "marker": "UNIVERSAL_ARTIFACT_STORE_OK",
        "receipt_id": "art_12345",
        "content_digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "storage_id": "local://e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "source_commit": "4548ade4322b8ab8aa483a2d3e28dacb595bedcf",
        "command_env": {
            "command": "pytest tests/",
            "environment": {"PYTHONPATH": "."},
        },
        "evidence_digest": "a" * 64,
        "byte_size": 0,
        "media_type": "text/plain",
        "access_class": "private",
        "status": "ingested",
        "is_quarantined": False,
        "created_at": "2026-07-21T10:00:00Z",
    }

    errors = validate_artifact_receipt(valid_receipt)
    assert errors == [], f"Expected zero errors, got: {errors}"

    invalid_receipt = dict(valid_receipt, marker="BAD_MARKER", byte_size=-1)
    invalid_errors = validate_artifact_receipt(invalid_receipt)
    assert len(invalid_errors) > 0


def test_ingest_bytes_and_deterministic_deduplication(
    store: UniversalArtifactStore,
) -> None:
    data = b"Hello Prismatic Artifact Store World!"
    metadata = {
        "source_commit": "4548ade4322b8ab8aa483a2d3e28dacb595bedcf",
        "command_env": {"command": "build", "environment": {"ENV": "test"}},
        "media_type": "text/plain",
    }

    receipt1 = store.ingest_bytes(data, metadata, access_class=AccessClass.PUBLIC)

    assert receipt1.byte_size == len(data)
    assert receipt1.access_class == AccessClass.PUBLIC
    assert receipt1.is_quarantined is False
    assert receipt1.status == ArtifactStatus.INGESTED
    assert receipt1.marker == UNIVERSAL_ARTIFACT_STORE_MARKER

    # Ingest same bytes with different metadata
    metadata2 = {
        "source_commit": "1111111111111111111111111111111111111111",
        "command_env": {"command": "test", "environment": {"ENV": "prod"}},
        "media_type": "text/plain",
    }
    receipt2 = store.ingest_bytes(data, metadata2, access_class=AccessClass.PRIVATE)

    # Content digest should match
    assert receipt1.content_digest == receipt2.content_digest
    # Unique receipt IDs & evidence digests
    assert receipt1.receipt_id != receipt2.receipt_id
    assert receipt1.evidence_digest != receipt2.evidence_digest

    # Verify content read
    read_data = store.get_content(
        receipt1.receipt_id, requester_access=AccessClass.PUBLIC
    )
    assert read_data == data


def test_ingest_file_and_symlink_hazard(
    store: UniversalArtifactStore, tmp_path: Path
) -> None:
    real_file = tmp_path / "artifact.txt"
    real_file.write_bytes(b"Real File Content")

    metadata = {
        "source_commit": "4548ade4322b8ab8aa483a2d3e28dacb595bedcf",
        "command_env": {"command": "generate", "environment": {}},
        "media_type": "text/plain",
    }

    receipt = store.ingest_file(real_file, metadata)
    assert receipt.byte_size == len(b"Real File Content")

    # Symlink hazard
    symlink_file = tmp_path / "symlink_artifact.txt"
    try:
        symlink_file.symlink_to(real_file)
        with pytest.raises(
            ArtifactIngestionError, match="Symlink input hazard detected"
        ):
            store.ingest_file(symlink_file, metadata)
    except OSError:
        pass  # If symlinks not supported on system


def test_quarantine_on_digest_mismatch(store: UniversalArtifactStore) -> None:
    data = b"Some data to ingest"
    fake_digest = "0" * 64
    metadata = {
        "source_commit": "4548ade4322b8ab8aa483a2d3e28dacb595bedcf",
        "command_env": {"command": "tool", "environment": {}},
        "claimed_digest": fake_digest,
        "media_type": "application/octet-stream",
    }

    with pytest.raises(
        ArtifactIngestionError, match="Ingestion quarantined"
    ) as exc_info:
        store.ingest_bytes(data, metadata)

    details = exc_info.value.details
    assert details["is_quarantined"] is True
    assert details["status"] == ArtifactStatus.QUARANTINED.value
    assert details["access_class"] == AccessClass.PRIVATE.value

    quar_id = details["receipt_id"]
    receipt = store.get_receipt(quar_id)
    assert receipt.is_quarantined is True

    # Quarantined content CANNOT be fetched publicly
    with pytest.raises(ArtifactAccessDeniedError, match="quarantined"):
        store.get_content(quar_id, requester_access=AccessClass.PUBLIC)

    with pytest.raises(ArtifactAccessDeniedError, match="quarantined"):
        store.get_content(quar_id, requester_access=AccessClass.PRIVATE)


def test_private_vs_public_access_enforcement(store: UniversalArtifactStore) -> None:
    data = b"Top Secret Data"
    metadata = {
        "source_commit": "4548ade4322b8ab8aa483a2d3e28dacb595bedcf",
        "command_env": {"command": "secret-gen", "environment": {}},
    }

    receipt = store.ingest_bytes(data, metadata, access_class=AccessClass.PRIVATE)

    # Public request fails
    with pytest.raises(ArtifactAccessDeniedError, match="requires private access"):
        store.get_content(receipt.receipt_id, requester_access=AccessClass.PUBLIC)

    # Private request succeeds
    retrieved = store.get_content(
        receipt.receipt_id, requester_access=AccessClass.PRIVATE
    )
    assert retrieved == data


def test_hazard_prevention_traversal_control_chars_and_secrets(
    store: UniversalArtifactStore,
) -> None:
    data = b"Sample artifact"

    # Traversal hazard in path property
    bad_meta_path = {
        "source_commit": "4548ade4322b8ab8aa483a2d3e28dacb595bedcf",
        "command_env": {"command": "run", "environment": {}},
        "filename": "../../etc/passwd",
    }
    with pytest.raises(ArtifactIngestionError, match="Unsafe path hazard"):
        store.ingest_bytes(data, bad_meta_path)

    # Secret exposure in metadata
    secret_meta = {
        "source_commit": "4548ade4322b8ab8aa483a2d3e28dacb595bedcf",
        "command_env": {
            "command": "run",
            "environment": {"TOKEN": "ghp_" + "1234567890abcdefghijklmnopqrstuv"},
        },
    }
    with pytest.raises(ArtifactIngestionError, match="REDACTED_SECRET"):
        store.ingest_bytes(data, secret_meta)


def test_lineage_source_and_derivatives(store: UniversalArtifactStore) -> None:
    parent_data = b"Original Parent Image Data"
    parent_meta = {
        "source_commit": "4548ade4322b8ab8aa483a2d3e28dacb595bedcf",
        "command_env": {"command": "parent-gen", "environment": {}},
    }
    parent_receipt = store.ingest_bytes(parent_data, parent_meta)

    child_data = b"Thumbnailed Derivative Data"
    child_meta = {
        "source_commit": "4548ade4322b8ab8aa483a2d3e28dacb595bedcf",
        "command_env": {"command": "thumbnail-gen", "environment": {}},
    }
    child_receipt = store.ingest_bytes(
        child_data, child_meta, parent_artifact_id=parent_receipt.receipt_id
    )

    assert child_receipt.parent_artifact_id == parent_receipt.receipt_id

    # Re-fetch parent receipt to verify derivative_ids updated
    updated_parent = store.get_receipt(parent_receipt.receipt_id)
    assert child_receipt.receipt_id in updated_parent.derivative_ids


def test_retention_and_gc_fencing_merge_evidence(store: UniversalArtifactStore) -> None:
    # 1. Ingest artifact with merge evidence reference
    data_merge = b"Merge Evidence Payload"
    meta_merge = {
        "source_commit": "4548ade4322b8ab8aa483a2d3e28dacb595bedcf",
        "command_env": {"command": "merge-check", "environment": {}},
    }
    merge_receipt = store.ingest_bytes(
        data_merge, meta_merge, merge_evidence_refs=["merge_req_999"]
    )

    # 2. Ingest unreferenced orphan artifact
    data_orphan = b"Orphan Temp Payload"
    meta_orphan = {
        "source_commit": "4548ade4322b8ab8aa483a2d3e28dacb595bedcf",
        "command_env": {"command": "temp-run", "environment": {}},
    }
    orphan_receipt = store.ingest_bytes(data_orphan, meta_orphan)

    # Run GC with min_age_seconds=0
    summary = store.run_garbage_collection(min_age_seconds=0.0)

    assert summary["marker"] == UNIVERSAL_ARTIFACT_STORE_MARKER
    assert orphan_receipt.receipt_id in summary["deleted_receipts"]
    assert merge_receipt.receipt_id not in summary["deleted_receipts"]
    assert summary["fenced_merge_evidence_count"] >= 1

    # Merge receipt still exists
    assert (
        store.get_receipt(merge_receipt.receipt_id).receipt_id
        == merge_receipt.receipt_id
    )

    # Orphan receipt is gone
    with pytest.raises(ArtifactNotFoundError):
        store.get_receipt(orphan_receipt.receipt_id)


def test_concurrent_same_content_ingestion(store: UniversalArtifactStore) -> None:
    data = b"Concurrent Ingestion Content Payload"
    meta = {
        "source_commit": "4548ade4322b8ab8aa483a2d3e28dacb595bedcf",
        "command_env": {"command": "parallel-job", "environment": {}},
    }

    def ingest_task(idx: int):
        task_meta = dict(meta, task_index=idx)
        return store.ingest_bytes(data, task_meta)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(ingest_task, i) for i in range(16)]
        receipts = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(receipts) == 16
    # All receipts should have the exact same content digest
    content_digests = {r.content_digest for r in receipts}
    assert len(content_digests) == 1
