from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

from prismatic.universal_result_manifest import (
    UNIVERSAL_RESULT_MANIFEST_V2_MARKER,
    adapt_legacy_packet_to_manifest_v2,
    is_promotion_ready,
    validate_universal_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_FILE = REPO_ROOT / "tests" / "fixtures" / "manifest_v2_fixtures.json"


def load_fixtures() -> dict[str, dict[str, Any]]:
    with FIXTURES_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_fixtures_are_all_valid():
    """Verify that all bundle type fixtures in fixtures JSON pass validation."""
    fixtures = load_fixtures()
    for bundle_type, fixture in fixtures.items():
        res = validate_universal_manifest(fixture)
        assert res.ok, f"Fixture for type {bundle_type} failed: {res.errors}"


def test_structural_validation_invalid_type():
    """Verify that using an invalid bundle_type is rejected by schema validation."""
    fixtures = load_fixtures()
    fixture = copy.deepcopy(fixtures["code"])
    fixture["bundle_type"] = "invalid-type"
    res = validate_universal_manifest(fixture)
    assert not res.ok
    assert any("bundle_type" in err for err in res.errors)


def test_cross_field_validation_missing_deliverable_hash():
    """Verify semantic rule: if a file is in deliverables, its hash must exist in content_hashes."""
    fixtures = load_fixtures()
    fixture = copy.deepcopy(fixtures["code"])
    fixture["deliverables"].append("src/new_file.py")
    res = validate_universal_manifest(fixture)
    assert not res.ok
    assert any(
        "Missing content hash for deliverable" in err
        or "Path/hash key disagreement" in err
        for err in res.errors
    )


def test_cross_field_validation_missing_type_specific_proof():
    """Verify semantic rule: the type-specific proof matching bundle_type must exist."""
    fixtures = load_fixtures()
    fixture = copy.deepcopy(fixtures["code"])
    # Delete the code proof
    fixture["type_specific_proofs"].pop("code", None)
    res = validate_universal_manifest(fixture)
    assert not res.ok
    assert any("type-specific proof is missing" in err for err in res.errors)


def test_secret_safe_errors_redaction():
    """Verify that validation errors redact secret-like content safely."""
    fixtures = load_fixtures()
    fixture = copy.deepcopy(fixtures["code"])
    secret_token = "ghp_" + "1234567890abcdef" + "1234567890abcdef" + "1234"
    # Inject secret token into a field to trigger secret validation check
    fixture["review_digest"] = secret_token
    res = validate_universal_manifest(fixture)
    assert not res.ok
    err_str = " ".join(res.errors)
    assert "[REDACTED_SECRET]" in err_str
    assert secret_token not in err_str


def test_compatibility_adapter_legacy_result_packet():
    """Verify that adapting a legacy raw packet produces a valid v2 manifest with typed availability objects."""
    legacy_packet = {
        "agent": "agy",
        "issue_identifier": "GRO-3837",
        "branch": "feature/agy-dashboard-canary",
        "base_branch": "main",
        "changed_files": ["docs/example.md"],
        "result_artifacts": ["docs/example.md"],
        "verification": {
            "commands": ["python3 -m pytest tests/test_agy_result_packet.py"],
            "result": "PASS",
            "log_path": "/tmp/fred-agy-autopilot-verify.log",
            "ad_hoc_or_canonical": "ad-hoc targeted",
        },
        "non_claims": ["production_deploy", "auto_merge_enabled"],
        "merge_lane": "docs",
        "risk_level": "low",
        "next_action": "merge-ready",
        "marker": "AGY_TASK_RESULT_PACKET_OK",
    }
    v2_manifest = adapt_legacy_packet_to_manifest_v2(legacy_packet)
    res = validate_universal_manifest(v2_manifest)
    assert res.ok, f"Adapted manifest failed validation: {res.errors}"
    assert v2_manifest["marker"] == UNIVERSAL_RESULT_MANIFEST_V2_MARKER
    assert v2_manifest["schema_version"] == 2.0


def test_compatibility_adapter_legacy_completed_work():
    """Verify that adapting a legacy completed work packet produces a valid v2 manifest."""
    legacy_packet = {
        "agent": "agy",
        "source_branch": "feature/refactor-router",
        "source_path": "/home/operator/src/prismatic",
        "base_branch": "origin/main",
        "changed_files": ["prismatic/router.py"],
        "result_summary": "Router has been successfully refactored.",
        "proof": {
            "command": "pytest tests/test_router.py",
            "result": "PASS",
            "log": "/tmp/router-test.log",
            "scope": "router",
            "ad_hoc_or_canonical": "ad-hoc targeted",
            "marker": "AGY_COMPLETED_WORK_INTEGRATION_GATE_OK",
        },
        "lane_scope": {
            "allowed_paths": ["prismatic/**/*"],
            "touched_paths": ["prismatic/router.py"],
        },
    }
    v2_manifest = adapt_legacy_packet_to_manifest_v2(legacy_packet)
    res = validate_universal_manifest(v2_manifest)
    assert res.ok, f"Adapted manifest failed validation: {res.errors}"


def test_provenance_toolchain_unknown_rejected():
    """provenance_toolchain_unknown:ACCEPTED - unknown nested properties in provenance.toolchain/builder must be rejected."""
    fixtures = load_fixtures()
    fixture = copy.deepcopy(fixtures["code"])
    fixture["provenance"]["toolchain"] = {
        "name": "python",
        "version": "3.11",
        "unknown_field": "val",
    }
    res = validate_universal_manifest(fixture)
    assert not res.ok
    assert any(
        "unknown_field" in err
        or "additionalProperty" in err
        or "provenance -> toolchain" in err
        for err in res.errors
    )


def test_web_lighthouse_unknown_rejected():
    """web_lighthouse_unknown:ACCEPTED - unknown nested properties in lighthouse must be rejected."""
    fixtures = load_fixtures()
    fixture = copy.deepcopy(fixtures["web/app"])
    fixture["type_specific_proofs"]["web_app"]["lighthouse"]["unknown_metric"] = 100
    res = validate_universal_manifest(fixture)
    assert not res.ok
    assert any(
        "lighthouse" in err and ("unknown_metric" in err or "additionalProperty" in err)
        for err in res.errors
    )


def test_mixed_unknown_rejected():
    """mixed_unknown:ACCEPTED - unknown nested properties in mixed must be rejected."""
    fixtures = load_fixtures()
    fixture = copy.deepcopy(fixtures["mixed"])
    fixture["type_specific_proofs"]["mixed"]["unknown_field"] = "val"
    res = validate_universal_manifest(fixture)
    assert not res.ok
    assert any(
        "mixed" in err and ("unknown_field" in err or "additionalProperty" in err)
        for err in res.errors
    )


def test_review_digest_unknown_rejected():
    """review_digest_unknown:ACCEPTED - unknown nested properties in review_digest must be rejected."""
    fixtures = load_fixtures()
    fixture = copy.deepcopy(fixtures["code"])
    fixture["review_digest"] = {
        "reviewer": "jules",
        "status": "approved",
        "digest": "a576c24f2b963b516b3f6f140656667d4fdf8a7351662d515a6b0c242b934b12",
        "unknown_field": "val",
    }
    res = validate_universal_manifest(fixture)
    assert not res.ok
    assert any(
        "review_digest" in err
        and ("unknown_field" in err or "additionalProperty" in err)
        for err in res.errors
    )


def test_post_integration_unknown_rejected():
    """post_integration_unknown:ACCEPTED - unknown nested properties in post_integration_proof must be rejected."""
    fixtures = load_fixtures()
    fixture = copy.deepcopy(fixtures["code"])
    fixture["post_integration_proof"] = {
        "proof_type": "smoke-test",
        "status": "success",
        "unknown_field": "val",
    }
    res = validate_universal_manifest(fixture)
    assert not res.ok
    assert any(
        "post_integration_proof" in err
        and ("unknown_field" in err or "additionalProperty" in err)
        for err in res.errors
    )


def test_mixed_missing_matching_proof_rejected():
    """mixed_missing_matching_proof:ACCEPTED - reject absent or mismatching proof for mixed bundle type."""
    fixtures = load_fixtures()
    fixture = copy.deepcopy(fixtures["mixed"])
    # Delete the mixed proof
    fixture["type_specific_proofs"].pop("mixed", None)
    res = validate_universal_manifest(fixture)
    assert not res.ok
    assert any("matching proof 'mixed' is missing" in err for err in res.errors)

    # Put a mismatching/incompatible proof
    fixture = copy.deepcopy(fixtures["mixed"])
    fixture["type_specific_proofs"]["code"] = {
        "tests_passed": True,
        "coverage": 90.0,
        "lint_status": "clean",
    }
    res = validate_universal_manifest(fixture)
    assert not res.ok
    assert any(
        "proofs object contains extra keys" in err
        or "matching proof 'mixed' is missing" in err
        for err in res.errors
    )


def test_deliverable_traversal_rejected():
    """deliverable_traversal:ACCEPTED - reject unsafe paths (absolute, traversal, control chars, backslash, URL/userinfo)."""
    fixtures = load_fixtures()

    # 1. Traversal segment
    fixture = copy.deepcopy(fixtures["code"])
    fixture["deliverables"].append("src/../../etc/passwd")
    fixture["content_hashes"]["src/../../etc/passwd"] = (
        "a576c24f2b963b516b3f6f140656667d4fdf8a7351662d515a6b0c242b934b12"
    )
    res = validate_universal_manifest(fixture)
    assert not res.ok
    assert any("directory traversal" in err for err in res.errors)

    # 2. Absolute path
    fixture = copy.deepcopy(fixtures["code"])
    fixture["deliverables"].append("/etc/passwd")
    fixture["content_hashes"]["/etc/passwd"] = (
        "a576c24f2b963b516b3f6f140656667d4fdf8a7351662d515a6b0c242b934b12"
    )
    res = validate_universal_manifest(fixture)
    assert not res.ok
    assert any("absolute path" in err for err in res.errors)

    # 3. Backslash separator ambiguity
    fixture = copy.deepcopy(fixtures["code"])
    fixture["deliverables"].append("src\\main.py")
    fixture["content_hashes"]["src\\main.py"] = (
        "a576c24f2b963b516b3f6f140656667d4fdf8a7351662d515a6b0c242b934b12"
    )
    res = validate_universal_manifest(fixture)
    assert not res.ok
    assert any("backslash" in err for err in res.errors)

    # 4. URL/userinfo format
    fixture = copy.deepcopy(fixtures["code"])
    fixture["deliverables"].append("http://example.com/file.py")
    fixture["content_hashes"]["http://example.com/file.py"] = (
        "a576c24f2b963b516b3f6f140656667d4fdf8a7351662d515a6b0c242b934b12"
    )
    res = validate_universal_manifest(fixture)
    assert not res.ok
    assert any("URL or userinfo" in err for err in res.errors)

    fixture = copy.deepcopy(fixtures["code"])
    fixture["deliverables"].append("user:pass@host/file.py")
    fixture["content_hashes"]["user:pass@host/file.py"] = (
        "a576c24f2b963b516b3f6f140656667d4fdf8a7351662d515a6b0c242b934b12"
    )
    res = validate_universal_manifest(fixture)
    assert not res.ok
    assert any("URL or userinfo" in err for err in res.errors)


def test_legacy_fabricated_evidence_fails_promotion():
    """legacy_fabricated_evidence:dummy_hash,tests_passed,coverage_100,lint_pass,merged_receipt,review_bypass - legacy adapter must not claim fabricated evidence."""
    legacy_packet = {
        "agent": "agy",
        "issue_identifier": "GRO-3837",
        "branch": "feature/agy-dashboard-canary",
        "changed_files": ["docs/example.md"],
        "result_artifacts": ["docs/example.md"],
        "verification": {
            "commands": ["python3 -m pytest tests/test_agy_result_packet.py"],
            "result": "PASS",
            "log_path": "/tmp/fred-agy-autopilot-verify.log",
        },
        "merge_lane": "docs",
    }
    v2_manifest = adapt_legacy_packet_to_manifest_v2(legacy_packet)

    # Verify no fabricated data exists and unavailable evidence is typed object
    expected_status = {"status": "unverified", "reason": "adapted_from_legacy_packet"}
    assert v2_manifest["content_hashes"]["docs/example.md"] == expected_status
    assert v2_manifest["review_digest"] == expected_status
    assert v2_manifest["integration_receipt"] == expected_status
    assert v2_manifest["rollback"] == expected_status
    assert (
        v2_manifest["type_specific_proofs"]["document_data"]["valid"] == expected_status
    )

    # Verify that it passes basic schema validation, but is not promotion-ready
    res = validate_universal_manifest(v2_manifest)
    assert res.ok, f"Basic validation failed: {res.errors}"

    ready, reasons = is_promotion_ready(v2_manifest)
    assert not ready
    assert any("status is unverified" in r for r in reasons)


def test_sentinel_strings_rejected_in_digest_sha_boolean_numeric():
    """Requirement 4: Add regression tests that fail if unverified|unavailable|held are accepted as digest/SHA/boolean/numeric/timestamp evidence values."""
    fixtures = load_fixtures()
    code_fixture = fixtures["code"]

    sentinels = ["unverified", "unavailable", "held"]

    for s in sentinels:
        # 1. Digest in content_hashes
        f1 = copy.deepcopy(code_fixture)
        f1["content_hashes"]["src/main.py"] = s
        res = validate_universal_manifest(f1)
        assert not res.ok, f"Sentinel '{s}' in content_hashes should fail validation"

        # 2. Source commit SHA
        f2 = copy.deepcopy(code_fixture)
        f2["source_revision"]["source_commit_sha"] = s
        res = validate_universal_manifest(f2)
        assert not res.ok, f"Sentinel '{s}' in source_commit_sha should fail validation"

        # 3. Base commit SHA
        f3 = copy.deepcopy(code_fixture)
        f3["source_revision"]["base_commit_sha"] = s
        res = validate_universal_manifest(f3)
        assert not res.ok, f"Sentinel '{s}' in base_commit_sha should fail validation"

        # 4. Candidate SHA
        f4 = copy.deepcopy(code_fixture)
        f4["candidate_sha_destination"]["candidate_sha"] = s
        res = validate_universal_manifest(f4)
        assert not res.ok, f"Sentinel '{s}' in candidate_sha should fail validation"

        # 5. Boolean tests_passed
        f5 = copy.deepcopy(code_fixture)
        f5["type_specific_proofs"]["code"]["tests_passed"] = s
        res = validate_universal_manifest(f5)
        assert not res.ok, f"Sentinel '{s}' in tests_passed should fail validation"

        # 6. Numeric coverage
        f6 = copy.deepcopy(code_fixture)
        f6["type_specific_proofs"]["code"]["coverage"] = s
        res = validate_universal_manifest(f6)
        assert not res.ok, f"Sentinel '{s}' in coverage should fail validation"

        # 7. Numeric Lighthouse performance score
        f7 = copy.deepcopy(fixtures["web/app"])
        f7["type_specific_proofs"]["web_app"]["lighthouse"]["performance"] = s
        res = validate_universal_manifest(f7)
        assert not res.ok, (
            f"Sentinel '{s}' in lighthouse performance score should fail validation"
        )

        # 8. Numeric frame_count in sprite asset
        f8 = copy.deepcopy(fixtures["sprite/game asset"])
        f8["type_specific_proofs"]["sprite_game_asset"]["frame_count"] = s
        res = validate_universal_manifest(f8)
        assert not res.ok, f"Sentinel '{s}' in frame_count should fail validation"

        # 9. Numeric duration_seconds in video
        f9 = copy.deepcopy(fixtures["video"])
        f9["type_specific_proofs"]["video"]["duration_seconds"] = s
        res = validate_universal_manifest(f9)
        assert not res.ok, f"Sentinel '{s}' in duration_seconds should fail validation"

        # 10. Integer channels in audio
        f10 = copy.deepcopy(fixtures["audio"])
        f10["type_specific_proofs"]["audio"]["channels"] = s
        res = validate_universal_manifest(f10)
        assert not res.ok, f"Sentinel '{s}' in channels should fail validation"

        # 11. Boolean valid in document_data
        f11 = copy.deepcopy(fixtures["document/data"])
        f11["type_specific_proofs"]["document_data"]["valid"] = s
        res = validate_universal_manifest(f11)
        assert not res.ok, f"Sentinel '{s}' in valid should fail validation"

        # 12. Integer submanifests_validated in mixed
        f12 = copy.deepcopy(fixtures["mixed"])
        f12["type_specific_proofs"]["mixed"]["submanifests_validated"] = s
        res = validate_universal_manifest(f12)
        assert not res.ok, (
            f"Sentinel '{s}' in submanifests_validated should fail validation"
        )


def test_strict_evidence_contract_regressions():
    """Verify that malformed non-sentinel digest, timestamp, and checksum strings fail validation."""
    fixtures = load_fixtures()
    code_fixture = fixtures["code"]

    # 1. review_digest.digest="not-a-digest" must fail
    f1 = copy.deepcopy(code_fixture)
    f1["review_digest"] = {
        "reviewer": "jules",
        "status": "approved",
        "digest": "not-a-digest",
        "timestamp": "2026-07-21T08:01:00Z",
    }
    res = validate_universal_manifest(f1)
    assert not res.ok, "review_digest.digest='not-a-digest' should fail validation"

    # 2. review_digest.timestamp="yesterday" must fail
    f2 = copy.deepcopy(code_fixture)
    f2["review_digest"] = {
        "reviewer": "jules",
        "status": "approved",
        "digest": "a576c24f2b963b516b3f6f140656667d4fdf8a7351662d515a6b0c242b934b12",
        "timestamp": "yesterday",
    }
    res = validate_universal_manifest(f2)
    assert not res.ok, "review_digest.timestamp='yesterday' should fail validation"

    # 2b. Malformed calendar/timezone timestamps
    for bad_ts in [
        "2026-02-31T09:47:36Z",
        "2026-07-21 09:47:36",
        "2026-07-21T09:47:36+25:00",
        "2026-07-21T25:00:00Z",
    ]:
        f2_bad = copy.deepcopy(code_fixture)
        f2_bad["review_digest"] = {
            "reviewer": "jules",
            "status": "approved",
            "digest": "a576c24f2b963b516b3f6f140656667d4fdf8a7351662d515a6b0c242b934b12",
            "timestamp": bad_ts,
        }
        res = validate_universal_manifest(f2_bad)
        assert not res.ok, f"review_digest.timestamp='{bad_ts}' should fail validation"

    # 3. integration_receipt.timestamp="soon" must fail
    f3 = copy.deepcopy(code_fixture)
    f3["integration_receipt"] = {
        "receipt_id": "rec-123",
        "timestamp": "soon",
        "status": "integrated",
    }
    res = validate_universal_manifest(f3)
    assert not res.ok, "integration_receipt.timestamp='soon' should fail validation"

    # 4. post_integration_proof.checksum="not-a-digest" must fail
    f4 = copy.deepcopy(code_fixture)
    f4["post_integration_proof"] = {
        "proof_type": "smoke-test",
        "status": "success",
        "checksum": "not-a-digest",
    }
    res = validate_universal_manifest(f4)
    assert not res.ok, (
        "post_integration_proof.checksum='not-a-digest' should fail validation"
    )

    # 5. review_digest string top level must be a valid sha256_hash
    f5 = copy.deepcopy(code_fixture)
    f5["review_digest"] = "not-a-digest"
    res = validate_universal_manifest(f5)
    assert not res.ok, "top-level review_digest='not-a-digest' should fail validation"

    # Valid RFC 3339 timestamps and SHA-256 digests must pass
    f_valid = copy.deepcopy(code_fixture)
    f_valid["review_digest"] = {
        "reviewer": "jules",
        "status": "approved",
        "digest": "a576c24f2b963b516b3f6f140656667d4fdf8a7351662d515a6b0c242b934b12",
        "timestamp": "2026-07-21T09:47:36.123456+00:00",
    }
    f_valid["integration_receipt"] = {
        "receipt_id": "rec-123",
        "timestamp": "2026-07-21T09:47:36-05:00",
        "status": "integrated",
    }
    f_valid["post_integration_proof"] = {
        "proof_type": "smoke-test",
        "status": "success",
        "checksum": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    }
    res = validate_universal_manifest(f_valid)
    assert res.ok, (
        f"Valid RFC 3339 and SHA-256 evidence should pass validation: {res.errors}"
    )


def test_wheel_contains_universal_result_manifest_schema(tmp_path: Path) -> None:
    """Verify that the built wheel package includes the schema file in its package data."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--wheel-dir",
            str(tmp_path),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheels = list(tmp_path.glob("prismatic_engine-*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = set(wheel.namelist())
    assert "prismatic/schemas/universal-result-manifest.schema.json" in names


def test_installed_wheel_validation_proof(tmp_path: Path) -> None:
    """Requirement 7: build exact revision wheel, install it into a fresh venv, load schema via importlib.resources, and validate."""
    # Build wheel
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            ".",
            "--no-deps",
            "--wheel-dir",
            str(wheel_dir),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheels = list(wheel_dir.glob("prismatic_engine-*.whl"))
    assert len(wheels) == 1
    wheel_path = wheels[0]

    # Create fresh virtual environment outside the source directory
    venv_dir = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

    # Determine pip and python paths inside the new venv
    if os.name == "nt":
        pip_exe = venv_dir / "Scripts" / "pip.exe"
        python_exe = venv_dir / "Scripts" / "python.exe"
    else:
        pip_exe = venv_dir / "bin" / "pip"
        python_exe = venv_dir / "bin" / "python"

    # Install dependencies required by the package (jsonschema) and the wheel
    subprocess.run([str(pip_exe), "install", "jsonschema", str(wheel_path)], check=True)

    # Load fixtures to validate inside the venv python process
    fixtures = load_fixtures()
    code_fixture = fixtures["code"]

    script = f"""
import sys
from prismatic.universal_result_manifest import validate_universal_manifest, load_universal_manifest_schema

schema = load_universal_manifest_schema()
if not schema or "$id" not in schema:
    print("Schema load failed")
    sys.exit(1)

code_manifest = {code_fixture!r}
res = validate_universal_manifest(code_manifest)
if not res.ok:
    print("Valid manifest failed in venv:", res.errors)
    sys.exit(2)

# Verify sentinel string in content_hashes fails in venv
bad_manifest = dict(code_manifest)
bad_manifest["content_hashes"] = dict(bad_manifest["content_hashes"])
bad_manifest["content_hashes"]["src/main.py"] = "unverified"
res_bad = validate_universal_manifest(bad_manifest)
if res_bad.ok:
    print("Sentinel string was accepted in venv!")
    sys.exit(3)

# Verify malformed nested key fails closed in venv
malformed = dict(code_manifest)
malformed["provenance"] = dict(malformed["provenance"])
malformed["provenance"]["toolchain"] = {{"name": "python", "unknown_key": "val"}}
res2 = validate_universal_manifest(malformed)
if res2.ok:
    print("Malformed manifest was accepted in venv")
    sys.exit(4)

# Verify malformed digest in review_digest.digest fails in venv
m1 = dict(code_manifest)
m1["review_digest"] = {{"reviewer": "jules", "status": "approved", "digest": "not-a-digest"}}
if validate_universal_manifest(m1).ok:
    print("review_digest.digest='not-a-digest' accepted in venv")
    sys.exit(5)

# Verify malformed timestamp in review_digest.timestamp fails in venv
m2 = dict(code_manifest)
m2["review_digest"] = {{"reviewer": "jules", "status": "approved", "digest": "a576c24f2b963b516b3f6f140656667d4fdf8a7351662d515a6b0c242b934b12", "timestamp": "yesterday"}}
if validate_universal_manifest(m2).ok:
    print("review_digest.timestamp='yesterday' accepted in venv")
    sys.exit(6)

# Verify malformed timestamp in integration_receipt.timestamp fails in venv
m3 = dict(code_manifest)
m3["integration_receipt"] = {{"receipt_id": "rec-1", "timestamp": "soon", "status": "integrated"}}
if validate_universal_manifest(m3).ok:
    print("integration_receipt.timestamp='soon' accepted in venv")
    sys.exit(7)

# Verify malformed checksum in post_integration_proof.checksum fails in venv
m4 = dict(code_manifest)
m4["post_integration_proof"] = {{"proof_type": "smoke", "status": "ok", "checksum": "not-a-digest"}}
if validate_universal_manifest(m4).ok:
    print("post_integration_proof.checksum='not-a-digest' accepted in venv")
    sys.exit(8)

print("VENV_PROOF_SUCCESS")
sys.exit(0)
"""

    run_res = subprocess.run(
        [str(python_exe), "-c", script], capture_output=True, text=True
    )
    assert run_res.returncode == 0, (
        f"Inline script failed with code {run_res.returncode}:\n{run_res.stdout}\n{run_res.stderr}"
    )
    assert "VENV_PROOF_SUCCESS" in run_res.stdout
