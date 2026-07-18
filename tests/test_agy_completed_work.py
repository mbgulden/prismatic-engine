from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

from prismatic.agy_completed_work import (
    AGY_COMPLETED_WORK_INGESTION_MARKER,
    AGY_PACKET_NORMALIZATION_MARKER,
    AgyCompletedWorkStore,
    completed_work_id,
    normalize_agy_result_packet,
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


def blocked_one_task_packet():
    return {
        "agent": "agy",
        "base_branch": "main",
        "changed_files": ["agy-one-task-lane-note.md"],
        "issue_identifier": "LOCAL-AGY-CANARY-ONE-TASK-20260717",
        "lane_scope": "docs",
        "marker": "AGY_RESULT_PACKET_INGESTED_OK",
        "non_claims": [
            "bulk_agy_dispatch",
            "overnight_autopilot_ready",
            "auto_merge_enabled",
            "production_deploy",
            "real_github_pr_created",
        ],
        "proof": {
            "command": "local canary artifact write; no repo mutation",
            "log": "/tmp/agy-proof.log",
            "marker": "AGY_RESULT_PACKET_INGESTED_OK",
            "non_claims": [
                "bulk_agy_dispatch",
                "overnight_autopilot_ready",
                "auto_merge_enabled",
                "production_deploy",
                "real_github_pr_created",
            ],
            "result": "PASS",
            "scope": "docs-only local canary AGY one-task dry run",
        },
        "result_summary": "AGY one-task dry run produced docs-only local canary artifact",
        "risk": "low",
        "source_branch": "local/agy-one-task-dry-run",
        "verification_lane": "docs",
    }


def canonical_agy_packet():
    return {
        "agent": "agy",
        "issue_identifier": "GRO-AGY-NORM-1",
        "branch": "feature/agy-normalized-result",
        "base_branch": "main",
        "merge_lane": "docs",
        "changed_files": ["docs/agy-result-packet-contract.md"],
        "result_artifacts": [
            {
                "path": str(
                    Path.home()
                    / ".prismatic"
                    / "agy-results"
                    / "GRO-AGY-NORM-1"
                    / "RESULT.md"
                )
            }
        ],
        "verification": {
            "result": "PASS",
            "commands": ["python3 -m pytest -q tests/test_agy_completed_work.py"],
            "log_path": "/tmp/fred-agy-normalization.log",
            "ad_hoc_or_canonical": "ad-hoc targeted",
        },
        "non_claims": ["production_deploy", "auto_merge_enabled"],
        "marker": "AGY_TASK_RESULT_PACKET_OK",
    }


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
    p["proof"]["non_claims"] = [
        "production_deployed",
        "auto_merge",
        "bulk_agy_dispatch",
    ]

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


def test_canonical_agy_result_packet_normalizes_source_path_and_merges_ready(tmp_path):
    store = AgyCompletedWorkStore(tmp_path / "agy_completed_work.db")

    row = store.ingest(canonical_agy_packet())

    assert row.classification == "merge_ready"
    assert row.source_branch == "feature/agy-normalized-result"
    assert row.source_path == str(
        Path.home() / ".prismatic" / "agy-results" / "GRO-AGY-NORM-1" / "RESULT.md"
    )
    assert row.packet["normalization"]["marker"] == AGY_PACKET_NORMALIZATION_MARKER
    assert row.packet["lane_scope"]["allowed_paths"][:3] == [
        "docs/",
        "research/",
        "reports/",
    ]
    assert (
        row.packet["proof"]["command"]
        == "python3 -m pytest -q tests/test_agy_completed_work.py"
    )
    assert row.non_claims == ("production_deploy", "auto_merge_enabled")


def test_blocked_one_task_fixture_normalizes_out_of_rejected_contract(tmp_path):
    store = AgyCompletedWorkStore(tmp_path / "agy_completed_work.db")

    row = store.ingest(blocked_one_task_packet())

    assert row.classification != "rejected"
    assert row.source_path == str(
        Path.home()
        / ".prismatic"
        / "agy-result-packets"
        / "local-agy-canary-one-task-20260717"
    )
    assert row.packet["normalization"]["marker"] == AGY_PACKET_NORMALIZATION_MARKER
    assert row.packet["lane_scope"]["touched_paths"] == ["agy-one-task-lane-note.md"]
    # The local/* canary branch is still not merge-ready; that is a scope/manual-review decision,
    # not an invalid handoff-contract rejection.
    assert row.classification == "manual_review_scope"
    assert "source_branch must be a feature/* branch" in row.gate["reasons"]


def test_missing_source_path_and_underivable_provenance_still_rejects(tmp_path):
    store = AgyCompletedWorkStore(tmp_path / "agy_completed_work.db")
    p = canonical_agy_packet()
    p.pop("issue_identifier")
    p.pop("branch")
    p.pop("result_artifacts")

    row = store.ingest(p)

    assert row.classification == "rejected"
    assert "missing packet fields: source_branch, source_path" in row.gate["reasons"]


def test_source_path_derivation_rejects_unsafe_artifact_paths(tmp_path):
    store = AgyCompletedWorkStore(tmp_path / "agy_completed_work.db")
    unsafe = canonical_agy_packet()
    unsafe["result_artifacts"] = [{"path": str(Path.home() / ".ssh" / "id_rsa")}]

    row = store.ingest(unsafe)

    assert row.classification == "rejected"
    assert row.source_path is None
    assert "missing packet fields: source_path" in row.gate["reasons"]

    traversal = canonical_agy_packet()
    traversal["result_artifacts"] = [
        {"path": str(Path.home() / "work" / "prismatic-engine" / ".." / "secret.md")}
    ]
    row2 = store.ingest(traversal)
    assert row2.classification == "rejected"
    assert row2.source_path is None


def test_derived_source_path_is_stable_and_deterministic():
    first = normalize_agy_result_packet(blocked_one_task_packet())
    second = normalize_agy_result_packet(blocked_one_task_packet())

    assert first["source_path"] == second["source_path"]
    assert first["source_path"] == str(
        Path.home()
        / ".prismatic"
        / "agy-result-packets"
        / "local-agy-canary-one-task-20260717"
    )


def test_packet_normalization_preserves_non_claims_without_positive_proof():
    normalized = normalize_agy_result_packet(blocked_one_task_packet())

    assert normalized["proof"]["non_claims"] == [
        "bulk_agy_dispatch",
        "overnight_autopilot_ready",
        "auto_merge_enabled",
        "production_deploy",
        "real_github_pr_created",
    ]
    assert "auto_merge" not in normalized["proof"]


def test_ingest_cli_runs_from_outside_repo(tmp_path):
    packet_path = tmp_path / "packet.json"
    db_path = tmp_path / "completed_work.db"
    packet_path.write_text(json.dumps(packet()))

    proc = subprocess.run(
        [
            sys.executable,
            str(
                Path(__file__).resolve().parents[1] / "scripts" / "ingest_agy_result.py"
            ),
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
    assert (
        payload["completed_work"]["ingestion_marker"]
        == AGY_COMPLETED_WORK_INGESTION_MARKER
    )
