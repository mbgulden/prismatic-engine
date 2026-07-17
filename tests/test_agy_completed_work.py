from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

from prismatic.agy_completed_work import (
    AGY_COMPLETED_WORK_INGESTION_MARKER,
    AgyCompletedWorkStore,
    completed_work_id,
)
from prismatic.completed_work_gate import (
    AGY_COMPLETED_WORK_MARKER,
    GateClassification,
    classify_completed_work,
    demo_completed_work_packet,
    normalize_non_claims,
)


def packet():
    return deepcopy(demo_completed_work_packet())


def test_ingest_persists_packet_and_gate_state(tmp_path):
    store = AgyCompletedWorkStore(tmp_path / "agy_completed_work.db")
    row = store.ingest(packet())

    assert row.id.startswith("agy-cw-")
    assert row.ingestion_marker == AGY_COMPLETED_WORK_INGESTION_MARKER
    assert row.gate_marker == AGY_COMPLETED_WORK_MARKER
    assert row.classification == "merge_ready"
    assert row.eligible_for_merge is True
    assert row.gate["classification"] == "merge_ready"
    assert row.packet["agent"] == "agy"
    assert row.non_claims == ("production_deployed", "auto_merge")

    fetched = store.get(row.id)
    assert fetched.as_dict() == row.as_dict()


def test_ingest_upserts_deterministic_id(tmp_path):
    store = AgyCompletedWorkStore(tmp_path / "agy_completed_work.db")
    p = packet()
    expected_id = completed_work_id(p)

    first = store.ingest(p)
    second = store.ingest(p)

    assert first.id == expected_id
    assert second.id == expected_id
    assert len(store.list()) == 1
    assert second.updated_at >= first.updated_at


def test_ingest_persists_blocked_gate_state(tmp_path):
    store = AgyCompletedWorkStore(tmp_path / "agy_completed_work.db")
    p = packet()
    p["proof"]["result"] = "FAIL"

    row = store.ingest(p)

    assert row.classification == "blocked_failed_verification"
    assert row.eligible_for_merge is False
    assert row.gate["linear_status"] == "Blocked: verification failed"


def test_list_orders_newest_first(tmp_path):
    store = AgyCompletedWorkStore(tmp_path / "agy_completed_work.db")
    p1 = packet()
    p2 = packet()
    p2["source_branch"] = "feature/agy-second-result"

    first = store.ingest(p1)
    second = store.ingest(p2)

    assert [row.id for row in store.list(limit=2)] == [second.id, first.id]


def test_non_claims_list_avoids_false_positive_claim_validation():
    p = packet()
    p["proof"].pop("not_claiming", None)
    p["proof"]["non_claims"] = ["production_deployed", "auto_merge", "bulk_agy_dispatch"]

    state = classify_completed_work(p)

    assert state.classification == GateClassification.MERGE_READY
    assert normalize_non_claims(p["proof"]) == (
        "production_deployed",
        "auto_merge",
        "bulk_agy_dispatch",
    )


def test_legacy_not_claiming_is_still_accepted_and_normalized():
    p = packet()
    p["proof"].pop("non_claims", None)
    p["proof"]["not_claiming"] = "production_deployed, auto_merge"

    state = classify_completed_work(p)

    assert state.classification == GateClassification.MERGE_READY
    assert normalize_non_claims(p["proof"]) == ("production_deployed", "auto_merge")


def test_ingest_cli_runs_from_outside_repo(tmp_path):
    packet_path = tmp_path / "packet.json"
    db_path = tmp_path / "completed_work.db"
    packet_path.write_text(json.dumps(packet()))

    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "scripts" / "ingest_agy_result.py"),
            str(packet_path),
            "--db",
            str(db_path),
        ],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=True,
    )
    payload = json.loads(proc.stdout)

    assert payload["status"] == "ok"
    assert payload["completed_work"]["classification"] == "merge_ready"
    assert payload["completed_work"]["ingestion_marker"] == AGY_COMPLETED_WORK_INGESTION_MARKER
