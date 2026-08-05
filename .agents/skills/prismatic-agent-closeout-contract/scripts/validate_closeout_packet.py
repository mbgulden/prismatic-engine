#!/usr/bin/env python3
"""
Prismatic Agent Closeout Packet Validator (v0.2 Standard Spec)
Fail-Closed CLI validator for AGY closeout packets (RESULT.md & result-packet.json).
"""

import sys
import os
import json
import hashlib
import re
import argparse
import tempfile
import shutil
from pathlib import Path

REQUIRED_FIELDS = [
    "agent",
    "STATUS",
    "PRODUCER_STATUS",
    "ACCEPTANCE_DECISION",
    "TASK_ID",
    "ATTEMPT_ID",
    "BASE_HEAD",
    "CANDIDATE_HEAD",
    "CANDIDATE_TREE",
    "CHANGED_PATHS",
    "COMMAND",
    "RESULT",
    "LOG",
    "LOG_SHA256",
    "result_artifacts",
    "SCOPE",
    "merge_lane",
    "risk_level",
    "AD_HOC_OR_CANONICAL",
    "PROOF_CLASSES",
    "SIDE_EFFECTS",
    "BLOCKERS",
    "NOT_CLAIMING",
    "NEXT_ACTION",
    "MARKER"
]

VALID_STATUSES = {"PASS", "PARTIAL", "BLOCKED", "ERROR"}
VALID_RESULTS = {"PASS", "FAIL", "BLOCKED"}
VALID_ACCEPTANCE = {"PENDING", "CLEAN", "REJECTED"}
VALID_TIERS = {"ad-hoc targeted", "canonical suite"}
VALID_PROOF_CLASSES = {"focused", "lint", "format", "build", "browser", "production"}
VALID_MERGE_LANES = {"dashboard-ui", "backend-api", "docs", "research", "mixed", "manual-review"}
VALID_RISK_LEVELS = {"low", "medium", "high"}
VALID_NEXT_ACTIONS = {"merge-ready", "needs-fred-cleanup", "needs-human-review", "blocked", "superseded"}

EXPECTED_MARKER = "AGY_TASK_RESULT_PACKET_OK"

SHA40_RE = re.compile(r"^[0-9a-fA-F]{40}$")
SHA64_RE = re.compile(r"^[0-9a-fA-F]{64}$")
TASK_ID_RE = re.compile(r"^GRO-[0-9]+$")

def compute_sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def parse_result_md(md_path: Path) -> dict:
    fields = {}
    if not md_path.exists():
        return fields

    content = md_path.read_text(encoding="utf-8")
    for line in content.splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()
            fields[k] = v
    return fields

def validate_packet(directory: Path, check_sha_files: bool = True) -> tuple[bool, list[str]]:
    errors = []

    result_md = directory / "RESULT.md"
    result_json = directory / "result-packet.json"

    if not result_md.exists():
        errors.append(f"Missing human-readable report artifact: RESULT.md in {directory}")
    if not result_json.exists():
        errors.append(f"Missing machine-readable schema artifact: result-packet.json in {directory}")

    if errors:
        return False, errors

    try:
        data = json.loads(result_json.read_text(encoding="utf-8"))
    except Exception as e:
        errors.append(f"Failed to parse result-packet.json: {str(e)}")
        return False, errors

    # 1. Check required fields
    for field in REQUIRED_FIELDS:
        if field not in data:
            errors.append(f"Missing required field in result-packet.json: '{field}'")

    if errors:
        return False, errors

    # 2. Agent discriminator check
    if data["agent"] != "agy":
        errors.append(f"Invalid agent '{data['agent']}'. Standard requires exactly 'agy'")

    # 3. Enum and Pattern Validations
    if data["STATUS"] not in VALID_STATUSES:
        errors.append(f"Invalid STATUS '{data['STATUS']}'. Expected one of {VALID_STATUSES}")

    if data["PRODUCER_STATUS"] not in VALID_STATUSES:
        errors.append(f"Invalid PRODUCER_STATUS '{data['PRODUCER_STATUS']}'. Expected one of {VALID_STATUSES}")

    if data["ACCEPTANCE_DECISION"] not in VALID_ACCEPTANCE:
        errors.append(f"Invalid ACCEPTANCE_DECISION '{data['ACCEPTANCE_DECISION']}'. Expected one of {VALID_ACCEPTANCE}")

    if data["RESULT"] not in VALID_RESULTS:
        errors.append(f"Invalid RESULT '{data['RESULT']}'. Expected one of {VALID_RESULTS}")

    if not TASK_ID_RE.match(data["TASK_ID"]):
        errors.append(f"TASK_ID '{data['TASK_ID']}' must match standard GRO task format '^GRO-[0-9]+$'")

    if not SHA40_RE.match(data["BASE_HEAD"]):
        errors.append(f"BASE_HEAD '{data['BASE_HEAD']}' is not a valid 40-character hex SHA")

    if not SHA40_RE.match(data["CANDIDATE_HEAD"]):
        errors.append(f"CANDIDATE_HEAD '{data['CANDIDATE_HEAD']}' is not a valid 40-character hex SHA")

    if not SHA40_RE.match(data["CANDIDATE_TREE"]):
        errors.append(f"CANDIDATE_TREE '{data['CANDIDATE_TREE']}' is not a valid 40-character hex SHA")

    if not SHA64_RE.match(data["LOG_SHA256"]):
        errors.append(f"LOG_SHA256 '{data['LOG_SHA256']}' is not a valid 64-character sha256 digest")

    if data["merge_lane"] not in VALID_MERGE_LANES:
        errors.append(f"Invalid merge_lane '{data['merge_lane']}'. Expected one of {VALID_MERGE_LANES}")

    if data["risk_level"] not in VALID_RISK_LEVELS:
        errors.append(f"Invalid risk_level '{data['risk_level']}'. Expected one of {VALID_RISK_LEVELS}")

    if data["AD_HOC_OR_CANONICAL"] not in VALID_TIERS:
        errors.append(f"AD_HOC_OR_CANONICAL '{data['AD_HOC_OR_CANONICAL']}' must be one of {VALID_TIERS}")

    if data["NEXT_ACTION"] not in VALID_NEXT_ACTIONS:
        errors.append(f"Invalid NEXT_ACTION '{data['NEXT_ACTION']}'. Expected one of {VALID_NEXT_ACTIONS}")

    if data["MARKER"] != EXPECTED_MARKER:
        errors.append(f"Invalid MARKER '{data['MARKER']}'. Expected exactly '{EXPECTED_MARKER}'")

    # 4. Check arrays and Safe Provenance
    if not isinstance(data["CHANGED_PATHS"], list):
        errors.append("CHANGED_PATHS must be a list of strings")

    if not isinstance(data["COMMAND"], list) or len(data["COMMAND"]) == 0:
        errors.append("COMMAND must be a non-empty list of command line strings")

    if not isinstance(data["PROOF_CLASSES"], list) or len(data["PROOF_CLASSES"]) == 0:
        errors.append("PROOF_CLASSES must be a non-empty list of proof class strings")
    else:
        for pc in data["PROOF_CLASSES"]:
            if pc not in VALID_PROOF_CLASSES:
                errors.append(f"Invalid proof class '{pc}'. Expected one of {VALID_PROOF_CLASSES}")

    # Check result_artifacts safe provenance
    if not isinstance(data["result_artifacts"], list) or len(data["result_artifacts"]) == 0:
        errors.append("result_artifacts must be a non-empty list")
    else:
        for item in data["result_artifacts"]:
            art_path = item["path"] if isinstance(item, dict) and "path" in item else str(item)
            if art_path.startswith("/tmp") or art_path.startswith("tmp/"):
                errors.append(f"Unsafe raw provenance artifact path '{art_path}'. /tmp paths are rejected.")

    # 5. Check Side Effects
    side_effects = data.get("SIDE_EFFECTS", {})
    if not isinstance(side_effects, dict):
        errors.append("SIDE_EFFECTS must be an object/dict")
    else:
        req_se = ["push", "pr", "merge", "deploy", "linear_updated"]
        for se in req_se:
            if se not in side_effects or not isinstance(side_effects[se], bool):
                errors.append(f"SIDE_EFFECTS missing boolean property '{se}'")

    # 6. Bidirectional Consistency Checks (Positive & Negative Direction)
    status = data["STATUS"]
    producer_status = data["PRODUCER_STATUS"]
    result = data["RESULT"]
    blockers = data.get("BLOCKERS", [])
    risk_level = data.get("risk_level")
    merge_lane = data.get("merge_lane")
    next_action = data.get("NEXT_ACTION")

    if status == "PASS":
        if producer_status != "PASS":
            errors.append(f"STATUS is 'PASS' but PRODUCER_STATUS is '{producer_status}'")
        if result != "PASS":
            errors.append(f"STATUS is 'PASS' but verification RESULT is '{result}'")
        if blockers:
            errors.append("STATUS is 'PASS' but BLOCKERS list is non-empty")
        if risk_level == "high":
            errors.append("STATUS is 'PASS' but risk_level is 'high' (high-risk changes require manual review)")
        if merge_lane == "manual-review":
            errors.append("STATUS is 'PASS' but merge_lane is 'manual-review'")
        if next_action != "merge-ready":
            errors.append(f"STATUS is 'PASS' but NEXT_ACTION is '{next_action}' (expected 'merge-ready')")

    elif status == "BLOCKED":
        if producer_status != "BLOCKED":
            errors.append(f"STATUS is 'BLOCKED' but PRODUCER_STATUS is '{producer_status}'")
        if result == "PASS":
            errors.append("STATUS is 'BLOCKED' but verification RESULT is 'PASS'")
        if not blockers:
            errors.append("STATUS is 'BLOCKED' but BLOCKERS list is empty (must state explicit blocker reason)")
        if next_action not in {"blocked", "needs-fred-cleanup", "needs-human-review"}:
            errors.append(f"STATUS is 'BLOCKED' but NEXT_ACTION is '{next_action}'")

    elif status == "ERROR":
        if producer_status != "ERROR":
            errors.append(f"STATUS is 'ERROR' but PRODUCER_STATUS is '{producer_status}'")
        if result == "PASS":
            errors.append("STATUS is 'ERROR' but verification RESULT is 'PASS'")
        if not blockers:
            errors.append("STATUS is 'ERROR' but BLOCKERS list is empty (must state explicit error details)")

    elif status == "PARTIAL":
        if producer_status != "PARTIAL":
            errors.append(f"STATUS is 'PARTIAL' but PRODUCER_STATUS is '{producer_status}'")
        if result == "PASS":
            errors.append("STATUS is 'PARTIAL' but verification RESULT is 'PASS'")

    # 7. Log File and Hash Check
    if check_sha_files and data.get("LOG"):
        log_path = directory / data["LOG"]
        if not log_path.exists():
            log_path = Path(data["LOG"])
        if not log_path.exists():
            errors.append(f"Execution log file does not exist: '{data['LOG']}' in {directory}")
        else:
            computed_hash = compute_sha256(log_path)
            if computed_hash.lower() != data["LOG_SHA256"].lower():
                errors.append(
                    f"LOG_SHA256 mismatch for {log_path}: expected {data['LOG_SHA256']}, got {computed_hash}"
                )

    # 8. Synchronization Check with RESULT.md
    md_fields = parse_result_md(result_md)
    sync_check_keys = ["agent", "STATUS", "PRODUCER_STATUS", "TASK_ID", "CANDIDATE_HEAD", "RESULT", "merge_lane", "risk_level", "MARKER"]
    for key in sync_check_keys:
        if key in md_fields and md_fields[key] != str(data.get(key)):
            errors.append(f"Mismatch between RESULT.md ({key}={md_fields[key]}) and result-packet.json ({key}={data.get(key)})")

    return len(errors) == 0, errors

def run_fixture_harness(examples_dir: Path, check_sha_files: bool = True) -> tuple[bool, list[str]]:
    """Fixture test runner: validates .pass.json, .blocked.json, and .error.json fixtures."""
    all_errors = []
    fixtures = [
        ("pass", "RESULT.pass.md", "result-packet.pass.json"),
        ("blocked", "RESULT.blocked.md", "result-packet.blocked.json"),
        ("error", "RESULT.error.md", "result-packet.error.json"),
    ]

    for label, md_name, json_name in fixtures:
        md_file = examples_dir / md_name
        json_file = examples_dir / json_name
        if not md_file.exists() or not json_file.exists():
            all_errors.append(f"Fixture file missing for {label}: {md_name} or {json_name}")
            continue

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            shutil.copy(md_file, tmppath / "RESULT.md")
            shutil.copy(json_file, tmppath / "result-packet.json")

            # Copy artifacts folder if present
            artifacts_src = examples_dir / "artifacts"
            if artifacts_src.exists():
                shutil.copytree(artifacts_src, tmppath / "artifacts")

            valid, errors = validate_packet(tmppath, check_sha_files=check_sha_files)
            if not valid:
                all_errors.append(f"Fixture [{label}] failed validation:")
                for err in errors:
                    all_errors.append(f"  - {err}")
            else:
                print(f"Fixture [{label}] VALIDATED CLEAN (check_sha_files={check_sha_files})")

    return len(all_errors) == 0, all_errors

def main():
    parser = argparse.ArgumentParser(description="Prismatic Closeout Packet Validator")
    parser.add_argument("path", nargs="?", default=".", help="Directory containing closeout artifacts or examples dir")
    parser.add_argument("--test-fixtures", action="store_true", help="Run harness against fixture files (*.pass.json, etc.)")
    parser.add_argument("--no-check-sha", action="store_true", help="Skip sha256 checksum verification of log file")

    args = parser.parse_args()
    target_dir = Path(args.path).resolve()
    check_sha = not args.no_check_sha

    if args.test_fixtures or (target_dir / "result-packet.pass.json").exists():
        valid, errors = run_fixture_harness(target_dir, check_sha_files=check_sha)
        if valid:
            print("STATUS=PASS")
            print("FIXTURE_HARNESS=100% GREEN")
            print(f"VALIDATED_DIR={target_dir}")
            sys.exit(0)
        else:
            print("STATUS=BLOCKED")
            print("REASON=FIXTURE_VALIDATION_FAILED")
            print("ERRORS=")
            for err in errors:
                print(f"  - {err}")
            sys.exit(1)
    else:
        valid, errors = validate_packet(target_dir, check_sha_files=check_sha)
        if valid:
            print("STATUS=PASS")
            print("ACCEPTANCE_DECISION=CLEAN_STRUCTURE")
            print(f"VALIDATED_DIR={target_dir}")
            sys.exit(0)
        else:
            print("STATUS=BLOCKED")
            print("REASON=INVALID_CLOSEOUT_PACKET")
            print("ERRORS=")
            for err in errors:
                print(f"  - {err}")
            sys.exit(1)

if __name__ == "__main__":
    main()
