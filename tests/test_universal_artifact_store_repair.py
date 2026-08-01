import concurrent.futures
import json
import multiprocessing
import shutil
import tempfile

import pytest

from prismatic.universal_artifact_store import (
    ArtifactIngestionError,
    UniversalArtifactStore,
    compute_receipt_integrity_digest,
)


@pytest.fixture
def temp_store():
    temp_dir = tempfile.mkdtemp()
    store = UniversalArtifactStore(temp_dir)
    yield store
    shutil.rmtree(temp_dir)


def test_owner_only_permissions(temp_store):
    for d in (
        temp_store.root_dir,
        temp_store.receipts_dir,
        temp_store.quarantine_dir,
        temp_store.proof_logs_dir,
        temp_store.storage_adapter.blobs_dir,
        temp_store.leases_dir,
    ):
        assert (d.stat().st_mode & 0o777) == 0o700

    data = b"Permission Check Content"
    meta = {
        "source_commit": "4548ade4322b8ab8aa483a2d3e28dacb595bedcf",
        "command_env": {"command": "test", "environment": {}},
    }
    receipt = temp_store.ingest_bytes(data, meta)

    receipt_path = temp_store.receipts_dir / f"{receipt.receipt_id}.json"
    assert (receipt_path.stat().st_mode & 0o777) == 0o600

    blob_path = temp_store.storage_adapter._blob_path(receipt.content_digest)
    assert (blob_path.stat().st_mode & 0o777) == 0o600

    log_file = temp_store.proof_logs_dir / "proof_events.jsonl"
    assert (log_file.stat().st_mode & 0o777) == 0o600


def test_secret_safe_negative_paths(temp_store):
    secret_meta = {
        "source_commit": "4548ade4322b8ab8aa483a2d3e28dacb595bedcf",
        "command_env": {
            "command": "run",
            "environment": {"SECRET": "ghp_" + "1234567890abcdefghijklmnopqrstuv"},
        },
    }
    with pytest.raises(ArtifactIngestionError, match="Ingestion rejected"):
        temp_store.ingest_bytes(b"content", secret_meta)

    assert len(list(temp_store.receipts_dir.glob("*.json"))) == 0
    assert len(list(temp_store.quarantine_dir.glob("*"))) == 0

    secret_content = b"Some private key: -----BEGIN " + b"RSA " + b"PRIVATE KEY-----"
    safe_meta = {
        "source_commit": "4548ade4322b8ab8aa483a2d3e28dacb595bedcf",
        "command_env": {"command": "run", "environment": {}},
    }
    with pytest.raises(ArtifactIngestionError, match="Ingestion rejected"):
        temp_store.ingest_bytes(secret_content, safe_meta)

    assert len(list(temp_store.receipts_dir.glob("*.json"))) == 0
    assert len(list(temp_store.quarantine_dir.glob("*"))) == 0


def test_url_userinfo_in_command_rejection(temp_store):
    userinfo_meta = {
        "source_commit": "4548ade4322b8ab8aa483a2d3e28dacb595bedcf",
        "command_env": {
            "command": "curl https://operator:credential-material@example.invalid/path",
            "environment": {},
        },
    }
    with pytest.raises(ArtifactIngestionError, match="Ingestion rejected"):
        temp_store.ingest_bytes(b"safe content", userinfo_meta)


def test_recomputed_receipt_tamper_rejection(temp_store):
    data = b"Tamper Check Content"
    meta = {
        "source_commit": "4548ade4322b8ab8aa483a2d3e28dacb595bedcf",
        "command_env": {"command": "test", "environment": {}},
    }
    receipt = temp_store.ingest_bytes(data, meta)

    receipt_path = temp_store.receipts_dir / f"{receipt.receipt_id}.json"
    parsed = json.loads(receipt_path.read_text())
    parsed["byte_size"] = parsed["byte_size"] + 1
    parsed["evidence_digest"] = compute_receipt_integrity_digest(parsed)

    receipt_path.write_text(json.dumps(parsed))

    with pytest.raises(ArtifactIngestionError, match="Receipt tamper detected"):
        temp_store.get_receipt(receipt.receipt_id)


def test_deterministic_parallel_same_input_receipts(temp_store):
    data = b"Deterministic Ingestion Content"
    meta = {
        "source_commit": "4548ade4322b8ab8aa483a2d3e28dacb595bedcf",
        "command_env": {"command": "task", "environment": {}},
    }

    def run_ingest():
        return temp_store.ingest_bytes(data, meta)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futures = [ex.submit(run_ingest) for _ in range(10)]
        receipts = [f.result() for f in futures]

    first_id = receipts[0].receipt_id
    for r in receipts:
        assert r.receipt_id == first_id

    receipt_files = list(temp_store.receipts_dir.glob("*.json"))
    assert len(receipt_files) == 1
    assert receipt_files[0].stem == first_id


def test_conflicting_metadata_behavior(temp_store):
    data = b"Same Content Different Meta"
    meta1 = {
        "source_commit": "1111111111111111111111111111111111111111",
        "command_env": {"command": "task-1", "environment": {}},
    }
    meta2 = {
        "source_commit": "2222222222222222222222222222222222222222",
        "command_env": {"command": "task-2", "environment": {}},
    }

    r1 = temp_store.ingest_bytes(data, meta1)
    r2 = temp_store.ingest_bytes(data, meta2)

    assert r1.receipt_id != r2.receipt_id
    assert r1.content_digest == r2.content_digest
    assert r1.storage_id == r2.storage_id


def test_exact_id_path_fencing(temp_store):
    with pytest.raises(ArtifactIngestionError, match="Malformed receipt ID"):
        temp_store.get_receipt("../receipts/invalid")

    with pytest.raises(ArtifactIngestionError, match="Malformed receipt ID"):
        temp_store.get_receipt("art_12345/../../etc/passwd")


def test_symlink_hazard(temp_store, tmp_path):
    target_file = tmp_path / "target.txt"
    target_file.write_text("secrets")

    symlink_file = tmp_path / "link.txt"
    symlink_file.symlink_to(target_file)

    meta = {
        "source_commit": "4548ade4322b8ab8aa483a2d3e28dacb595bedcf",
        "command_env": {"command": "test", "environment": {}},
    }
    with pytest.raises(ArtifactIngestionError, match="Symlink input hazard detected"):
        temp_store.ingest_file(symlink_file, meta)


def test_userinfo_and_control_char_rejection(temp_store):
    meta = {
        "source_commit": "4548ade4322b8ab8aa483a2d3e28dacb595bedcf",
        "command_env": {"command": "test", "environment": {}},
    }

    with pytest.raises(ArtifactIngestionError, match="control characters"):
        temp_store.ingest_file("file\x00name.txt", meta)

    with pytest.raises(ArtifactIngestionError, match="URL or userinfo structure"):
        temp_store.ingest_file("user:pass@host/file.txt", meta)


def test_cross_process_stale_gc(temp_store):
    data = b"GC Stale Check Content"
    meta = {
        "source_commit": "4548ade4322b8ab8aa483a2d3e28dacb595bedcf",
        "command_env": {"command": "test", "environment": {}},
    }
    receipt = temp_store.ingest_bytes(data, meta)

    summary = temp_store.run_garbage_collection(min_age_seconds=10.0)
    assert receipt.receipt_id not in summary["deleted_receipts"]
    assert receipt.content_digest not in summary["deleted_blobs"]


def test_process_lease_protection_during_gc(temp_store):
    data = b"Lease Protected Content"
    meta = {
        "source_commit": "4548ade4322b8ab8aa483a2d3e28dacb595bedcf",
        "command_env": {"command": "test", "environment": {}},
    }
    receipt = temp_store.ingest_bytes(data, meta)

    temp_store.acquire_lease(
        holder_id="test_worker_1",
        active_receipts=[receipt.receipt_id],
        ttl_seconds=60.0,
    )

    summary = temp_store.run_garbage_collection(min_age_seconds=0.0)
    assert receipt.receipt_id not in summary["deleted_receipts"]


def _acquire_worker(root_dir, holder_id, queue):
    store = UniversalArtifactStore(root_dir)
    try:
        res = store.acquire_lease(holder_id=holder_id, ttl_seconds=60.0)
        queue.put(("SUCCESS", res))
    except Exception as exc:
        queue.put(("ERROR", str(exc)))


def test_multiprocess_simultaneous_acquire_one_winner(temp_store):
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    processes = []
    holder_id = "contested_holder"

    for _ in range(5):
        p = ctx.Process(
            target=_acquire_worker,
            args=(str(temp_store.root_dir), holder_id, queue),
        )
        processes.append(p)

    for p in processes:
        p.start()
    for p in processes:
        p.join()

    results = []
    while not queue.empty():
        results.append(queue.get())

    successes = [r for r in results if r[0] == "SUCCESS"]
    errors = [r for r in results if r[0] == "ERROR"]

    assert len(successes) == 1
    assert len(errors) == 4
    winner_lease = successes[0][1]
    assert winner_lease["holder_id"] == holder_id
    assert winner_lease["generation"] == 1
    assert "lease_token" in winner_lease


def test_same_principal_reacquire_without_token(temp_store):
    lease1 = temp_store.acquire_lease(holder_id="holder_alpha", ttl_seconds=60.0)
    token1 = lease1["lease_token"]

    # Reacquire without token -> fails
    with pytest.raises(ArtifactIngestionError, match="missing or stale lease_token"):
        temp_store.acquire_lease(holder_id="holder_alpha", ttl_seconds=60.0)

    # Reacquire with wrong token -> fails
    with pytest.raises(ArtifactIngestionError, match="missing or stale lease_token"):
        temp_store.acquire_lease(
            holder_id="holder_alpha", lease_token="bad_token_12345", ttl_seconds=60.0
        )

    # Renew with correct token and generation -> succeeds and increments generation
    lease2 = temp_store.renew_lease(
        holder_id="holder_alpha",
        lease_token=token1,
        expected_generation=1,
        ttl_seconds=60.0,
    )
    assert lease2["generation"] == 2
    assert lease2["lease_token"] == token1


def test_stale_token_renew_after_successor_generation(temp_store):
    lease1 = temp_store.acquire_lease(holder_id="holder_beta", ttl_seconds=60.0)
    token1 = lease1["lease_token"]

    # Renew to Gen 2
    lease2 = temp_store.renew_lease(
        holder_id="holder_beta",
        lease_token=token1,
        expected_generation=1,
        ttl_seconds=60.0,
    )
    assert lease2["generation"] == 2

    # Attempt renew expecting Gen 1 (stale generation)
    with pytest.raises(
        ArtifactIngestionError, match="expected generation 1 does not match"
    ):
        temp_store.renew_lease(
            holder_id="holder_beta",
            lease_token=token1,
            expected_generation=1,
            ttl_seconds=60.0,
        )


def test_stale_release_protection(temp_store):
    lease1 = temp_store.acquire_lease(holder_id="holder_gamma", ttl_seconds=60.0)
    token1 = lease1["lease_token"]

    # Stale release with wrong token
    with pytest.raises(ArtifactIngestionError, match="lease_token mismatch"):
        temp_store.release_lease(
            "holder_gamma", lease_token="wrong_token_999", expected_generation=1
        )

    # Lease file must still exist
    assert (temp_store.leases_dir / "holder_gamma.json").is_file()

    # Renew to Gen 2
    temp_store.renew_lease(
        holder_id="holder_gamma",
        lease_token=token1,
        expected_generation=1,
        ttl_seconds=60.0,
    )

    # Stale release expecting Gen 1
    with pytest.raises(
        ArtifactIngestionError, match="expected generation 1 does not match"
    ):
        temp_store.release_lease(
            "holder_gamma", lease_token=token1, expected_generation=1
        )
    assert (temp_store.leases_dir / "holder_gamma.json").is_file()

    # Valid release matching token and Gen 2
    assert temp_store.release_lease(
        "holder_gamma", lease_token=token1, expected_generation=2
    )
    assert not (temp_store.leases_dir / "holder_gamma.json").is_file()

    # Replay release -> replay safe
    assert temp_store.release_lease(
        "holder_gamma", lease_token=token1, expected_generation=2
    )


def test_release_missing_stale_wrong_type_generation_byte_preservation(temp_store):
    lease = temp_store.acquire_lease(holder_id="gen_fence_holder", ttl_seconds=60.0)
    token = lease["lease_token"]
    lease_file = temp_store.leases_dir / "gen_fence_holder.json"
    initial_bytes = lease_file.read_bytes()

    # Missing generation
    with pytest.raises(ArtifactIngestionError, match="expected_generation"):
        temp_store.release_lease("gen_fence_holder", lease_token=token)
    assert lease_file.read_bytes() == initial_bytes

    # Boolean generation
    with pytest.raises(ArtifactIngestionError, match="expected_generation"):
        temp_store.release_lease(
            "gen_fence_holder", lease_token=token, expected_generation=True
        )
    assert lease_file.read_bytes() == initial_bytes

    # String generation
    with pytest.raises(ArtifactIngestionError, match="expected_generation"):
        temp_store.release_lease(
            "gen_fence_holder", lease_token=token, expected_generation="1"
        )
    assert lease_file.read_bytes() == initial_bytes

    # Stale / wrong generation
    with pytest.raises(
        ArtifactIngestionError, match="expected generation 999 does not match"
    ):
        temp_store.release_lease(
            "gen_fence_holder", lease_token=token, expected_generation=999
        )
    assert lease_file.read_bytes() == initial_bytes

    # Successful release with exact token + exact current generation
    assert temp_store.release_lease(
        "gen_fence_holder", lease_token=token, expected_generation=1
    )
    assert not lease_file.is_file()

    # Replay-safe when file genuinely does not exist
    assert temp_store.release_lease(
        "gen_fence_holder", lease_token=token, expected_generation=1
    )


def test_malformed_nested_durable_fields_acquire_and_release_byte_preservation(
    temp_store,
):
    import datetime

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    valid_base = {
        "holder_id": "nested_holder",
        "lease_token": "a1b2c3d4e5f678901234567890abcdef",
        "generation": 1,
        "created_at": now_iso,
        "updated_at": now_iso,
        "expires_at": (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=60)
        ).isoformat(),
        "pid": 12345,
        "active_digests": [],
        "active_receipts": [],
    }

    malformed_variations = [
        # active_digests scalar instead of list
        {**valid_base, "active_digests": "not_a_list"},
        # active_digests containing integer
        {**valid_base, "active_digests": [12345]},
        # active_digests containing malformed hex digest
        {**valid_base, "active_digests": ["not_a_64_char_hex_digest"]},
        # active_receipts scalar instead of list
        {**valid_base, "active_receipts": "not_a_list"},
        # active_receipts containing malformed receipt format
        {**valid_base, "active_receipts": ["invalid_rcpt_id"]},
        # pid boolean
        {**valid_base, "pid": True},
        # pid negative
        {**valid_base, "pid": -100},
        # naive timestamp (no tzinfo)
        {**valid_base, "expires_at": "2026-07-21T12:00:00"},
        # unknown extra field
        {**valid_base, "unknown_field": "hazard"},
        # missing created_at
        {k: v for k, v in valid_base.items() if k != "created_at"},
        # holder_id stem mismatch
        {**valid_base, "holder_id": "other_holder"},
    ]

    for idx, var in enumerate(malformed_variations):
        holder_name = f"nested_holder_{idx}"
        if var.get("holder_id") == "nested_holder":
            var_dict = {**var, "holder_id": holder_name}
        else:
            var_dict = var

        lease_file = temp_store.leases_dir / f"{holder_name}.json"
        raw_bytes = json.dumps(var_dict, indent=2).encode("utf-8")
        lease_file.write_bytes(raw_bytes)

        # acquire_lease on malformed row must fail closed and preserve bytes
        with pytest.raises(ArtifactIngestionError):
            temp_store.acquire_lease(
                holder_id=holder_name,
                lease_token=var_dict.get("lease_token"),
                expected_generation=var_dict.get("generation"),
            )
        assert lease_file.is_file()
        assert lease_file.read_bytes() == raw_bytes

        # release_lease on malformed row must fail closed and preserve bytes
        with pytest.raises(ArtifactIngestionError):
            temp_store.release_lease(
                holder_name,
                lease_token=var_dict.get("lease_token", "some_token_12345"),
                expected_generation=1,
            )
        assert lease_file.is_file()
        assert lease_file.read_bytes() == raw_bytes


def test_lease_input_validation_fencing(temp_store):
    # Malformed holder_id
    for bad_holder in (
        "../bad",
        "holder/slash",
        "holder\\backslash",
        "http://user:pass@host",
        "holder\x00ctrl",
        "",
    ):
        with pytest.raises(ArtifactIngestionError):
            temp_store.acquire_lease(holder_id=bad_holder)

    # Malformed TTL
    for bad_ttl in (0, -10, float("nan"), float("inf"), True, "30"):
        with pytest.raises(ArtifactIngestionError):
            temp_store.acquire_lease(ttl_seconds=bad_ttl)

    # Malformed active receipts
    with pytest.raises(ArtifactIngestionError):
        temp_store.acquire_lease(active_receipts=["../invalid_rcpt"])

    # Malformed active digests
    with pytest.raises(ArtifactIngestionError):
        temp_store.acquire_lease(active_digests=["not_a_64_hex_digest"])


def test_expired_holder_mutation_rejection(temp_store):
    # Acquire short TTL lease
    lease = temp_store.acquire_lease(holder_id="short_holder", ttl_seconds=0.01)
    token = lease["lease_token"]
    import time

    time.sleep(0.05)

    # Mutating an expired lease with token must fail closed
    with pytest.raises(ArtifactIngestionError, match="target lease is expired"):
        temp_store.renew_lease("short_holder", lease_token=token)


def test_caller_chosen_initial_identity_rejection(temp_store):
    # Caller passing lease_token on initial acquisition must be rejected without creating file
    with pytest.raises(
        ArtifactIngestionError, match="caller-supplied lease_token not allowed"
    ):
        temp_store.acquire_lease(
            holder_id="init_holder1", lease_token="caller_token_12345"
        )
    assert not (temp_store.leases_dir / "init_holder1.json").is_file()

    # Caller passing expected_generation on initial acquisition must be rejected without creating file
    with pytest.raises(
        ArtifactIngestionError, match="caller-supplied expected_generation not allowed"
    ):
        temp_store.acquire_lease(holder_id="init_holder2", expected_generation=1)
    assert not (temp_store.leases_dir / "init_holder2.json").is_file()


def test_missing_generation_on_existing_holder_rejection(temp_store):
    lease = temp_store.acquire_lease(holder_id="cas_holder", ttl_seconds=60.0)
    token = lease["lease_token"]
    lease_file = temp_store.leases_dir / "cas_holder.json"
    initial_bytes = lease_file.read_bytes()

    # Renewing existing lease without expected_generation must fail closed without byte changes
    with pytest.raises(ArtifactIngestionError, match="missing expected_generation"):
        temp_store.renew_lease(holder_id="cas_holder", lease_token=token)
    assert lease_file.read_bytes() == initial_bytes


def test_malformed_row_acquire_fail_closed(temp_store):
    lease_file = temp_store.leases_dir / "malformed_acq.json"
    raw_content = b"{corrupt json string: [1, 2, 3"
    lease_file.write_bytes(raw_content)

    with pytest.raises(ArtifactIngestionError, match="Malformed durable lease file"):
        temp_store.acquire_lease(holder_id="malformed_acq")
    assert lease_file.is_file()
    assert lease_file.read_bytes() == raw_content


def test_malformed_row_release_fail_closed(temp_store):
    lease_file = temp_store.leases_dir / "malformed_rel.json"
    raw_content = b"{corrupt json string: [1, 2, 3"
    lease_file.write_bytes(raw_content)

    with pytest.raises(ArtifactIngestionError, match="Malformed durable lease file"):
        temp_store.release_lease(
            "malformed_rel", lease_token="some_token_123", expected_generation=1
        )
    assert lease_file.is_file()
    assert lease_file.read_bytes() == raw_content


def test_gc_malformed_durable_rows_resilience(temp_store):
    # Put corrupted lease JSON
    corrupt_file = temp_store.leases_dir / "corrupt_worker.json"
    corrupt_file.write_text("{invalid json structure")

    # Put missing fields lease JSON
    missing_fields_file = temp_store.leases_dir / "missing_fields.json"
    missing_fields_file.write_text(json.dumps({"holder_id": "bad"}))

    data = b"GC Resilience Content"
    meta = {
        "source_commit": "4548ade4322b8ab8aa483a2d3e28dacb595bedcf",
        "command_env": {"command": "test", "environment": {}},
    }
    _ = temp_store.ingest_bytes(data, meta)

    # GC should execute safely without throwing exceptions
    summary = temp_store.run_garbage_collection(min_age_seconds=10.0)
    assert isinstance(summary, dict)


def _ingest_worker(root_dir, idx, queue):
    store = UniversalArtifactStore(root_dir)
    try:
        data = f"Multiprocess content {idx}".encode()
        meta = {
            "source_commit": "4548ade4322b8ab8aa483a2d3e28dacb595bedcf",
            "command_env": {"command": f"cmd_{idx}", "environment": {}},
        }
        rcpt = store.ingest_bytes(data, meta)
        queue.put(("SUCCESS", rcpt.receipt_id))
    except Exception as exc:
        queue.put(("ERROR", str(exc)))


def _gc_worker(root_dir, queue):
    store = UniversalArtifactStore(root_dir)
    try:
        summary = store.run_garbage_collection(min_age_seconds=0.0)
        queue.put(("SUCCESS", summary))
    except Exception as exc:
        queue.put(("ERROR", str(exc)))


def test_multiprocess_ingest_vs_gc_contention(temp_store):
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    processes = []

    # Launch 4 ingest workers and 2 GC workers concurrently
    for i in range(4):
        p = ctx.Process(
            target=_ingest_worker, args=(str(temp_store.root_dir), i, queue)
        )
        processes.append(p)
    for i in range(2):
        p = ctx.Process(target=_gc_worker, args=(str(temp_store.root_dir), queue))
        processes.append(p)

    for p in processes:
        p.start()
    for p in processes:
        p.join()

    results = []
    while not queue.empty():
        results.append(queue.get())

    for status, res in results:
        assert status == "SUCCESS"
