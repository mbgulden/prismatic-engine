"""Comprehensive unit, integration, and adversarial tests for GRO-4114 verifier registry.

Tests:
1. Registration & lookup of all 8 verifier plugin types
2. Output verification & evidence schema validation for all 8 types with strict provenance
3. Fail-closed behavior on unknown types, unknown properties, malformed shapes, duplicate IDs
4. Type-specific required fields enforcement (cross-type invalidity)
5. Explicit unavailable tooling behavior & non-promotion enforcement
6. Mixed bundle child digest validation & fail-closed locator security rules
7. Secret sanitization in results and durable logs
8. Integration with Universal Result Manifest v2 and promotion readiness
9. True end-to-end provenance binding (artifact byte SHA-256 and exact Git commit lineage)
10. Explicit fail-closed tests for all 11 required negative provenance conditions:
    - missing identity
    - malformed/all-zero/sentinel digest
    - missing artifact locator
    - artifact-byte mismatch
    - stale prior commit
    - current tree hash supplied as commit
    - nonexistent 40-hex commit
    - wrong branch/base
    - dirty/uncommitted revision
    - absent repository/artifact context
    - revision mismatch
"""

import hashlib
import subprocess
from pathlib import Path

import pytest

from prismatic.universal_result_manifest import (
    is_promotion_ready,
)
from prismatic.verifiers import (
    UNIVERSAL_OUTPUT_VERIFIER_REGISTRY_OK,
    CodePackageVerifier,
    MixedBundleVerifier,
    SpriteAtlasGameVerifier,
    VerifierRegistry,
    WebsiteAppBrowserVerifier,
    enforce_strict_provenance,
    validate_locator_safety,
    validate_verifier_result,
    write_durable_log,
)


@pytest.fixture
def test_repo_and_artifact(tmp_path):
    """Fixture providing a real Git repo and retained artifact file with matching SHA-256 digest."""
    repo_dir = tmp_path / "test_repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
    )

    code_file = repo_dir / "main.py"
    code_file.write_text("print('hello world')\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "main.py"], cwd=str(repo_dir), check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
    )

    commit_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    artifact_file = tmp_path / "retained_artifact.tar.gz"
    art_bytes = b"retained_artifact_bytes_content_GRO_4114_exact"
    artifact_file.write_bytes(art_bytes)
    artifact_digest = hashlib.sha256(art_bytes).hexdigest()

    return {
        "repo_path": str(repo_dir),
        "commit_sha": commit_sha,
        "branch": branch,
        "artifact_path": str(artifact_file),
        "artifact_digest": artifact_digest,
        "allowed_artifact_root": str(tmp_path),
    }


def make_valid_candidate_and_context(
    test_repo_and_artifact, verifier_type="code", evidence=None
):
    """Helper to construct valid candidate and context for positive tests."""
    candidate = {
        "task_id": "GRO-4114",
        "candidate_digest": test_repo_and_artifact["artifact_digest"],
        "artifact_locator": test_repo_and_artifact["artifact_path"],
    }
    if evidence is not None:
        candidate["type_specific_evidence"] = evidence

    context = {
        "task_id": "GRO-4114",
        "repo_path": test_repo_and_artifact["repo_path"],
        "allowed_artifact_root": test_repo_and_artifact.get(
            "allowed_artifact_root",
            str(Path(test_repo_and_artifact["artifact_path"]).parent),
        ),
        "source_lineage": {
            "source_commit_sha": test_repo_and_artifact["commit_sha"],
            "branch": test_repo_and_artifact["branch"],
        },
    }
    return candidate, context


def test_registry_contains_all_eight_builtins():
    """Verify registry has plugins registered for all 8 required output types."""
    registry = VerifierRegistry()
    verifiers = registry.list_verifiers()
    expected_types = [
        "audio",
        "code",
        "document/data",
        "image/design",
        "mixed",
        "sprite/game asset",
        "video",
        "web/app",
    ]
    assert sorted(verifiers) == sorted(expected_types)


def test_registry_alias_lookups():
    """Verify lookup using Linear contract alias names."""
    registry = VerifierRegistry()

    assert isinstance(registry.get_verifier("code/package"), CodePackageVerifier)
    assert isinstance(
        registry.get_verifier("website/app/browser"), WebsiteAppBrowserVerifier
    )
    assert isinstance(
        registry.get_verifier("sprite/atlas/game import"), SpriteAtlasGameVerifier
    )
    assert isinstance(registry.get_verifier("mixed bundles"), MixedBundleVerifier)


def test_registry_duplicate_plugin_id_fails_closed():
    """Verify registering duplicate plugin ID fails closed."""
    registry = VerifierRegistry()
    duplicate_plugin = CodePackageVerifier()
    with pytest.raises(ValueError, match="Duplicate plugin ID"):
        registry.register(duplicate_plugin)


def test_registry_unknown_verifier_type_lookup_fails_closed():
    """Verify lookup of unknown verifier type fails closed."""
    registry = VerifierRegistry()
    with pytest.raises(KeyError, match="Unknown verifier type"):
        registry.get_verifier("unknown/type")


def test_code_package_verifier_pass(test_repo_and_artifact):
    """Verify code/package verifier execution with valid evidence and strict provenance."""
    registry = VerifierRegistry()
    candidate, context = make_valid_candidate_and_context(
        test_repo_and_artifact,
        verifier_type="code",
        evidence={
            "tests_passed": True,
            "coverage": 92.5,
            "lint_status": "clean",
            "install_readback": "verified",
        },
    )
    res = registry.verify("code/package", candidate, context)
    assert res["status"] == "pass"
    assert res["verifier_type"] == "code"
    assert res["type_specific_evidence"]["tests_passed"] is True
    assert res["marker"] == UNIVERSAL_OUTPUT_VERIFIER_REGISTRY_OK


def test_website_app_browser_verifier_pass(test_repo_and_artifact):
    """Verify website/app/browser verifier execution with valid evidence."""
    registry = VerifierRegistry()
    candidate, context = make_valid_candidate_and_context(
        test_repo_and_artifact,
        verifier_type="web/app",
        evidence={
            "build_status": "success",
            "lighthouse": {
                "performance": 0.95,
                "accessibility": 1.0,
                "best_practices": 0.98,
                "seo": 0.96,
            },
            "visual_qa": "passed",
            "browser_mobile_proof": "console_clean",
        },
    )
    res = registry.verify("website/app/browser", candidate, context)
    assert res["status"] == "pass"
    assert res["verifier_type"] == "web/app"


def test_image_design_verifier_pass(test_repo_and_artifact):
    """Verify image/design verifier execution with valid evidence."""
    registry = VerifierRegistry()
    candidate, context = make_valid_candidate_and_context(
        test_repo_and_artifact,
        verifier_type="image/design",
        evidence={
            "dimensions": [1920, 1080],
            "color_space": "sRGB",
            "similarity_score": 0.99,
            "color_alpha_proof": "rgba_alpha_clean",
        },
    )
    res = registry.verify("image/design", candidate, context)
    assert res["status"] == "pass"
    assert res["verifier_type"] == "image/design"


def test_sprite_atlas_game_verifier_pass(test_repo_and_artifact):
    """Verify sprite/atlas/game verifier execution with valid evidence."""
    registry = VerifierRegistry()
    candidate, context = make_valid_candidate_and_context(
        test_repo_and_artifact,
        verifier_type="sprite/game asset",
        evidence={
            "frame_count": 16,
            "spritesheet": "hero_sheet.png",
            "collision_boxes": [{"x": 0, "y": 0, "width": 16, "height": 16}],
            "atlas_geometry": {"grid": "16x16"},
        },
    )
    res = registry.verify("sprite/atlas/game import", candidate, context)
    assert res["status"] == "pass"
    assert res["verifier_type"] == "sprite/game asset"


def test_video_verifier_pass(test_repo_and_artifact):
    """Verify video verifier execution with valid evidence."""
    registry = VerifierRegistry()
    candidate, context = make_valid_candidate_and_context(
        test_repo_and_artifact,
        verifier_type="video",
        evidence={
            "resolution": "1920x1080",
            "duration_seconds": 60.0,
            "bitrate_kbps": 5000,
            "codec_fps_sync": "h264_60fps_synced",
        },
    )
    res = registry.verify("video", candidate, context)
    assert res["status"] == "pass"
    assert res["verifier_type"] == "video"


def test_audio_verifier_pass(test_repo_and_artifact):
    """Verify audio verifier execution with valid evidence."""
    registry = VerifierRegistry()
    candidate, context = make_valid_candidate_and_context(
        test_repo_and_artifact,
        verifier_type="audio",
        evidence={
            "channels": 2,
            "sample_rate_hz": 44100,
            "duration_seconds": 120.0,
            "loudness_silence_proof": "lufs_-14",
        },
    )
    res = registry.verify("audio", candidate, context)
    assert res["status"] == "pass"
    assert res["verifier_type"] == "audio"


def test_document_data_verifier_pass(test_repo_and_artifact):
    """Verify document/data verifier execution with valid evidence."""
    registry = VerifierRegistry()
    candidate, context = make_valid_candidate_and_context(
        test_repo_and_artifact,
        verifier_type="document/data",
        evidence={
            "format": "markdown",
            "valid": True,
            "schema_compliant": True,
        },
    )
    res = registry.verify("document/data", candidate, context)
    assert res["status"] == "pass"
    assert res["verifier_type"] == "document/data"


def test_mixed_bundle_verifier_pass(test_repo_and_artifact):
    """Verify mixed bundle verifier execution with valid evidence."""
    registry = VerifierRegistry()
    candidate, context = make_valid_candidate_and_context(
        test_repo_and_artifact,
        verifier_type="mixed",
        evidence={
            "submanifests_validated": 2,
            "child_digests": [
                {"locator": "code/main.py", "digest": "3" * 64},
                {"locator": "docs/readme.md", "digest": "4" * 64},
            ],
            "atomic_policy": "all_or_nothing",
        },
    )
    res = registry.verify("mixed bundles", candidate, context)
    assert res["status"] == "pass"
    assert res["verifier_type"] == "mixed"


def test_unknown_nested_properties_fail_closed(test_repo_and_artifact):
    """Verify closed schema: unknown top-level or nested properties fail closed."""
    registry = VerifierRegistry()
    candidate, context = make_valid_candidate_and_context(
        test_repo_and_artifact,
        verifier_type="code",
        evidence={
            "tests_passed": True,
            "coverage": 92.5,
            "lint_status": "clean",
            "install_readback": "verified",
            "unknown_extra_property": "should_fail",
        },
    )
    with pytest.raises(ValueError, match="contains unknown fields"):
        registry.verify("code", candidate, context)


def test_cross_type_evidence_mismatch_fails_closed(test_repo_and_artifact):
    """Verify a proof from code does NOT satisfy image/design contract."""
    registry = VerifierRegistry()
    candidate, context = make_valid_candidate_and_context(
        test_repo_and_artifact,
        verifier_type="image/design",
        evidence={
            "tests_passed": True,
            "coverage": 92.5,
            "lint_status": "clean",
            "install_readback": "verified",
        },
    )
    with pytest.raises(ValueError, match="missing required fields"):
        registry.verify("image/design", candidate, context)


def test_explicit_unavailable_status_cannot_promote(test_repo_and_artifact):
    """Verify unavailable status never normalizes to pass or decision_ready."""
    registry = VerifierRegistry()
    candidate, context = make_valid_candidate_and_context(
        test_repo_and_artifact,
        verifier_type="code",
        evidence={
            "tests_passed": {"status": "unavailable", "reason": "pytest tool missing"},
            "coverage": {"status": "unavailable", "reason": "pytest tool missing"},
            "lint_status": {"status": "unavailable", "reason": "pytest tool missing"},
            "install_readback": {
                "status": "unavailable",
                "reason": "pytest tool missing",
            },
        },
    )
    res = registry.verify("code", candidate, context)
    assert res["status"] == "unavailable"
    assert res["unavailable_reason"] is not None

    base_manifest = {
        "schema_version": 2.0,
        "bundle_type": "code",
        "deliverables": ["prismatic/engine.py"],
        "content_hashes": {"prismatic/engine.py": "a" * 64},
        "provenance": {"toolchain": "python3", "builder": "pytest"},
        "source_revision": {
            "source_commit_sha": test_repo_and_artifact["commit_sha"],
            "base_commit_sha": test_repo_and_artifact["commit_sha"],
            "branch": test_repo_and_artifact["branch"],
        },
        "path_scope": {
            "allowed_paths": ["prismatic/engine.py"],
            "touched_paths": ["prismatic/engine.py"],
        },
        "privacy_retention": {
            "privacy_level": "internal",
            "retention_status": "retained",
            "retention_reasons": ["test"],
            "retention_policy": "default",
        },
        "type_specific_proofs": {
            "code": {
                "tests_passed": {"status": "unavailable", "reason": "tooling missing"},
                "coverage": {"status": "unavailable", "reason": "tooling missing"},
                "lint_status": {"status": "unavailable", "reason": "tooling missing"},
            }
        },
        "preview_handles": [],
        "licenses": ["MIT"],
        "non_claims": ["perf"],
        "candidate_sha_destination": {
            "candidate_sha": test_repo_and_artifact["commit_sha"],
            "destination_branch": test_repo_and_artifact["branch"],
        },
        "review_digest": "b" * 64,
        "integration_receipt": {
            "receipt_id": "rec1",
            "timestamp": "2026-07-21T14:00:00Z",
            "status": "ok",
        },
        "rollback": {
            "revert_instructions": "git revert",
            "script_path": "scripts/rollback.sh",
        },
        "post_integration_proof": "passed",
        "marker": "UNIVERSAL_RESULT_MANIFEST_V2_OK",
    }

    bound_manifest = registry.bind_to_universal_manifest(res, base_manifest)
    ready, reasons = is_promotion_ready(bound_manifest)
    assert not ready
    assert any("unavailable" in r.lower() or "tests" in r.lower() for r in reasons)


def test_mixed_bundle_traversal_locators_fail_closed(test_repo_and_artifact):
    """Verify mixed bundle locators with traversal, userinfo, credentials fail closed."""
    ok, err = validate_locator_safety("../etc/passwd")
    assert not ok
    assert "traversal" in err

    ok, err = validate_locator_safety("http://user:secretpass@example.com/file")
    assert not ok
    assert "userinfo" in err or "Unsafe" in err

    fake_token = "".join(["ghp_", "1234567890abcdef", "1234567890abcdef", "1234"])
    ok, err = validate_locator_safety(fake_token)
    assert not ok
    assert "credential" in err or "REDACTED_SECRET" in err

    registry = VerifierRegistry()
    candidate, context = make_valid_candidate_and_context(
        test_repo_and_artifact,
        verifier_type="mixed",
        evidence={
            "submanifests_validated": 1,
            "child_digests": [
                {"locator": "../secret/db.json", "digest": "3" * 64},
            ],
            "atomic_policy": "all_or_nothing",
        },
    )
    with pytest.raises(ValueError, match="locator error"):
        registry.verify("mixed bundles", candidate, context)


def test_secret_sanitization_in_logs_and_results():
    """Verify matched secret values are redacted in durable logs and verifier results."""
    secret_token = "ghp_" + "A" * 36
    log_text = (
        f"Running build with token={secret_token} and AWS key AKIAIOSFODNN7EXAMPLE"
    )
    log_path, digest = write_durable_log(log_text, prefix="agy-GRO-4114-test-secret")

    with log_path.open("r", encoding="utf-8") as f:
        content = f.read()
    assert secret_token not in content
    assert "AKIAIOSFODNN7EXAMPLE" not in content
    assert "[REDACTED_SECRET]" in content

    if log_path.exists():
        log_path.unlink()


# ============================================================================
# EXPLICIT TESTS FOR ALL 11 REQUIRED NEGATIVE PROVENANCE CONDITIONS
# ============================================================================


def test_negative_1_missing_identity(test_repo_and_artifact):
    """Negative 1: Missing identity (task_id missing from candidate & context)."""
    candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)
    candidate.pop("task_id", None)
    context.pop("task_id", None)
    with pytest.raises(ValueError, match="Missing identity"):
        enforce_strict_provenance(candidate, context)


def test_negative_2_malformed_all_zero_sentinel_digest(test_repo_and_artifact):
    """Negative 2: Malformed, all-zero, or sentinel candidate_digest."""
    candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)

    # Malformed
    candidate["candidate_digest"] = "invalid-digest"
    with pytest.raises(ValueError, match="Invalid candidate_digest provenance"):
        enforce_strict_provenance(candidate, context)

    # All zero
    candidate["candidate_digest"] = "0" * 64
    with pytest.raises(ValueError, match="Invalid candidate_digest provenance"):
        enforce_strict_provenance(candidate, context)

    # All f
    candidate["candidate_digest"] = "f" * 64
    with pytest.raises(ValueError, match="Invalid candidate_digest provenance"):
        enforce_strict_provenance(candidate, context)


def test_negative_3_missing_artifact_locator(test_repo_and_artifact):
    """Negative 3: Missing artifact locator."""
    candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)
    candidate.pop("artifact_locator", None)
    context.pop("artifact_locator", None)
    with pytest.raises(ValueError, match="Missing required retained artifact locator"):
        enforce_strict_provenance(candidate, context)


def test_negative_4_artifact_byte_mismatch(test_repo_and_artifact, tmp_path):
    """Negative 4: Artifact byte mismatch (file content digest != candidate_digest)."""
    candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)
    tampered_file = tmp_path / "tampered_artifact.tar.gz"
    tampered_file.write_bytes(b"different_content_bytes")
    candidate["artifact_locator"] = str(tampered_file)
    # candidate_digest is still original artifact digest
    with pytest.raises(ValueError, match="Artifact byte mismatch"):
        enforce_strict_provenance(candidate, context)


def test_negative_5_stale_prior_commit(test_repo_and_artifact):
    """Negative 5: Stale prior commit (commit SHA exists in history but is not current HEAD)."""
    repo_dir = Path(test_repo_and_artifact["repo_path"])
    c1 = test_repo_and_artifact["commit_sha"]

    # Make second commit C2
    (repo_dir / "file2.py").write_text("print('second')", encoding="utf-8")
    subprocess.run(
        ["git", "add", "file2.py"], cwd=str(repo_dir), check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "Second commit"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
    )

    candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)
    context["source_lineage"]["source_commit_sha"] = c1  # stale C1
    with pytest.raises(
        ValueError, match="Source revision mismatch or stale prior commit"
    ):
        enforce_strict_provenance(candidate, context)


def test_negative_6_current_tree_hash_supplied_as_commit(test_repo_and_artifact):
    """Negative 6: Current tree hash supplied as commit SHA."""
    repo_dir = Path(test_repo_and_artifact["repo_path"])
    tree_sha = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)
    context["source_lineage"]["source_commit_sha"] = tree_sha
    with pytest.raises(
        ValueError, match="Invalid source_commit_sha object type|not a commit object"
    ):
        enforce_strict_provenance(candidate, context)


def test_negative_7_nonexistent_40_hex_commit(test_repo_and_artifact):
    """Negative 7: Nonexistent 40-hex commit SHA."""
    candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)
    context["source_lineage"]["source_commit_sha"] = (
        "1234567890123456789012345678901234567890"
    )
    with pytest.raises(ValueError, match="nonexistent 40-hex commit|not found"):
        enforce_strict_provenance(candidate, context)


def test_negative_8_wrong_branch_or_base(test_repo_and_artifact):
    """Negative 8: Wrong branch or wrong base commit."""
    candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)

    # Wrong branch
    context["source_lineage"]["branch"] = "wrong_branch_name"
    with pytest.raises(ValueError, match="Wrong branch"):
        enforce_strict_provenance(candidate, context)

    # Reset branch and test wrong base commit
    context["source_lineage"]["branch"] = test_repo_and_artifact["branch"]
    context["source_lineage"]["base_commit_sha"] = (
        "1234567890123456789012345678901234567890"
    )
    with pytest.raises(ValueError, match="base_commit_sha '.*' not found"):
        enforce_strict_provenance(candidate, context)


def test_negative_9_dirty_uncommitted_revision(test_repo_and_artifact):
    """Negative 9: Dirty/uncommitted revision in repository."""
    repo_dir = Path(test_repo_and_artifact["repo_path"])
    (repo_dir / "dirty_file.py").write_text("# uncommitted change\n", encoding="utf-8")

    candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)
    with pytest.raises(ValueError, match="Dirty/uncommitted revision"):
        enforce_strict_provenance(candidate, context)


def test_negative_10_absent_repository_or_artifact_context(test_repo_and_artifact):
    """Negative 10: Absent repository context (repo_path missing)."""
    candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)
    context.pop("repo_path", None)
    with pytest.raises(ValueError, match="Absent repository context"):
        enforce_strict_provenance(candidate, context)


def test_negative_11_revision_mismatch(test_repo_and_artifact):
    """Negative 11: Revision mismatch (source_commit_sha != expected_revision)."""
    candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)
    context["expected_revision"] = "a" * 40
    with pytest.raises(ValueError, match="Revision mismatch"):
        enforce_strict_provenance(candidate, context)


def test_positive_exact_committed_candidate_and_retained_bytes(test_repo_and_artifact):
    """Positive: Exact committed candidate, explicit retained artifact bytes, exact real commit/base/branch."""
    candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)
    digest, lineage, locator = enforce_strict_provenance(candidate, context)
    assert digest == test_repo_and_artifact["artifact_digest"]
    assert lineage["source_commit_sha"] == test_repo_and_artifact["commit_sha"]
    assert lineage["branch"] == test_repo_and_artifact["branch"]
    assert locator == test_repo_and_artifact["artifact_path"]


def test_log_mutation_after_digest_invalidates_result(tmp_path):
    """Log content mutation after digest calculation invalidates verifier result."""
    log_file = tmp_path / "test_mutation.log"
    log_file.write_text("Initial log content", encoding="utf-8")
    original_digest = hashlib.sha256(b"Initial log content").hexdigest()

    log_file.write_text("Tampered log content after digest", encoding="utf-8")

    res = {
        "task_id": "GRO-4114",
        "verifier_type": "code",
        "verifier_version": "1.0.0",
        "candidate_digest": "a" * 64,
        "source_lineage": {
            "source_commit_sha": "17983619345b74c31cc3fcb7b22c55888a962cd7",
            "branch": "feature/gro-4114",
        },
        "command": "pytest",
        "environment": {"os": "linux"},
        "log_path": str(log_file),
        "log_digest": original_digest,
        "proof_class": "install_readback",
        "status": "pass",
        "scope": {"target_paths": ["prismatic/"]},
        "non_claims": [],
        "timestamp": "2026-07-21T14:00:00Z",
        "type_specific_evidence": {
            "tests_passed": True,
            "coverage": 95.0,
            "lint_status": "clean",
            "install_readback": "verified",
        },
        "marker": UNIVERSAL_OUTPUT_VERIFIER_REGISTRY_OK,
    }
    ok, errors = validate_verifier_result(res)
    assert not ok
    assert any("digest mismatch" in e.lower() for e in errors)


def test_schema_parity_root_and_package():
    """Verify root schemas/verifier-result.schema.json and prismatic/schemas/ match identically."""
    root_p = (
        Path(__file__).resolve().parents[1] / "schemas" / "verifier-result.schema.json"
    )
    pkg_p = (
        Path(__file__).resolve().parents[1]
        / "prismatic"
        / "schemas"
        / "verifier-result.schema.json"
    )
    assert root_p.read_bytes() == pkg_p.read_bytes()


# ============================================================================
# EXPLICIT REPAIR 4 REGRESSION TESTS FOR ALL 5 REPRODUCED BYPASSES
# ============================================================================


def test_bypass_1_untracked_log_file_rejected(test_repo_and_artifact):
    """Bypass 1: Reject arbitrary untracked or modified *.log file in repository."""
    repo_dir = Path(test_repo_and_artifact["repo_path"])
    (repo_dir / "arbitrary_debug.log").write_text("log output\n", encoding="utf-8")

    candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)
    with pytest.raises(ValueError, match="Dirty/uncommitted revision"):
        enforce_strict_provenance(candidate, context)


def test_bypass_2_untracked_tmp_file_rejected(test_repo_and_artifact):
    """Bypass 2: Reject arbitrary untracked or modified file in tmp/ directory."""
    repo_dir = Path(test_repo_and_artifact["repo_path"])
    tmp_sub = repo_dir / "tmp"
    tmp_sub.mkdir(exist_ok=True)
    (tmp_sub / "scratch.txt").write_text("untracked tmp file\n", encoding="utf-8")

    candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)
    with pytest.raises(ValueError, match="Dirty/uncommitted revision"):
        enforce_strict_provenance(candidate, context)


def test_bypass_3_symlink_artifact_locator_rejected(test_repo_and_artifact, tmp_path):
    """Bypass 3: Reject symlink artifact locator file and symlink parent components."""
    candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)

    # 3a. Symlink file locator
    sym_file = tmp_path / "sym_artifact.tar.gz"
    sym_file.symlink_to(test_repo_and_artifact["artifact_path"])
    candidate["artifact_locator"] = str(sym_file)

    with pytest.raises(ValueError, match="Symlink artifact locator"):
        enforce_strict_provenance(candidate, context)

    # 3b. Symlink parent directory in locator path
    sym_dir = tmp_path / "sym_dir"
    sym_dir.symlink_to(tmp_path)
    sym_parent_loc = str(sym_dir / "retained_artifact.tar.gz")
    candidate["artifact_locator"] = sym_parent_loc

    with pytest.raises(ValueError, match="Symlink artifact locator or path component"):
        enforce_strict_provenance(candidate, context)


def test_bypass_4_artifact_outside_allowed_root_rejected(
    test_repo_and_artifact, tmp_path
):
    """Bypass 4: Require allowed_artifact_root and reject artifact outside allowed_artifact_root."""
    candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)

    # 4a. Missing allowed_artifact_root
    context.pop("allowed_artifact_root", None)
    with pytest.raises(
        ValueError, match="allowed_artifact_root must be explicitly provided"
    ):
        enforce_strict_provenance(candidate, context)

    # 4b. Invalid / symlinked allowed_artifact_root
    sym_root = tmp_path / "sym_root"
    sym_root.symlink_to(tmp_path)
    context["allowed_artifact_root"] = str(sym_root)
    candidate["artifact_locator"] = test_repo_and_artifact["artifact_path"]
    with pytest.raises(
        ValueError, match="allowed_artifact_root or path component is a symlink"
    ):
        enforce_strict_provenance(candidate, context)

    # 4c. Artifact outside allowed_artifact_root
    outside_dir = tmp_path / "outside_root"
    outside_dir.mkdir()
    context["allowed_artifact_root"] = str(outside_dir)
    with pytest.raises(ValueError, match="is outside allowed_artifact_root"):
        enforce_strict_provenance(candidate, context)


def test_bypass_5_unrelated_or_descendant_base_commit_rejected(
    test_repo_and_artifact,
):
    """Bypass 5: Reject unrelated commit or descendant commit as base_commit_sha."""
    repo_dir = Path(test_repo_and_artifact["repo_path"])
    c1 = test_repo_and_artifact["commit_sha"]

    # 5a. Make a second commit C2
    (repo_dir / "second.py").write_text("c2\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "second.py"], cwd=str(repo_dir), check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "C2"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
    )
    c2 = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)
    context["allowed_artifact_root"] = test_repo_and_artifact["allowed_artifact_root"]

    # Descendant C2 supplied as base for source C1
    context["source_lineage"]["source_commit_sha"] = c1
    # checkout back to c1 to make HEAD == c1
    subprocess.run(
        ["git", "checkout", c1], cwd=str(repo_dir), check=True, capture_output=True
    )
    context["source_lineage"]["branch"] = "HEAD"
    context["source_lineage"]["base_commit_sha"] = c2  # C2 is descendant of C1!
    with pytest.raises(
        ValueError, match="base_commit_sha '.*' is not an ancestor of source_commit_sha"
    ):
        enforce_strict_provenance(candidate, context)

    # 5b. Unrelated commit from an orphaned branch
    subprocess.run(
        ["git", "checkout", "--orphan", "orphan_branch"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
    )
    (repo_dir / "orphan.py").write_text("orphan\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "orphan.py"], cwd=str(repo_dir), check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "Orphan commit"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
    )
    orphan_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    # Switch back to c2 branch
    subprocess.run(
        ["git", "checkout", test_repo_and_artifact["branch"]],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
    )
    head_c2 = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    context["source_lineage"]["branch"] = test_repo_and_artifact["branch"]
    context["source_lineage"]["source_commit_sha"] = head_c2
    context["source_lineage"]["base_commit_sha"] = (
        orphan_sha  # orphan commit is NOT ancestor of head_c2!
    )
    with pytest.raises(
        ValueError, match="base_commit_sha '.*' is not an ancestor of source_commit_sha"
    ):
        enforce_strict_provenance(candidate, context)


def test_positive_exact_retained_regular_artifact_with_ancestral_base(
    test_repo_and_artifact,
):
    """Positive: Exact retained regular artifact inside allowed_artifact_root with ancestral base_commit_sha."""
    repo_dir = Path(test_repo_and_artifact["repo_path"])
    c1 = test_repo_and_artifact["commit_sha"]

    (repo_dir / "file2.py").write_text("update\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "file2.py"], cwd=str(repo_dir), check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "Commit 2"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
    )
    c2 = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)
    context["source_lineage"]["source_commit_sha"] = c2
    context["source_lineage"]["base_commit_sha"] = c1  # C1 is ancestor of C2
    context["allowed_artifact_root"] = test_repo_and_artifact["allowed_artifact_root"]

    digest, lineage, loc = enforce_strict_provenance(candidate, context)
    assert digest == test_repo_and_artifact["artifact_digest"]
    assert lineage["source_commit_sha"] == c2
    assert lineage["base_commit_sha"] == c1
    assert loc == test_repo_and_artifact["artifact_path"]


# ============================================================================
# REPAIR 5 ADVERSARIAL TESTS FOR LIFECYCLE ARTIFACTS AND PORCELAIN STATUS
# ============================================================================


def test_reject_untracked_lifecycle_file_as_artifact_locator(test_repo_and_artifact):
    """Reject untracked lifecycle files (RESULT.md, AGY_TASK.md, STARTED.md) as artifact locators (relative & absolute)."""
    repo_dir = Path(test_repo_and_artifact["repo_path"])
    lifecycle_names = ["RESULT.md", "AGY_TASK.md", "STARTED.md"]

    for name in lifecycle_names:
        lf = repo_dir / name
        lf_bytes = f"lifecycle content for {name}".encode()
        lf.write_bytes(lf_bytes)
        lf_digest = hashlib.sha256(lf_bytes).hexdigest()

        candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)
        candidate["candidate_digest"] = lf_digest

        # Relative locator
        candidate["artifact_locator"] = name
        with pytest.raises(
            ValueError, match="resolves to a lifecycle allowlist path inside repository"
        ):
            enforce_strict_provenance(candidate, context)

        # Relative locator with ./
        candidate["artifact_locator"] = f"./{name}"
        with pytest.raises(
            ValueError, match="resolves to a lifecycle allowlist path inside repository"
        ):
            enforce_strict_provenance(candidate, context)

        # Absolute locator
        candidate["artifact_locator"] = str(lf.resolve())
        with pytest.raises(
            ValueError, match="resolves to a lifecycle allowlist path inside repository"
        ):
            enforce_strict_provenance(candidate, context)

        # Clean up untracked file so next iteration starts fresh
        lf.unlink()


def test_reject_modified_staged_deleted_tracked_lifecycle_files(test_repo_and_artifact):
    """Reject modified, staged, or deleted tracked lifecycle files in worktree."""
    repo_dir = Path(test_repo_and_artifact["repo_path"])
    res_file = repo_dir / "RESULT.md"
    res_file.write_text("initial result\n", encoding="utf-8")

    subprocess.run(
        ["git", "add", "RESULT.md"], cwd=str(repo_dir), check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "-m", "Track RESULT.md"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
    )
    new_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)
    context["source_lineage"]["source_commit_sha"] = new_head

    # Subtest 1: Modified tracked lifecycle file
    res_file.write_text("modified result\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Dirty/uncommitted revision"):
        enforce_strict_provenance(candidate, context)

    # Subtest 2: Staged tracked lifecycle file
    subprocess.run(
        ["git", "add", "RESULT.md"], cwd=str(repo_dir), check=True, capture_output=True
    )
    with pytest.raises(ValueError, match="Dirty/uncommitted revision"):
        enforce_strict_provenance(candidate, context)

    # Subtest 3: Deleted tracked lifecycle file
    subprocess.run(
        ["git", "checkout", "HEAD", "--", "RESULT.md"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
    )
    res_file.unlink()
    with pytest.raises(ValueError, match="Dirty/uncommitted revision"):
        enforce_strict_provenance(candidate, context)


def test_root_vs_nested_lifecycle_lookalikes(test_repo_and_artifact):
    """Verify root untracked lifecycle file is tolerated for supervisor, but nested lookalikes are rejected."""
    repo_dir = Path(test_repo_and_artifact["repo_path"])

    # Root untracked RESULT.md present
    (repo_dir / "RESULT.md").write_text("supervisor result\n", encoding="utf-8")

    candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)

    # Root untracked lifecycle file is tolerated when candidate locator points to retained artifact
    digest, lineage, loc = enforce_strict_provenance(candidate, context)
    assert digest == test_repo_and_artifact["artifact_digest"]

    # Nested untracked RESULT.md is NOT tolerated
    sub_dir = repo_dir / "sub"
    sub_dir.mkdir(exist_ok=True)
    (sub_dir / "RESULT.md").write_text("nested result\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Dirty/uncommitted revision"):
        enforce_strict_provenance(candidate, context)


def test_reject_rename_and_copy_status(test_repo_and_artifact):
    """Reject repository state with renamed or copied files."""
    repo_dir = Path(test_repo_and_artifact["repo_path"])

    subprocess.run(
        ["git", "mv", "main.py", "renamed_main.py"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
    )

    candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)
    with pytest.raises(ValueError, match="Dirty/uncommitted revision"):
        enforce_strict_provenance(candidate, context)


def test_spaces_quotes_unicode_filenames_no_parser_truncation(test_repo_and_artifact):
    """Verify status parser handles spaces, quotes, and unicode filenames without truncation."""
    repo_dir = Path(test_repo_and_artifact["repo_path"])

    # File with spaces
    space_file = repo_dir / "path with spaces.py"
    space_file.write_text("print('space')\n", encoding="utf-8")

    candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)
    with pytest.raises(ValueError, match="path with spaces.py"):
        enforce_strict_provenance(candidate, context)

    space_file.unlink()

    # Unicode file
    unicode_file = repo_dir / "unicode_🚀_file.py"
    unicode_file.write_text("print('unicode')\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unicode_🚀_file.py"):
        enforce_strict_provenance(candidate, context)

    unicode_file.unlink()

    # File with quotes
    quote_file = repo_dir / "file_with'quote.py"
    quote_file.write_text("print('quote')\n", encoding="utf-8")

    with pytest.raises(ValueError, match="file_with'quote.py"):
        enforce_strict_provenance(candidate, context)

    quote_file.unlink()


def test_reject_arbitrary_tracked_and_untracked_dirty_files(test_repo_and_artifact):
    """Reject arbitrary dirty files including untracked .log and tmp/ directory contents."""
    repo_dir = Path(test_repo_and_artifact["repo_path"])
    candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)

    # Subtest 1: Untracked .log file
    log_file = repo_dir / "build_output.log"
    log_file.write_text("log data\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Dirty/uncommitted revision"):
        enforce_strict_provenance(candidate, context)
    log_file.unlink()

    # Subtest 2: Untracked file in tmp/ directory
    tmp_sub = repo_dir / "tmp"
    tmp_sub.mkdir(exist_ok=True)
    tmp_file = tmp_sub / "scratch.txt"
    tmp_file.write_text("scratch\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Dirty/uncommitted revision"):
        enforce_strict_provenance(candidate, context)
    tmp_file.unlink()

    # Subtest 3: Modified tracked file
    (repo_dir / "main.py").write_text("print('modified')\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Dirty/uncommitted revision"):
        enforce_strict_provenance(candidate, context)


def test_secret_shaped_dirty_filename_is_redacted_but_harmless_secret_word_remains(
    test_repo_and_artifact,
):
    """Credential-shaped Git status paths must not be copied into verifier errors."""
    repo_dir = Path(test_repo_and_artifact["repo_path"])
    candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)

    credential_tail = "a" * 24
    credential_filename = "client_" + "secret=" + credential_tail + ".txt"
    (repo_dir / credential_filename).write_text("not retained\n", encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        enforce_strict_provenance(candidate, context)

    message = str(exc_info.value)
    assert "Dirty/uncommitted revision" in message
    assert credential_filename not in message
    assert credential_tail not in message
    assert "[REDACTED_SECRET]" in message

    (repo_dir / credential_filename).unlink()
    harmless_filename = "SECRET_notes_without_credential_syntax.txt"
    (repo_dir / harmless_filename).write_text("not retained\n", encoding="utf-8")

    with pytest.raises(ValueError) as harmless_exc:
        enforce_strict_provenance(candidate, context)

    harmless_message = str(harmless_exc.value)
    assert harmless_filename in harmless_message
    assert "[REDACTED_SECRET]" not in harmless_message


def test_secret_shaped_git_status_stdout_and_stderr_are_redacted(
    test_repo_and_artifact,
    monkeypatch,
):
    """Credential-shaped Git status stdout/stderr must be sanitized before raising."""
    candidate, context = make_valid_candidate_and_context(test_repo_and_artifact)
    real_run = subprocess.run
    credential_tail = "b" * 24
    credential_component = "api_" + "key=" + credential_tail + ".txt"

    class StatusResult:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def status_stdout_run(cmd, *args, **kwargs):
        if cmd == ["git", "status", "--porcelain=v1", "-z"]:
            return StatusResult(stdout="?? " + credential_component + "\0")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", status_stdout_run)
    with pytest.raises(ValueError) as stdout_exc:
        enforce_strict_provenance(candidate, context)
    stdout_message = str(stdout_exc.value)
    assert credential_component not in stdout_message
    assert credential_tail not in stdout_message
    assert "[REDACTED_SECRET]" in stdout_message

    def status_stderr_run(cmd, *args, **kwargs):
        if cmd == ["git", "status", "--porcelain=v1", "-z"]:
            return StatusResult(returncode=1, stderr="fatal: " + credential_component)
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", status_stderr_run)
    with pytest.raises(ValueError) as stderr_exc:
        enforce_strict_provenance(candidate, context)
    stderr_message = str(stderr_exc.value)
    assert credential_component not in stderr_message
    assert credential_tail not in stderr_message
    assert "[REDACTED_SECRET]" in stderr_message
