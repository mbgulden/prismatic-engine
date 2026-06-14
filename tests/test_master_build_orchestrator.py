"""Tests for scripts/master_build_orchestrator.py and master_seed_verifier.py — GRO-1623.

Covers: CLI parsing, manifest validation, schema checks, auto-heal,
pre-req ledger detection, telemetry config generation, seed verifier.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure the scripts are importable
SCRIPT_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, SCRIPT_DIR)

from master_build_orchestrator import (
    ANCHOR_MANIFEST_SCHEMA,
    PRE_REQ_LEDGERS,
    DEFAULT_TELEMETRY_BOOT_CONFIG,
    ValidationError,
    ValidationReport,
    build_parser,
    validate_manifest_schema,
    check_pre_req_ledgers,
    load_manifest,
    generate_seed_manifest,
    auto_heal_manifest,
    write_telemetry_boot_config,
    run_validation,
    format_report,
    main,
)
from master_seed_verifier import (
    verify_manifest as seed_verify_manifest,
    verify_from_git,
)


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as td:
        yield td


@pytest.fixture
def valid_manifest():
    """A fully valid anchor_manifest.json."""
    return {
        "ledger_name": "anchor_manifest",
        "phase_id": 3,
        "schema_version": "1.0.0",
        "assets": [
            {
                "asset_id": "prism://hero/character_model",
                "type": "mesh",
                "storage_uri": "prism://models/hero.glb",
                "sha256_hash": "a" * 64,
                "size_bytes": 1048576,
                "replayability": {
                    "model_identifier": "imagen-3",
                    "seed": 42,
                    "input_prompt": "A heroic character model",
                    "hyperparameters": {"cfg_scale": 7.0},
                },
                "dependencies": [],
                "resource_footprint": {
                    "vram_allocated_bytes": 524288,
                    "gpu_processing_time_ms": 120,
                },
            }
        ],
    }


@pytest.fixture
def valid_manifest_path(tmp_dir, valid_manifest):
    """Write a valid manifest to a temp file."""
    path = os.path.join(tmp_dir, "anchor_manifest.json")
    with open(path, "w") as f:
        json.dump(valid_manifest, f)
    return path


@pytest.fixture
def design_guides_dir(tmp_dir):
    """Create a design_guides directory with all pre-req ledgers."""
    dg = os.path.join(tmp_dir, "design_guides")
    os.makedirs(dg, exist_ok=True)
    for ledger in PRE_REQ_LEDGERS:
        ledger_path = os.path.join(dg, ledger)
        with open(ledger_path, "w") as f:
            json.dump({"ledger_name": ledger, "phase_id": 1, "schema_version": "1.0.0", "assets": []}, f)
    return dg


@pytest.fixture
def seed_dir(tmp_dir):
    """Create a seed directory with sample JSON files."""
    sd = os.path.join(tmp_dir, "context", "state")
    os.makedirs(sd, exist_ok=True)
    with open(os.path.join(sd, "system_optimization_rules.json"), "w") as f:
        json.dump({"rules": ["optimize_vram", "prefer_bc7"]}, f)
    return sd


# ── load_manifest ────────────────────────────────────────────


class TestLoadManifest:
    def test_loads_valid_json(self, valid_manifest_path):
        data, err = load_manifest(valid_manifest_path)
        assert err is None
        assert data["ledger_name"] == "anchor_manifest"

    def test_returns_error_for_missing_file(self, tmp_dir):
        path = os.path.join(tmp_dir, "nonexistent.json")
        data, err = load_manifest(path)
        assert data is None
        assert "not found" in err.lower()

    def test_returns_error_for_invalid_json(self, tmp_dir):
        path = os.path.join(tmp_dir, "bad.json")
        with open(path, "w") as f:
            f.write("{invalid json")
        data, err = load_manifest(path)
        assert data is None
        assert "invalid json" in err.lower()


# ── validate_manifest_schema ─────────────────────────────────


class TestValidateManifestSchema:
    def test_valid_manifest_passes(self, valid_manifest):
        report = ValidationReport("test.json")
        validate_manifest_schema(valid_manifest, report)
        assert report.valid
        assert len(report.errors) == 0

    def test_missing_required_field(self):
        manifest = {"ledger_name": "test"}  # missing phase_id, schema_version, assets
        report = ValidationReport("test.json")
        validate_manifest_schema(manifest, report)
        assert not report.valid
        assert any("phase_id" in e.message for e in report.errors)

    def test_wrong_type_for_phase_id(self):
        manifest = {
            "ledger_name": "test",
            "phase_id": "not_a_number",
            "schema_version": "1.0.0",
            "assets": [],
        }
        report = ValidationReport("test.json")
        validate_manifest_schema(manifest, report)
        assert not report.valid
        assert any("integer" in e.message.lower() for e in report.errors)

    def test_invalid_asset_id_pattern(self):
        manifest = {
            "ledger_name": "test",
            "phase_id": 3,
            "schema_version": "1.0.0",
            "assets": [
                {
                    "asset_id": "not-a-prism-uri",
                    "type": "mesh",
                    "storage_uri": "prism://test",
                    "sha256_hash": "a" * 64,
                    "size_bytes": 100,
                    "replayability": {"model_identifier": "test", "seed": 1},
                }
            ],
        }
        report = ValidationReport("test.json")
        validate_manifest_schema(manifest, report)
        assert not report.valid

    def test_invalid_asset_type_enum(self):
        manifest = {
            "ledger_name": "test",
            "phase_id": 3,
            "schema_version": "1.0.0",
            "assets": [
                {
                    "asset_id": "prism://test",
                    "type": "invalid_type",
                    "storage_uri": "prism://test",
                    "sha256_hash": "a" * 64,
                    "size_bytes": 100,
                    "replayability": {"model_identifier": "test", "seed": 1},
                }
            ],
        }
        report = ValidationReport("test.json")
        validate_manifest_schema(manifest, report)
        assert not report.valid

    def test_phase_id_below_minimum(self):
        manifest = {
            "ledger_name": "test",
            "phase_id": 0,  # below minimum of 1
            "schema_version": "1.0.0",
            "assets": [],
        }
        report = ValidationReport("test.json")
        validate_manifest_schema(manifest, report)
        assert not report.valid

    def test_phase_id_above_maximum(self):
        manifest = {
            "ledger_name": "test",
            "phase_id": 99,  # above maximum of 62
            "schema_version": "1.0.0",
            "assets": [],
        }
        report = ValidationReport("test.json")
        validate_manifest_schema(manifest, report)
        assert not report.valid

    def test_invalid_schema_version_pattern(self):
        manifest = {
            "ledger_name": "test",
            "phase_id": 3,
            "schema_version": "v1",
            "assets": [],
        }
        report = ValidationReport("test.json")
        validate_manifest_schema(manifest, report)
        assert not report.valid

    def test_invalid_sha256_hash(self):
        manifest = {
            "ledger_name": "test",
            "phase_id": 3,
            "schema_version": "1.0.0",
            "assets": [
                {
                    "asset_id": "prism://test",
                    "type": "mesh",
                    "storage_uri": "prism://test",
                    "sha256_hash": "short",
                    "size_bytes": 100,
                    "replayability": {"model_identifier": "test", "seed": 1},
                }
            ],
        }
        report = ValidationReport("test.json")
        validate_manifest_schema(manifest, report)
        assert not report.valid

    def test_warns_on_unknown_fields(self, valid_manifest):
        valid_manifest["extra_field"] = "unexpected"
        report = ValidationReport("test.json")
        validate_manifest_schema(valid_manifest, report)
        # Unknown fields are warnings, not errors — manifest still valid
        assert report.valid
        assert len(report.warnings) > 0


# ── check_pre_req_ledgers ────────────────────────────────────


class TestCheckPreReqLedgers:
    def test_all_ledgers_present(self, design_guides_dir):
        report = ValidationReport("test.json")
        check_pre_req_ledgers(design_guides_dir, report)
        assert report.valid
        assert len(report.missing_ledgers) == 0

    def test_missing_directory(self, tmp_dir):
        missing_dir = os.path.join(tmp_dir, "nonexistent")
        report = ValidationReport("test.json")
        check_pre_req_ledgers(missing_dir, report)
        assert not report.valid
        assert len(report.missing_ledgers) == len(PRE_REQ_LEDGERS)

    def test_missing_single_ledger(self, tmp_dir):
        dg = os.path.join(tmp_dir, "design_guides")
        os.makedirs(dg, exist_ok=True)
        # Only create 2 of 3 ledgers
        for ledger in PRE_REQ_LEDGERS[:2]:
            with open(os.path.join(dg, ledger), "w") as f:
                json.dump({}, f)
        report = ValidationReport("test.json")
        check_pre_req_ledgers(dg, report)
        assert not report.valid
        assert PRE_REQ_LEDGERS[2] in report.missing_ledgers


# ── auto_heal_manifest ───────────────────────────────────────


class TestAutoHealManifest:
    def test_auto_heal_creates_valid_manifest(self, tmp_dir, seed_dir):
        manifest_path = os.path.join(tmp_dir, "anchor_manifest.json")
        design_dir = os.path.join(tmp_dir, "design_guides")
        os.makedirs(design_dir, exist_ok=True)
        # Create all pre-req ledgers so validation passes
        for ledger in PRE_REQ_LEDGERS:
            with open(os.path.join(design_dir, ledger), "w") as f:
                json.dump({"ledger_name": ledger, "phase_id": 1, "schema_version": "1.0.0", "assets": []}, f)

        report = ValidationReport(manifest_path)
        result = auto_heal_manifest(manifest_path, design_dir, report, seed_dir=seed_dir)
        assert result is True
        assert report.auto_heal_attempted
        assert report.auto_heal_succeeded
        assert os.path.exists(manifest_path)

        # Verify the healed manifest is valid JSON
        with open(manifest_path, "r") as f:
            data = json.load(f)
        assert data["ledger_name"] == "anchor_manifest"
        assert data["phase_id"] == 3

    def test_auto_heal_fails_without_seed_data(self, tmp_dir):
        manifest_path = os.path.join(tmp_dir, "anchor_manifest.json")
        design_dir = os.path.join(tmp_dir, "design_guides")
        os.makedirs(design_dir, exist_ok=True)
        # Pass explicit nonexistent seed dir so it doesn't find the real repo seed data
        nonexistent_seed = os.path.join(tmp_dir, "nonexistent_seed")

        report = ValidationReport(manifest_path)
        result = auto_heal_manifest(manifest_path, design_dir, report, seed_dir=nonexistent_seed)
        assert result is False
        assert report.auto_heal_attempted
        assert not report.auto_heal_succeeded


# ── write_telemetry_boot_config ──────────────────────────────


class TestWriteTelemetryBootConfig:
    def test_writes_valid_config(self, tmp_dir):
        output = os.path.join(tmp_dir, "telemetry_boot_config.json")
        report = ValidationReport("test.json")
        result = write_telemetry_boot_config(output, report)
        assert result is True
        assert report.telemetry_written
        assert os.path.exists(output)

        with open(output, "r") as f:
            config = json.load(f)
        assert config["config_version"] == "1.0.0"
        assert "generated_at" in config
        assert "vram_ceilings" in config

    def test_creates_parent_directories(self, tmp_dir):
        output = os.path.join(tmp_dir, "deeply", "nested", "telemetry_boot_config.json")
        report = ValidationReport("test.json")
        result = write_telemetry_boot_config(output, report)
        assert result is True
        assert os.path.exists(output)


# ── run_validation (full pipeline) ───────────────────────────


class TestRunValidation:
    def test_valid_manifest_passes(self, valid_manifest_path, design_guides_dir):
        report = run_validation(
            manifest_path=valid_manifest_path,
            design_guides_dir=design_guides_dir,
        )
        assert report.valid
        assert len(report.errors) == 0

    def test_missing_manifest_fails(self, tmp_dir):
        missing_path = os.path.join(tmp_dir, "nonexistent.json")
        report = run_validation(
            manifest_path=missing_path,
            design_guides_dir=tmp_dir,
        )
        assert not report.valid

    def test_auto_heal_recovers_missing_manifest(self, tmp_dir, seed_dir):
        manifest_path = os.path.join(tmp_dir, "anchor_manifest.json")
        design_dir = os.path.join(tmp_dir, "design_guides")
        os.makedirs(design_dir, exist_ok=True)
        # Create all pre-req ledgers so the full validation passes
        for ledger in PRE_REQ_LEDGERS:
            with open(os.path.join(design_dir, ledger), "w") as f:
                json.dump({"ledger_name": ledger, "phase_id": 1, "schema_version": "1.0.0", "assets": []}, f)
        # Manifest does NOT exist yet

        report = run_validation(
            manifest_path=manifest_path,
            design_guides_dir=design_dir,
            auto_heal=True,
            seed_dir=seed_dir,
        )
        assert report.valid
        assert report.auto_heal_succeeded
        assert os.path.exists(manifest_path)

    def test_corrupt_manifest_fails(self, tmp_dir, design_guides_dir):
        corrupt_path = os.path.join(tmp_dir, "corrupt.json")
        with open(corrupt_path, "w") as f:
            f.write("this is not json")
        report = run_validation(
            manifest_path=corrupt_path,
            design_guides_dir=design_guides_dir,
        )
        assert not report.valid

    def test_telemetry_written_on_valid(self, valid_manifest_path, design_guides_dir, tmp_dir):
        telemetry_path = os.path.join(tmp_dir, "telemetry_boot_config.json")
        report = run_validation(
            manifest_path=valid_manifest_path,
            design_guides_dir=design_guides_dir,
            write_telemetry=True,
            telemetry_output=telemetry_path,
        )
        assert report.valid
        assert report.telemetry_written
        assert os.path.exists(telemetry_path)

    def test_telemetry_skipped_on_invalid(self, tmp_dir):
        missing_path = os.path.join(tmp_dir, "nonexistent.json")
        telemetry_path = os.path.join(tmp_dir, "should_not_exist.json")
        report = run_validation(
            manifest_path=missing_path,
            design_guides_dir=tmp_dir,
            write_telemetry=True,
            telemetry_output=telemetry_path,
        )
        assert not report.valid
        assert not report.telemetry_written


# ── CLI ──────────────────────────────────────────────────────


class TestCLI:
    def test_validate_flag_works(self, valid_manifest_path, design_guides_dir):
        exit_code = main([
            "--validate",
            "--manifest", valid_manifest_path,
            "--design-guides-dir", design_guides_dir,
        ])
        assert exit_code == 0

    def test_validate_fails_on_invalid(self, tmp_dir, design_guides_dir):
        corrupt_path = os.path.join(tmp_dir, "corrupt.json")
        with open(corrupt_path, "w") as f:
            f.write("{bad")
        exit_code = main([
            "--validate",
            "--manifest", corrupt_path,
            "--design-guides-dir", design_guides_dir,
        ])
        assert exit_code == 1

    def test_json_output(self, valid_manifest_path, design_guides_dir):
        exit_code = main([
            "--validate",
            "--manifest", valid_manifest_path,
            "--design-guides-dir", design_guides_dir,
            "--json",
        ])
        assert exit_code == 0

    def test_no_args_shows_help(self):
        exit_code = main([])
        assert exit_code == 1  # prints help, exits 1

    def test_auto_heal_flag(self, tmp_dir, seed_dir):
        manifest_path = os.path.join(tmp_dir, "anchor_manifest.json")
        design_dir = os.path.join(tmp_dir, "design_guides")
        os.makedirs(design_dir, exist_ok=True)
        # Create all pre-req ledgers so validation passes after heal
        for ledger in PRE_REQ_LEDGERS:
            with open(os.path.join(design_dir, ledger), "w") as f:
                json.dump({"ledger_name": ledger, "phase_id": 1, "schema_version": "1.0.0", "assets": []}, f)
        # Don't create manifest — auto-heal should create it

        exit_code = main([
            "--validate",
            "--manifest", manifest_path,
            "--design-guides-dir", design_dir,
            "--auto-heal",
        ])
        assert exit_code == 0
        assert os.path.exists(manifest_path)

    def test_pre_push_check_passes(self, valid_manifest_path, design_guides_dir):
        exit_code = main([
            "--pre-push-check",
            "--manifest", valid_manifest_path,
            "--design-guides-dir", design_guides_dir,
        ])
        assert exit_code == 0

    def test_pre_push_check_fails(self, tmp_dir, design_guides_dir):
        corrupt_path = os.path.join(tmp_dir, "corrupt.json")
        with open(corrupt_path, "w") as f:
            f.write("{bad")
        exit_code = main([
            "--pre-push-check",
            "--manifest", corrupt_path,
            "--design-guides-dir", design_guides_dir,
        ])
        assert exit_code == 1

    def test_write_telemetry_flag(self, valid_manifest_path, design_guides_dir, tmp_dir):
        telemetry_path = os.path.join(tmp_dir, "telemetry_boot_config.json")
        exit_code = main([
            "--validate",
            "--manifest", valid_manifest_path,
            "--design-guides-dir", design_guides_dir,
            "--write-telemetry",
            "--telemetry-output", telemetry_path,
        ])
        assert exit_code == 0
        assert os.path.exists(telemetry_path)


# ── format_report ────────────────────────────────────────────


class TestFormatReport:
    def test_valid_report(self):
        report = ValidationReport("test.json", valid=True)
        report.checked_at = "2026-06-14T00:00:00Z"
        output = format_report(report)
        assert "VALID" in output
        assert "test.json" in output

    def test_invalid_report(self):
        report = ValidationReport("test.json", valid=False)
        report.add_error("test.json", "something went wrong")
        report.checked_at = "2026-06-14T00:00:00Z"
        output = format_report(report)
        assert "INVALID" in output
        assert "something went wrong" in output


# ── seed_verifier ────────────────────────────────────────────


class TestSeedVerifier:
    def test_valid_manifest_passes(self, valid_manifest_path, design_guides_dir):
        report = seed_verify_manifest(valid_manifest_path, design_guides_dir)
        assert report.valid

    def test_missing_manifest_fails(self, tmp_dir):
        missing_path = os.path.join(tmp_dir, "nonexistent.json")
        report = seed_verify_manifest(missing_path)
        assert not report.valid

    def test_corrupt_manifest_fails(self, tmp_dir):
        corrupt_path = os.path.join(tmp_dir, "corrupt.json")
        with open(corrupt_path, "w") as f:
            f.write("{bad")
        report = seed_verify_manifest(corrupt_path)
        assert not report.valid


# ── Edge Cases ───────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_assets_array(self):
        manifest = {
            "ledger_name": "test",
            "phase_id": 3,
            "schema_version": "1.0.0",
            "assets": [],
        }
        report = ValidationReport("test.json")
        validate_manifest_schema(manifest, report)
        assert report.valid

    def test_multiple_valid_assets(self):
        asset_template = {
            "asset_id": "prism://test/item",
            "type": "mesh",
            "storage_uri": "prism://test",
            "sha256_hash": "a" * 64,
            "size_bytes": 100,
            "replayability": {"model_identifier": "test", "seed": 1},
        }
        manifest = {
            "ledger_name": "test",
            "phase_id": 3,
            "schema_version": "1.0.0",
            "assets": [asset_template] * 10,
        }
        report = ValidationReport("test.json")
        validate_manifest_schema(manifest, report)
        assert report.valid

    def test_boolean_field(self):
        manifest = {
            "ledger_name": "test",
            "phase_id": 3,
            "schema_version": "1.0.0",
            "assets": [],
            "extra_bool": True,
        }
        report = ValidationReport("test.json")
        validate_manifest_schema(manifest, report)
        assert report.valid  # unknown fields are warnings only

    def test_nested_object_validation(self):
        manifest = {
            "ledger_name": "test",
            "phase_id": 3,
            "schema_version": "1.0.0",
            "assets": [
                {
                    "asset_id": "prism://test",
                    "type": "mesh",
                    "storage_uri": "prism://test",
                    "sha256_hash": "a" * 64,
                    "size_bytes": 100,
                    "replayability": {
                        "model_identifier": "test",
                        "seed": "not_an_integer",  # should fail
                    },
                }
            ],
        }
        report = ValidationReport("test.json")
        validate_manifest_schema(manifest, report)
        assert not report.valid

    def test_negative_size_bytes(self):
        manifest = {
            "ledger_name": "test",
            "phase_id": 3,
            "schema_version": "1.0.0",
            "assets": [
                {
                    "asset_id": "prism://test",
                    "type": "mesh",
                    "storage_uri": "prism://test",
                    "sha256_hash": "a" * 64,
                    "size_bytes": -1,
                    "replayability": {"model_identifier": "test", "seed": 1},
                }
            ],
        }
        report = ValidationReport("test.json")
        validate_manifest_schema(manifest, report)
        assert not report.valid
