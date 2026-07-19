from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

from prismatic.agy_completed_work import (
    AGY_COMPLETED_WORK_INGESTION_MARKER,
    AGY_COMPLETED_WORK_INTEGRATION_GATE_MARKER,
    AGY_PACKET_NORMALIZATION_MARKER,
    AgyCompletedWorkStore,
    completed_work_id,
    ingest_completed_work_file,
    ingest_completed_work_text,
    normalize_agy_result_packet,
    packet_record_from_file,
    packet_record_from_text,
    parse_completed_work_packet_text,
    persist_packet_record,
    get_packet_record,
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


def compact_completed_work_text(
    *, result: str = "PASS", marker: str = "AGY_LOG_PACKET_OK"
) -> str:
    return f"""
COMMAND=$HOME/.prismatic/venv_stable/bin/python -m pytest tests/test_agy_completed_work.py -q
RESULT={result}
LOG=/tmp/agy-log-packet-proof.log
SCOPE=completed-work log ingestion gate
AD_HOC_OR_CANONICAL=ad-hoc targeted
NOT_CLAIMING=production_deployed,auto_merge_enabled,real_github_pr_created,real_Linear_writeback_posted,bulk_agent_dispatch,overnight_autopilot
MARKER={marker}
AGENT=agy
ISSUE_IDENTIFIER=GRO-AGY-LOG-1
SOURCE_BRANCH=feature/agy-log-packet
SOURCE_PATH={Path.home() / ".prismatic" / "agy-result-packets" / "GRO-AGY-LOG-1"}
BASE_BRANCH=main
CHANGED_FILES=prismatic/agy_completed_work.py,tests/test_agy_completed_work.py
RESULT_SUMMARY=AGY compact log packet ingested safely
VERIFICATION_LANE=backend-api
""".strip()


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
    assert row.integration_classification == "pass_ready_for_review"
    assert row.as_dict()["integration_classification"] == "pass_ready_for_review"
    assert (
        row.as_dict()["integration_marker"]
        == AGY_COMPLETED_WORK_INTEGRATION_GATE_MARKER
    )
    assert row.as_dict()["linear_writeback"]["posted"] is False
    assert row.as_dict()["linear_writeback"]["dry_run"] is True
    assert (
        row.as_dict()["linear_writeback"]["marker"]
        == AGY_COMPLETED_WORK_INTEGRATION_GATE_MARKER
    )
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


def test_ingest_completed_work_text_persists_log_packet(tmp_path):
    row = ingest_completed_work_text(
        compact_completed_work_text(), db_path=tmp_path / "agy_completed_work.db"
    )

    assert row.classification == "merge_ready"
    assert row.integration_classification == "pass_ready_for_review"
    assert row.proof_result == "PASS"
    assert row.proof_marker == "AGY_LOG_PACKET_OK"
    assert row.as_dict()["linear_writeback"]["posted"] is False
    assert (
        row.as_dict()["linear_writeback"]["body"].count(
            "REAL_LINEAR_WRITEBACK_POSTED=false"
        )
        == 1
    )
    assert "real_Linear_writeback_posted" in row.non_claims
    assert "production_deployed" in row.non_claims


def test_ingest_completed_work_file_accepts_fred_or_jules_compatible_packet(tmp_path):
    packet_path = tmp_path / "jules-result.log"
    packet_path.write_text(
        compact_completed_work_text(marker="JULES_SESSION_RESULT_OK").replace(
            "AGENT=agy", "AGENT=jules"
        ),
        encoding="utf-8",
    )

    row = ingest_completed_work_file(
        packet_path, db_path=tmp_path / "agy_completed_work.db"
    )

    assert row.agent == "jules"
    assert row.integration_classification == "pass_ready_for_review"
    assert row.proof_marker == "JULES_SESSION_RESULT_OK"


def test_completed_work_text_rejects_template_placeholder_marker():
    template = compact_completed_work_text(marker="<EXPECTED_OK_MARKER>")

    try:
        parse_completed_work_packet_text(template)
    except ValueError as exc:
        assert "template completed-work packet field is not proof: MARKER" in str(exc)
    else:  # pragma: no cover - defensive fail clarity
        raise AssertionError("template marker should not count as completed work")


def test_completed_work_text_rejects_missing_required_packet_lines():
    missing_log = compact_completed_work_text().replace(
        "LOG=/tmp/agy-log-packet-proof.log\n", ""
    )

    try:
        parse_completed_work_packet_text(missing_log)
    except ValueError as exc:
        assert "missing completed-work packet fields: LOG" in str(exc)
    else:  # pragma: no cover - defensive fail clarity
        raise AssertionError("missing LOG should be repairable invalid work")


def test_failed_and_blocked_packets_get_bridge_classifications(tmp_path):
    failed = ingest_completed_work_text(
        compact_completed_work_text(result="FAIL", marker="AGY_LOG_PACKET_FAIL"),
        db_path=tmp_path / "agy_completed_work.db",
    )
    blocked = ingest_completed_work_text(
        compact_completed_work_text(result="BLOCKED", marker="AGY_LOG_PACKET_BLOCKED"),
        db_path=tmp_path / "agy_completed_work.db",
    )

    assert failed.integration_classification == "failed_needs_repair"
    assert failed.as_dict()["linear_writeback"]["status"] == "needs_repair"
    assert blocked.integration_classification == "blocked_needs_operator"
    assert blocked.as_dict()["linear_writeback"]["status"] == "blocked_needs_operator"


def test_packet_record_classifies_valid_blocked_failed_and_malformed(tmp_path):
    valid = packet_record_from_text(
        compact_completed_work_text(marker="EXPECTED_PACKET_OK"),
        expected_marker="EXPECTED_PACKET_OK",
        launch_record_id="launch-valid",
        context_metadata={
            "context_pack_path": "contexts/CONTEXT_PACK.md",
            "work_packet_path": "contexts/WORK_PACKET.md",
            "packet_contract_path": "contexts/PACKET_CONTRACT.md",
        },
    )
    blocked = packet_record_from_text(
        compact_completed_work_text(result="BLOCKED", marker="AGY_BLOCKED_PACKET_OK"),
        expected_marker="AGY_BLOCKED_PACKET_OK",
    )
    failed = packet_record_from_text(
        compact_completed_work_text(result="FAIL", marker="AGY_FAILED_PACKET_OK"),
        expected_marker="AGY_FAILED_PACKET_OK",
    )
    malformed = packet_record_from_text(
        compact_completed_work_text().replace(
            "NOT_CLAIMING=production_deployed,auto_merge_enabled,real_github_pr_created,real_Linear_writeback_posted,bulk_agent_dispatch,overnight_autopilot\n",
            "",
        ),
        expected_marker="AGY_LOG_PACKET_OK",
    )

    assert valid["classification"] == "packet_valid"
    assert valid["result"] == "PASS"
    assert valid["marker"] == "EXPECTED_PACKET_OK"
    assert valid["launch_record_id"] == "launch-valid"
    assert valid["context_pack_path"] == "contexts/CONTEXT_PACK.md"
    assert valid["work_packet_path"] == "contexts/WORK_PACKET.md"
    assert valid["packet_contract_path"] == "contexts/PACKET_CONTRACT.md"
    assert blocked["classification"] == "packet_blocked"
    assert blocked["result"] == "BLOCKED"
    assert failed["classification"] == "packet_failed"
    assert failed["result"] == "FAIL"
    assert malformed["classification"] == "packet_malformed"
    assert "NOT_CLAIMING" in malformed["missing_fields"]


def test_packet_record_classifies_missing_and_marker_conflict(tmp_path):
    missing_file = tmp_path / "missing-output.log"
    missing = packet_record_from_file(
        missing_file, expected_marker="EXPECTED_PACKET_OK"
    )
    conflict = packet_record_from_text(
        compact_completed_work_text(marker="ACTUAL_PACKET_OK"),
        expected_marker="EXPECTED_PACKET_OK",
    )

    assert missing["classification"] == "packet_missing"
    assert missing["result"] is None
    assert conflict["classification"] == "needs_manual_review"
    assert conflict["marker"] == "ACTUAL_PACKET_OK"
    assert conflict["expected_marker"] == "EXPECTED_PACKET_OK"


def test_packet_record_redacts_token_like_content_and_persists_readback(tmp_path):
    text = (
        compact_completed_work_text()
        .replace(
            "RESULT_SUMMARY=AGY log packet integration proof\n",
            "RESULT_SUMMARY=used token=" + "ghp_" + "a" * 36 + " for proof\n",
        )
        .replace(
            "SCOPE=completed-work log ingestion gate\n",
            "SCOPE=api_key=" + "b" * 36 + " should redact\n",
        )
    )
    record = packet_record_from_text(text, expected_marker="AGY_LOG_PACKET_OK")
    stored = persist_packet_record(record, db_path=tmp_path / "agy_completed_work.db")
    readback = get_packet_record(
        stored["id"], db_path=tmp_path / "agy_completed_work.db"
    )

    assert readback["classification"] == "packet_valid"
    assert "[REDACTED]" in readback["proof_summary"]
    assert "ghp_" not in json.dumps(readback)
    assert "sk-supersecret" not in json.dumps(readback)


def test_completed_work_row_exposes_packet_classification_and_normalized_record(
    tmp_path,
):
    row = ingest_completed_work_text(
        compact_completed_work_text(), db_path=tmp_path / "agy_completed_work.db"
    )
    payload = row.as_dict()

    assert payload["packet_classification"] == "packet_valid"
    assert payload["normalized_record"]["classification"] == "packet_valid"
    assert payload["normalized_record"]["marker"] == "AGY_LOG_PACKET_OK"
    assert payload["normalized_record"]["log_path"] == "/tmp/agy-log-packet-proof.log"


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
