from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading

import pytest
import prismatic.agy_completed_work as completed_work_module

from prismatic.agy_completed_work import (
    AGY_COMPLETED_WORK_INGESTION_MARKER,
    AGY_COMPLETED_WORK_INTEGRATION_GATE_MARKER,
    AGY_PACKET_NORMALIZATION_MARKER,
    GOVERNANCE_PROMOTION_DECISION_READ_MODEL_MARKER,
    AgyCompletedWorkConflictError,
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
from prismatic.agy_result_packet import ResultPacketValidationError


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


def raw_agy_packet(**overrides):
    p = canonical_agy_packet()
    p.update(
        {
            "issue_identifier": "GRO-3837",
            "branch": "feature/agy-raw-result-contract",
            "risk_level": "low",
            "next_action": "merge-ready",
        }
    )
    for key, value in overrides.items():
        if key == "verification":
            p["verification"].update(value)
        else:
            p[key] = value
    return p


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
    promotion = row.as_dict()["promotion_decision"]
    assert promotion["marker"] == GOVERNANCE_PROMOTION_DECISION_READ_MODEL_MARKER
    assert promotion["status"] == "hold_needs_durable_evidence"
    assert promotion["recommendation"] == "hold_for_durable_evidence"
    assert promotion["policy_gate"] == "blocked"
    assert promotion["side_effects"]["linear_comment_posted"] is False
    assert promotion["side_effects"]["github_pr_created"] is False
    assert promotion["side_effects"]["auto_merge_enabled"] is False
    assert promotion["side_effects"]["production_deployed"] is False
    assert promotion["side_effects"]["agent_dispatched"] is False
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
    traversal["branch"] = "feature/agy-traversal-result"
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


def test_raw_agy_packet_validates_normalizes_classifies_and_retains_evidence(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / ".prismatic" / "agy-results" / "GRO-3837" / "RESULT.md"
    source.parent.mkdir(parents=True)
    source.write_text("RESULT=PASS\nMARKER=AGY_TASK_RESULT_PACKET_OK", encoding="utf-8")
    log = tmp_path / "proof.log"
    log.write_text("raw packet verification passed", encoding="utf-8")
    p = raw_agy_packet(
        result_artifacts=[str(source)],
        verification={"log_path": str(log)},
        source_commit_sha=VALID_SOURCE_SHA,
        base_commit_sha=VALID_BASE_SHA,
    )
    store = AgyCompletedWorkStore(
        tmp_path / "agy_completed_work.db", evidence_dir=tmp_path / "evidence"
    )

    row = store.ingest(p)

    assert row.classification == "merge_ready"
    assert row.source_branch == "feature/agy-raw-result-contract"
    assert row.packet["normalization"]["marker"] == AGY_PACKET_NORMALIZATION_MARKER
    assert row.evidence_retention["status"] == "complete"
    assert row.as_dict()["promotion_decision"]["policy_gate"] == "pass"


def test_malformed_raw_agy_packet_creates_no_evidence_or_sqlite_row(tmp_path):
    store = AgyCompletedWorkStore(
        tmp_path / "agy_completed_work.db", evidence_dir=tmp_path / "evidence"
    )
    bad = raw_agy_packet(branch="agy/not-feature")

    with pytest.raises(ResultPacketValidationError):
        store.ingest(bad)

    assert store.list() == []
    assert not (tmp_path / "evidence").exists()


def test_secret_bearing_raw_agy_packet_creates_no_evidence_or_sqlite_row(tmp_path):
    store = AgyCompletedWorkStore(
        tmp_path / "agy_completed_work.db", evidence_dir=tmp_path / "evidence"
    )
    secret_path = str(Path.home() / ".ssh" / "id_rsa")
    bad = raw_agy_packet(result_artifacts=[secret_path])

    with pytest.raises(ResultPacketValidationError):
        store.ingest(bad)

    assert store.list() == []
    assert not (tmp_path / "evidence").exists()


def test_raw_next_action_blocked_cannot_normalize_into_merge_ready(tmp_path):
    store = AgyCompletedWorkStore(tmp_path / "agy_completed_work.db")
    p = raw_agy_packet(
        next_action="blocked",
        verification={"result": "FAIL"},
    )

    row = store.ingest(p)

    assert row.classification == "blocked_failed_verification"
    assert row.eligible_for_merge is False
    assert row.as_dict()["promotion_decision"]["status"] == "needs_repair"


def test_raw_high_risk_manual_review_survives_normalization_and_classification(
    tmp_path,
):
    store = AgyCompletedWorkStore(tmp_path / "agy_completed_work.db")
    p = raw_agy_packet(
        merge_lane="manual-review",
        risk_level="high",
        next_action="needs-human-review",
    )

    row = store.ingest(p)

    assert row.classification == "manual_review_scope"
    assert row.eligible_for_merge is False
    assert "outside lane scope" in row.gate["reasons"][0]


def test_normalized_fred_and_jules_packets_do_not_require_agy_raw_schema(tmp_path):
    store = AgyCompletedWorkStore(tmp_path / "agy_completed_work.db")
    fred = packet()
    fred["agent"] = "fred"
    fred["source_branch"] = "feature/fred-normalized"
    fred["source_path"] = str(Path.home() / ".prismatic" / "fred" / "RESULT.md")
    jules = packet()
    jules["agent"] = "jules"
    jules["source_branch"] = "feature/jules-normalized"
    jules["source_path"] = str(Path.home() / ".prismatic" / "jules" / "RESULT.md")

    fred_row = store.ingest(fred)
    jules_row = store.ingest(jules)

    assert fred_row.classification == "merge_ready"
    assert jules_row.classification == "merge_ready"


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


VALID_SOURCE_SHA = "c" * 40
VALID_BASE_SHA = "d" * 40


def _retention_packet(
    tmp_path,
    *,
    source_path=None,
    proof_log=None,
    source_commit_sha=VALID_SOURCE_SHA,
    base_commit_sha=VALID_BASE_SHA,
    source_branch="feature/agy-durable-evidence",
):
    source = Path(source_path) if source_path is not None else tmp_path / "RESULT.md"
    log = Path(proof_log) if proof_log is not None else tmp_path / "proof.log"
    p = packet()
    p["source_path"] = str(source)
    p["proof"]["log"] = str(log)
    p["source_branch"] = source_branch
    p["base_branch"] = "origin/main"
    if source_commit_sha is not None:
        p["source_commit_sha"] = source_commit_sha
    if base_commit_sha is not None:
        p["base_commit_sha"] = base_commit_sha
    p["changed_files"] = ["prismatic/agy_completed_work.py"]
    p["lane_scope"] = {
        "allowed_paths": ["prismatic/", "tests/"],
        "touched_paths": ["prismatic/agy_completed_work.py"],
    }
    return p


def _manifest(row):
    path = Path(row.as_dict()["evidence_retention"]["manifest_path"])
    return json.loads(path.read_text(encoding="utf-8"))


def _write_retained_inputs(tmp_path):
    source = tmp_path / "RESULT.md"
    source.write_text("RESULT=PASS\nMARKER=AGY_DURABLE_OK", encoding="utf-8")
    proof = tmp_path / "proof.log"
    proof.write_text("pytest passed", encoding="utf-8")
    return source, proof


def test_promotion_decision_requires_complete_durable_evidence(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    source, proof = _write_retained_inputs(tmp_path)
    store = AgyCompletedWorkStore(
        tmp_path / "agy_completed_work.db", evidence_dir=tmp_path / "evidence"
    )

    row = store.ingest(_retention_packet(tmp_path, source_path=source, proof_log=proof))
    promotion = row.as_dict()["promotion_decision"]

    assert promotion["status"] == "decision_ready"
    assert promotion["recommendation"] == "open_or_update_pr_dry_run_only"
    assert promotion["promotion_decision"] == "open_or_update_pr_dry_run_only"
    assert promotion["policy_gate"] == "pass"
    assert promotion["packet_classification"] == "packet_valid"
    assert promotion["integration_classification"] == "pass_ready_for_review"
    assert promotion["evidence_retention"]["status"] == "complete"
    assert promotion["evidence_retention"]["proof_log_retained"] is True
    assert promotion["evidence_retention"]["manifest_sha256"]
    assert promotion["evidence_retention"]["source_commit_sha"] == VALID_SOURCE_SHA
    assert promotion["side_effects"] == {
        "linear_comment_posted": False,
        "github_pr_created": False,
        "git_branch_created": False,
        "auto_merge_enabled": False,
        "production_deployed": False,
        "agent_dispatched": False,
        "bulk_agent_dispatch": False,
    }


def test_promotion_decision_holds_when_durable_evidence_unavailable(tmp_path):
    store = AgyCompletedWorkStore(tmp_path / "agy_completed_work.db")

    row = store.ingest(packet())
    historical_unavailable = row.__class__(**{**row.__dict__, "evidence_retention": {}})
    promotion = historical_unavailable.as_dict()["promotion_decision"]

    assert row.classification == "merge_ready"
    assert row.integration_classification == "pass_ready_for_review"
    assert promotion["status"] == "hold_needs_durable_evidence"
    assert promotion["recommendation"] == "hold_for_durable_evidence"
    assert promotion["policy_gate"] == "blocked"
    assert "durable evidence retention status is unavailable" in promotion["reasons"]


def test_promotion_decision_holds_for_missing_manifest_source_commit_and_log(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    source, proof = _write_retained_inputs(tmp_path)
    store = AgyCompletedWorkStore(
        tmp_path / "agy_completed_work.db", evidence_dir=tmp_path / "evidence"
    )

    missing_source_commit = store.ingest(
        _retention_packet(
            tmp_path,
            source_path=source,
            proof_log=proof,
            source_commit_sha=None,
            source_branch="feature/missing-source-commit",
        )
    )
    missing_manifest_sha = dict(missing_source_commit.evidence_retention)
    missing_manifest_sha["manifest_sha256"] = None
    no_proof_log = dict(missing_source_commit.evidence_retention)
    no_proof_log["status"] = "complete"
    no_proof_log["proof_log_retained"] = False
    no_proof_log["manifest_sha256"] = "a" * 64
    no_proof_log["source_commit_sha"] = VALID_SOURCE_SHA

    for evidence in (
        missing_source_commit.evidence_retention,
        missing_manifest_sha,
        no_proof_log,
    ):
        decision = missing_source_commit.__class__(
            **{**missing_source_commit.__dict__, "evidence_retention": evidence}
        ).promotion_decision_read_model()
        assert decision["status"] == "hold_needs_durable_evidence"
        assert decision["policy_gate"] == "blocked"


def test_promotion_decision_holds_for_partial_and_rejected_unsafe_evidence(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    proof = tmp_path / "proof.log"
    proof.write_text("proof", encoding="utf-8")
    store = AgyCompletedWorkStore(
        tmp_path / "agy_completed_work.db", evidence_dir=tmp_path / "evidence"
    )
    partial = store.ingest(
        _retention_packet(
            tmp_path,
            source_path=tmp_path / "missing.md",
            proof_log=proof,
            source_branch="feature/partial-evidence",
        )
    )
    unsafe_source = tmp_path / ".env"
    unsafe_source.write_text("secret", encoding="utf-8")
    rejected = store.ingest(
        _retention_packet(
            tmp_path,
            source_path=unsafe_source,
            proof_log=proof,
            source_branch="feature/rejected-evidence",
        )
    )

    for row in (partial, rejected):
        promotion = row.as_dict()["promotion_decision"]
        assert promotion["status"] == "hold_needs_durable_evidence"
        assert promotion["policy_gate"] == "blocked"
        assert (
            "unsafe, rejected, or partial durable evidence state"
            in promotion["reasons"]
        )


def test_promotion_decision_preserves_blocked_and_invalid_packet_state(tmp_path):
    store = AgyCompletedWorkStore(tmp_path / "agy_completed_work.db")
    blocked = packet()
    blocked["proof"]["result"] = "BLOCKED"
    malformed = packet()
    malformed["source_branch"] = "feature/malformed-result"
    malformed["proof"]["result"] = "WHAT"

    blocked_promotion = store.ingest(blocked).as_dict()["promotion_decision"]
    malformed_promotion = store.ingest(malformed).as_dict()["promotion_decision"]

    assert blocked_promotion["status"] == "blocked"
    assert blocked_promotion["recommendation"] == "blocked"
    assert malformed_promotion["status"] == "needs_manual_review"
    assert malformed_promotion["recommendation"] == "manual_review"


def test_durable_evidence_retains_packet_source_and_proof_after_originals_deleted(
    tmp_path,
):
    source = tmp_path / "RESULT.md"
    source.write_text("RESULT=PASS\nsecret=" + "a" * 40, encoding="utf-8")
    proof = tmp_path / "proof.log"
    proof.write_text("pytest passed token=ghp_" + "b" * 36, encoding="utf-8")
    store = AgyCompletedWorkStore(
        tmp_path / "agy_completed_work.db", evidence_dir=tmp_path / "evidence"
    )

    row = store.ingest(_retention_packet(tmp_path, source_path=source, proof_log=proof))
    source.unlink()
    proof.unlink()
    manifest = _manifest(row)

    evidence = row.as_dict()["evidence_retention"]
    assert evidence["status"] == "complete"
    assert evidence["source_commit_sha"] == VALID_SOURCE_SHA
    assert evidence["base_commit_sha"] == VALID_BASE_SHA
    assert manifest["retention_status"] == "complete"
    assert manifest["source_commit_sha"] == VALID_SOURCE_SHA
    assert manifest["base_commit_sha"] == VALID_BASE_SHA
    assert Path(manifest["retained_packet_path"]).is_file()
    assert Path(manifest["retained_gate_path"]).is_file()
    assert Path(manifest["retained_source_path"]).read_text(encoding="utf-8")
    assert Path(manifest["retained_proof_log_path"]).read_text(encoding="utf-8")
    retained_blob = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path(manifest["retained_packet_path"]).parent.iterdir()
    )
    assert "ghp_" not in retained_blob
    assert "a" * 40 not in retained_blob
    assert "[REDACTED]" in retained_blob


def test_durable_evidence_rejects_credential_filenames_before_copy(tmp_path):
    proof = tmp_path / "proof.log"
    proof.write_text("proof", encoding="utf-8")
    store = AgyCompletedWorkStore(
        tmp_path / "agy_completed_work.db", evidence_dir=tmp_path / "evidence"
    )

    for name in (".env", ".env.local", ".git-credentials", ".netrc"):
        source = tmp_path / name
        source.write_text("raw-secret-should-not-copy", encoding="utf-8")
        row = store.ingest(
            _retention_packet(tmp_path, source_path=source, proof_log=proof)
        )
        manifest = _manifest(row)
        assert row.as_dict()["evidence_retention"]["status"] == "rejected_unsafe"
        assert "secret_or_credential_path" in manifest["retention_reasons"]
        assert manifest["retained_source_path"] is None
        retained_blob = "\n".join(
            path.read_text(encoding="utf-8")
            for path in Path(manifest["retained_packet_path"]).parent.iterdir()
            if path.is_file()
        )
        assert "raw-secret-should-not-copy" not in retained_blob


def test_durable_evidence_allows_safe_result_and_proof_filenames(tmp_path):
    source = tmp_path / "RESULT.md"
    proof = tmp_path / "proof.log"
    packet_json = tmp_path / "packet.json"
    source.write_text("source", encoding="utf-8")
    proof.write_text("proof", encoding="utf-8")
    packet_json.write_text("packet", encoding="utf-8")
    store = AgyCompletedWorkStore(
        tmp_path / "agy_completed_work.db", evidence_dir=tmp_path / "evidence"
    )

    result_row = store.ingest(
        _retention_packet(tmp_path, source_path=source, proof_log=proof)
    )
    packet_row = store.ingest(
        _retention_packet(tmp_path, source_path=packet_json, proof_log=proof)
    )

    assert result_row.as_dict()["evidence_retention"]["status"] == "complete"
    assert packet_row.as_dict()["evidence_retention"]["status"] == "complete"


def test_durable_evidence_commit_identity_round_trip_and_compact_alias(tmp_path):
    source = tmp_path / "RESULT.md"
    proof = tmp_path / "proof.log"
    source.write_text("source", encoding="utf-8")
    proof.write_text("proof", encoding="utf-8")
    store = AgyCompletedWorkStore(
        tmp_path / "agy_completed_work.db", evidence_dir=tmp_path / "evidence"
    )

    row = store.ingest(_retention_packet(tmp_path, source_path=source, proof_log=proof))
    manifest = _manifest(row)
    reloaded = store.get(row.id).as_dict()["evidence_retention"]

    assert manifest["source_commit_sha"] == VALID_SOURCE_SHA
    assert manifest["base_commit_sha"] == VALID_BASE_SHA
    assert reloaded["source_commit_sha"] == VALID_SOURCE_SHA
    assert reloaded["base_commit_sha"] == VALID_BASE_SHA

    compact = f"""
COMMAND=pytest
RESULT=PASS
LOG={proof}
SCOPE=compact commit alias
AD_HOC_OR_CANONICAL=ad-hoc targeted
NOT_CLAIMING=merge,deploy
AGENT=agy
SOURCE_BRANCH=feature/compact
SOURCE_PATH={source}
BASE_BRANCH=main
COMMIT={VALID_SOURCE_SHA}
BASE_COMMIT_SHA={VALID_BASE_SHA}
CHANGED_FILES=prismatic/agy_completed_work.py
MARKER=AGY_COMPLETED_WORK_DURABLE_EVIDENCE_REPAIR_OK
"""
    parsed = parse_completed_work_packet_text(compact)
    assert parsed["source_commit_sha"] == VALID_SOURCE_SHA
    assert parsed["base_commit_sha"] == VALID_BASE_SHA


def test_durable_evidence_invalid_or_missing_source_sha_is_partial(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "RESULT.md"
    proof = tmp_path / "proof.log"
    source.write_text("source", encoding="utf-8")
    proof.write_text("proof", encoding="utf-8")
    store = AgyCompletedWorkStore(
        tmp_path / "agy_completed_work.db", evidence_dir=tmp_path / "evidence"
    )

    invalid = store.ingest(
        _retention_packet(
            tmp_path,
            source_path=source,
            proof_log=proof,
            source_commit_sha="not-a-sha",
            base_commit_sha="short",
            source_branch="feature/invalid-commit-evidence",
        )
    )
    missing = store.ingest(
        _retention_packet(
            tmp_path,
            source_path=source,
            proof_log=proof,
            source_commit_sha=None,
            source_branch="feature/missing-commit-evidence",
        )
    )

    invalid_manifest = _manifest(invalid)
    missing_manifest = _manifest(missing)
    assert invalid.as_dict()["classification"] == "merge_ready"
    assert invalid.as_dict()["evidence_retention"]["status"] == "partial"
    assert invalid_manifest["source_commit_sha"] is None
    assert invalid_manifest["base_commit_sha"] is None
    assert "invalid_source_commit_sha" in invalid_manifest["retention_reasons"]
    assert "invalid_base_commit_sha" in invalid_manifest["retention_reasons"]
    assert missing.as_dict()["classification"] == "merge_ready"
    assert missing.as_dict()["evidence_retention"]["status"] == "partial"
    assert "source_commit_sha_missing" in missing_manifest["retention_reasons"]


def test_durable_evidence_reingest_is_idempotent_and_preserves_first_manifest(tmp_path):
    source = tmp_path / "RESULT.md"
    proof = tmp_path / "proof.log"
    source.write_text("first", encoding="utf-8")
    proof.write_text("first log", encoding="utf-8")
    store = AgyCompletedWorkStore(
        tmp_path / "agy_completed_work.db", evidence_dir=tmp_path / "evidence"
    )
    p = _retention_packet(tmp_path, source_path=source, proof_log=proof)

    first = store.ingest(p)
    first_manifest = _manifest(first)
    source.write_text("second", encoding="utf-8")
    proof.write_text("second log", encoding="utf-8")
    second = store.ingest(p)
    second_manifest = _manifest(second)

    assert first.id == second.id
    assert len(list((tmp_path / "evidence").glob("agy-cw-*"))) == 1
    assert first_manifest["manifest_sha256"] == second_manifest["manifest_sha256"]
    assert (
        Path(second_manifest["retained_source_path"]).read_text(encoding="utf-8")
        == "first"
    )


def test_durable_evidence_missing_source_or_log_is_partial_without_fabrication(
    tmp_path,
):
    proof = tmp_path / "proof.log"
    proof.write_text("proof", encoding="utf-8")
    store = AgyCompletedWorkStore(
        tmp_path / "agy_completed_work.db", evidence_dir=tmp_path / "evidence"
    )
    missing_source = store.ingest(
        _retention_packet(
            tmp_path, source_path=tmp_path / "missing.md", proof_log=proof
        )
    )
    source = tmp_path / "RESULT.md"
    source.write_text("source", encoding="utf-8")
    missing_log = store.ingest(
        _retention_packet(
            tmp_path, source_path=source, proof_log=tmp_path / "missing.log"
        )
    )

    assert missing_source.as_dict()["evidence_retention"]["status"] == "partial"
    assert (
        "source_path_missing_at_ingest"
        in _manifest(missing_source)["retention_reasons"]
    )
    assert _manifest(missing_source)["retained_source_path"] is None
    assert missing_log.as_dict()["evidence_retention"]["status"] == "partial"
    assert (
        "proof_log_file_missing_or_not_regular"
        in _manifest(missing_log)["retention_reasons"]
    )
    assert _manifest(missing_log)["retained_proof_log_path"] is None


def test_durable_evidence_rejects_symlink_traversal_credentials_and_oversize(tmp_path):
    proof = tmp_path / "proof.log"
    proof.write_text("proof", encoding="utf-8")
    source = tmp_path / "RESULT.md"
    source.write_text("source", encoding="utf-8")
    symlink = tmp_path / "link.md"
    symlink.symlink_to(source)
    store = AgyCompletedWorkStore(
        tmp_path / "agy_completed_work.db", evidence_dir=tmp_path / "evidence"
    )

    symlink_row = store.ingest(
        _retention_packet(tmp_path, source_path=symlink, proof_log=proof)
    )
    traversal_row = store.ingest(
        _retention_packet(
            tmp_path, source_path=tmp_path / ".." / "RESULT.md", proof_log=proof
        )
    )
    credential_row = store.ingest(
        _retention_packet(
            tmp_path, source_path=Path.home() / ".ssh" / "id_rsa", proof_log=proof
        )
    )
    oversized = tmp_path / "oversized.md"
    oversized.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    oversized_row = store.ingest(
        _retention_packet(tmp_path, source_path=oversized, proof_log=proof)
    )

    assert symlink_row.as_dict()["evidence_retention"]["status"] == "rejected_unsafe"
    assert traversal_row.as_dict()["evidence_retention"]["status"] == "rejected_unsafe"
    assert credential_row.as_dict()["evidence_retention"]["status"] == "rejected_unsafe"
    assert oversized_row.as_dict()["evidence_retention"]["status"] == "rejected_unsafe"
    assert _manifest(oversized_row)["retained_source_path"] is None


def test_existing_sqlite_schema_migrates_and_exposes_retention_state(tmp_path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE agy_completed_work (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                agent TEXT,
                source_branch TEXT,
                source_path TEXT,
                base_branch TEXT,
                classification TEXT NOT NULL,
                eligible_for_merge INTEGER NOT NULL,
                requires_clean_rebuild INTEGER NOT NULL,
                proof_result TEXT,
                proof_marker TEXT,
                gate_marker TEXT NOT NULL,
                ingestion_marker TEXT NOT NULL,
                packet_json TEXT NOT NULL,
                gate_json TEXT NOT NULL,
                non_claims_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO agy_completed_work VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-row",
                "2026-07-20T00:00:00+00:00",
                "2026-07-20T00:00:00+00:00",
                "agy",
                "feature/legacy",
                str(tmp_path / "missing.md"),
                "origin/main",
                "merge_ready",
                1,
                0,
                "PASS",
                "AGY_COMPLETED_WORK_OK",
                AGY_COMPLETED_WORK_MARKER,
                AGY_COMPLETED_WORK_INGESTION_MARKER,
                json.dumps(_retention_packet(tmp_path)),
                json.dumps({"classification": "merge_ready"}),
                json.dumps([]),
            ),
        )
    store = AgyCompletedWorkStore(db_path, evidence_dir=tmp_path / "evidence")
    legacy = store.get("legacy-row").as_dict()

    assert legacy["evidence_retention"]["status"] == "unavailable"
    assert legacy["evidence_retention"]["historical_source_recovered"] is False
    assert legacy["evidence_retention"]["historical_log_recovered"] is False


def test_exact_canonical_replay_creates_one_immutable_row(tmp_path):
    store = AgyCompletedWorkStore(
        tmp_path / "agy_completed_work.db", evidence_dir=tmp_path / "evidence"
    )
    p = canonical_agy_packet()

    first = store.ingest(p)
    second = store.ingest(p)

    assert first.id == second.id
    assert first.created_at == second.created_at
    assert first.updated_at == second.updated_at
    assert len(store.list()) == 1


def test_same_deterministic_id_with_different_packet_fails_atomically_and_leaves_db_unchanged(
    tmp_path,
):
    db_path = tmp_path / "agy_completed_work.db"
    evidence_dir = tmp_path / "evidence"
    store = AgyCompletedWorkStore(db_path, evidence_dir=evidence_dir)

    p1 = canonical_agy_packet()
    first = store.ingest(p1)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        before_row = dict(
            conn.execute(
                "SELECT * FROM agy_completed_work WHERE id = ?", (first.id,)
            ).fetchone()
        )

    manifest_path = Path(first.evidence_retention["manifest_path"])
    before_manifest_bytes = manifest_path.read_bytes()

    p2 = deepcopy(p1)
    p2["verification"]["commands"] = ["python3 -m pytest -q tests/test_different.py"]

    with pytest.raises(AgyCompletedWorkConflictError):
        store.ingest(p2)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        after_row = dict(
            conn.execute(
                "SELECT * FROM agy_completed_work WHERE id = ?", (first.id,)
            ).fetchone()
        )

    after_manifest_bytes = manifest_path.read_bytes()

    assert before_row == after_row
    assert before_manifest_bytes == after_manifest_bytes


def test_trusted_agents_includes_kai_and_george(tmp_path):
    store = AgyCompletedWorkStore(tmp_path / "agy_completed_work.db")
    kai_packet = packet()
    kai_packet["agent"] = "kai"
    kai_packet["source_branch"] = "feature/kai-canonical"

    george_packet = packet()
    george_packet["agent"] = "george"
    george_packet["source_branch"] = "feature/george-canonical"

    row_kai = store.ingest(kai_packet)
    row_george = store.ingest(george_packet)

    assert row_kai.classification == "merge_ready"
    assert row_george.classification == "merge_ready"


def test_concurrent_same_id_conflict_retains_only_winning_packet(tmp_path, monkeypatch):
    store = AgyCompletedWorkStore(tmp_path / "agy_completed_work.db")
    first = packet()
    first["result_summary"] = "first concurrent packet"
    second = deepcopy(first)
    second["result_summary"] = "second conflicting packet"
    assert completed_work_id(first) == completed_work_id(second)

    original_retain = completed_work_module.retain_completed_work_evidence
    retained_packets = []
    retained_lock = threading.Lock()

    def recording_retain(**kwargs):
        with retained_lock:
            retained_packets.append(deepcopy(kwargs["packet"]))
        return original_retain(**kwargs)

    monkeypatch.setattr(
        completed_work_module, "retain_completed_work_evidence", recording_retain
    )
    start = threading.Barrier(2)

    def ingest(candidate):
        start.wait(timeout=5)
        try:
            return ("row", store.ingest(candidate))
        except AgyCompletedWorkConflictError:
            return ("conflict", None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result(timeout=10)
            for future in (pool.submit(ingest, first), pool.submit(ingest, second))
        ]

    assert sorted(kind for kind, _ in results) == ["conflict", "row"]
    winner = next(row for kind, row in results if kind == "row")
    assert winner is not None
    assert len(retained_packets) == 1
    assert retained_packets[0] == winner.packet
    assert store.get(winner.id).packet == winner.packet
