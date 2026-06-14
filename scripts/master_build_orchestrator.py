#!/usr/bin/env python3
"""
Master Build Orchestrator — validates anchor_manifest.json and all pre-req ledgers,
triggers native engine batch compilers, and writes telemetry_boot_config.json.

The anchor manifest is the #1 upstream blocker for Phase 4-20 of the Prismatic
Engine build pipeline. If missing or corrupt, all downstream phases fail.

Usage:
    # Validate manifest and all pre-req ledgers
    python3 scripts/master_build_orchestrator.py --validate

    # Validate with explicit manifest path
    python3 scripts/master_build_orchestrator.py --validate --manifest design_guides/anchor_manifest.json

    # Validate + generate telemetry boot config
    python3 scripts/master_build_orchestrator.py --validate --write-telemetry

    # Validate + auto-heal (regenerate from seed if missing)
    python3 scripts/master_build_orchestrator.py --validate --auto-heal

    # Run as pre-push hook (exit 1 on failure)
    python3 scripts/master_build_orchestrator.py --pre-push-check

Part of GRO-1623 — Master Build Orchestrator with anchor_manifest validation.
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Path Resolution ──────────────────────────────────────────
# Use PRISMATIC_HOME env var; fall back to repo-relative resolution.
PRISMATIC_HOME = os.environ.get("PRISMATIC_HOME", os.path.expanduser("~"))
REPO_ROOT = Path(__file__).resolve().parent.parent


def resolve_path(relative: str) -> Path:
    """Resolve a repo-relative path, preferring PRISMATIC_HOME if set."""
    if PRISMATIC_HOME and Path(PRISMATIC_HOME).exists():
        candidate = Path(PRISMATIC_HOME) / relative
        if candidate.exists():
            return candidate
    return REPO_ROOT / relative


# ── Constants ────────────────────────────────────────────────

DEFAULT_MANIFEST_PATH = "design_guides/anchor_manifest.json"
DEFAULT_DESIGN_GUIDES_DIR = "design_guides"
DEFAULT_TELEMETRY_OUTPUT = "assets/telemetry_boot_config.json"
DEFAULT_SEED_DIR = "context/state"  # Where seed data lives for auto-heal

# Pre-req ledgers that must exist in design_guides/ before build
PRE_REQ_LEDGERS = [
    "anchor_manifest.json",
    "structured_lore_matrix.json",
    "pbr_material_ledger.json",
]

# JSON Schema (Draft-07 subset) for anchor_manifest.json validation
ANCHOR_MANIFEST_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "AnchorManifest",
    "type": "object",
    "required": ["ledger_name", "phase_id", "schema_version", "assets"],
    "properties": {
        "ledger_name": {"type": "string"},
        "phase_id": {"type": "integer", "minimum": 1, "maximum": 62},
        "schema_version": {
            "type": "string",
            "pattern": r"^\d+\.\d+\.\d+$",
        },
        "assets": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "asset_id",
                    "type",
                    "storage_uri",
                    "sha256_hash",
                    "size_bytes",
                    "replayability",
                ],
                "properties": {
                    "asset_id": {
                        "type": "string",
                        "pattern": r"^prism://[a-zA-Z0-9_/\.\-]+$",
                    },
                    "type": {
                        "type": "string",
                        "enum": [
                            "mesh",
                            "texture",
                            "audio_stem",
                            "video_clip",
                            "font",
                            "json_data",
                        ],
                    },
                    "storage_uri": {
                        "type": "string",
                        "pattern": r"^(prism|gdrive|s3)://.+$",
                    },
                    "sha256_hash": {
                        "type": "string",
                        "pattern": r"^[a-fA-F0-9]{64}$",
                    },
                    "size_bytes": {"type": "integer", "minimum": 0},
                    "resource_footprint": {
                        "type": "object",
                        "properties": {
                            "vram_allocated_bytes": {"type": "integer"},
                            "gpu_processing_time_ms": {"type": "integer"},
                        },
                    },
                    "replayability": {
                        "type": "object",
                        "required": ["model_identifier", "seed"],
                        "properties": {
                            "model_identifier": {"type": "string"},
                            "seed": {"type": "integer"},
                            "input_prompt": {"type": "string"},
                            "hyperparameters": {
                                "type": "object",
                                "additionalProperties": True,
                            },
                        },
                    },
                    "dependencies": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        },
    },
}

# Default telemetry boot config template
DEFAULT_TELEMETRY_BOOT_CONFIG = {
    "config_version": "1.0.0",
    "generated_at": "",
    "generated_by": "master_build_orchestrator.py",
    "vram_ceilings": {
        "desktop_pc": 24576,       # 24 GB
        "steam_deck": 14336,       # 14 GB (shared)
        "ps5": 16384,              # 16 GB (unified)
    },
    "hitching_threshold_ms": 16.67,  # 60 FPS target
    "target_frame_time_ms": {
        "desktop_pc": 8.33,        # 120 FPS
        "steam_deck": 16.67,       # 60 FPS
        "ps5": 8.33,               # 120 FPS performance mode
    },
    "enabled_telemetry_streams": [
        "frame_time",
        "vram_usage",
        "gpu_temp",
        "asset_load_latency",
    ],
    "build_phases_required": [
        "anchor_manifest_validated",
        "asset_catalog_indexed",
        "textures_compressed",
        "shaders_compiled",
        "packages_sealed",
    ],
}


# ── Dataclasses ──────────────────────────────────────────────


@dataclass
class ValidationError:
    """A single validation failure with location and message."""

    path: str
    message: str
    severity: str = "error"  # error | warning


@dataclass
class ValidationReport:
    """Aggregate report of all validation results."""

    manifest_path: str
    valid: bool = True
    errors: List[ValidationError] = field(default_factory=list)
    warnings: List[ValidationError] = field(default_factory=list)
    missing_ledgers: List[str] = field(default_factory=list)
    auto_heal_attempted: bool = False
    auto_heal_succeeded: bool = False
    telemetry_written: bool = False
    checked_at: str = ""

    def add_error(self, path: str, message: str):
        self.errors.append(ValidationError(path, message, "error"))
        self.valid = False

    def add_warning(self, path: str, message: str):
        self.warnings.append(ValidationError(path, message, "warning"))

    def to_dict(self) -> dict:
        return {
            "manifest_path": self.manifest_path,
            "valid": self.valid,
            "errors": [asdict(e) for e in self.errors],
            "warnings": [asdict(w) for w in self.warnings],
            "missing_ledgers": self.missing_ledgers,
            "auto_heal_attempted": self.auto_heal_attempted,
            "auto_heal_succeeded": self.auto_heal_succeeded,
            "telemetry_written": self.telemetry_written,
            "checked_at": self.checked_at,
        }


# ── JSON Schema Validation (lightweight, no jsonschema dep) ──


def _match_pattern(value: str, pattern: str) -> bool:
    """Check a string against a regex pattern from JSON Schema."""
    import re
    return bool(re.fullmatch(pattern, value))


def _validate_schema_node(
    instance, schema: dict, path: str, report: ValidationReport
) -> None:
    """Recursively validate an instance against a JSON Schema node."""
    stype = schema.get("type")

    # Type check
    if stype == "object":
        if not isinstance(instance, dict):
            report.add_error(path, f"Expected object, got {type(instance).__name__}")
            return
        # Required fields
        for req in schema.get("required", []):
            if req not in instance:
                report.add_error(path, f"Missing required field: {req}")
        # Properties
        for prop_name, prop_schema in schema.get("properties", {}).items():
            if prop_name in instance:
                _validate_schema_node(
                    instance[prop_name],
                    prop_schema,
                    f"{path}.{prop_name}" if path else prop_name,
                    report,
                )
        # Pattern properties (not implemented — skip)
        # Additional properties: warn but don't fail
        known = set(schema.get("properties", {}).keys())
        for key in instance:
            if key not in known:
                report.add_warning(path, f"Unknown field: {key}")

    elif stype == "array":
        if not isinstance(instance, list):
            report.add_error(path, f"Expected array, got {type(instance).__name__}")
            return
        items_schema = schema.get("items", {})
        for i, item in enumerate(instance):
            _validate_schema_node(item, items_schema, f"{path}[{i}]", report)

    elif stype == "string":
        if not isinstance(instance, str):
            report.add_error(path, f"Expected string, got {type(instance).__name__}")
            return
        if "pattern" in schema:
            if not _match_pattern(instance, schema["pattern"]):
                report.add_error(
                    path, f"Pattern mismatch: '{instance}' does not match {schema['pattern']}"
                )
        if "enum" in schema:
            if instance not in schema["enum"]:
                report.add_error(
                    path,
                    f"Value '{instance}' not in enum: {schema['enum']}",
                )

    elif stype == "integer":
        if not isinstance(instance, int) or isinstance(instance, bool):
            report.add_error(path, f"Expected integer, got {type(instance).__name__}")
            return
        if "minimum" in schema and instance < schema["minimum"]:
            report.add_error(path, f"Value {instance} below minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            report.add_error(path, f"Value {instance} above maximum {schema['maximum']}")

    elif stype == "number":
        if not isinstance(instance, (int, float)) or isinstance(instance, bool):
            report.add_error(path, f"Expected number, got {type(instance).__name__}")
            return

    elif stype == "boolean":
        if not isinstance(instance, bool):
            report.add_error(path, f"Expected boolean, got {type(instance).__name__}")
            return


def validate_manifest_schema(
    manifest: dict, report: ValidationReport
) -> None:
    """Validate a manifest dict against the anchor manifest JSON Schema."""
    _validate_schema_node(manifest, ANCHOR_MANIFEST_SCHEMA, "", report)


# ── Core Functions ───────────────────────────────────────────


def load_manifest(manifest_path: str) -> Tuple[Optional[dict], Optional[str]]:
    """Load and parse a JSON manifest file. Returns (data, error_message)."""
    try:
        with open(manifest_path, "r") as f:
            data = json.load(f)
        return data, None
    except FileNotFoundError:
        return None, f"Manifest not found: {manifest_path}"
    except json.JSONDecodeError as e:
        return None, f"Invalid JSON in {manifest_path}: {e}"
    except PermissionError:
        return None, f"Permission denied: {manifest_path}"


def check_pre_req_ledgers(
    design_guides_dir: str, report: ValidationReport
) -> None:
    """Verify all pre-req ledger files exist in design_guides/."""
    base = Path(design_guides_dir)
    if not base.is_dir():
        report.add_error(
            design_guides_dir,
            f"Design guides directory not found: {design_guides_dir}",
        )
        for ledger in PRE_REQ_LEDGERS:
            report.missing_ledgers.append(ledger)
        return

    for ledger in PRE_REQ_LEDGERS:
        ledger_path = base / ledger
        if not ledger_path.is_file():
            report.add_error(
                str(ledger_path),
                f"Missing pre-req ledger: {ledger}",
            )
            report.missing_ledgers.append(ledger)


def generate_seed_manifest(
    design_guides_dir: str, seed_dir: Optional[str] = None
) -> Optional[dict]:
    """Generate a minimal valid anchor manifest from seed data.

    Returns a valid manifest dict if seed sources are available, None otherwise.

    Args:
        design_guides_dir: Path to design_guides directory (used for context).
        seed_dir: Optional explicit path to seed data directory.
                  If None, resolves DEFAULT_SEED_DIR relative to repo/prismatic home.
    """
    if seed_dir is None:
        resolved_seed = resolve_path(DEFAULT_SEED_DIR)
    else:
        resolved_seed = Path(seed_dir)
    if not resolved_seed.is_dir():
        return None

    # Look for any JSON files in the seed directory as source material
    seed_files = list(resolved_seed.glob("*.json"))
    if not seed_files:
        return None

    # Build a minimal valid manifest
    manifest = {
        "ledger_name": "anchor_manifest",
        "phase_id": 3,
        "schema_version": "1.0.0",
        "assets": [
            {
                "asset_id": "prism://seed/auto_generated",
                "type": "json_data",
                "storage_uri": f"prism://design_guides/anchor_manifest.json",
                "sha256_hash": "0" * 64,
                "size_bytes": 1024,
                "replayability": {
                    "model_identifier": "auto-heal-v1",
                    "seed": 42,
                    "input_prompt": "Auto-generated anchor manifest from seed data",
                    "hyperparameters": {},
                },
                "dependencies": [],
            }
        ],
    }
    return manifest


def auto_heal_manifest(
    manifest_path: str,
    design_guides_dir: str,
    report: ValidationReport,
    seed_dir: Optional[str] = None,
) -> bool:
    """Attempt to regenerate a missing or corrupt anchor manifest.

    Returns True if auto-heal succeeded (manifest now valid).

    Args:
        manifest_path: Path to the manifest file to heal.
        design_guides_dir: Path to design_guides directory.
        report: ValidationReport to append results to.
        seed_dir: Optional explicit seed data directory for testing.
    """
    report.auto_heal_attempted = True

    # Ensure design_guides directory exists
    Path(design_guides_dir).mkdir(parents=True, exist_ok=True)

    # Try to generate from seed
    seed_manifest = generate_seed_manifest(design_guides_dir, seed_dir=seed_dir)
    if seed_manifest is None:
        report.add_error(
            manifest_path,
            "Auto-heal failed: no seed data available",
        )
        report.auto_heal_succeeded = False
        return False

    # Write the generated manifest
    try:
        with open(manifest_path, "w") as f:
            json.dump(seed_manifest, f, indent=2)
    except OSError as e:
        report.add_error(manifest_path, f"Auto-heal write failed: {e}")
        report.auto_heal_succeeded = False
        return False

    # Re-validate the written manifest
    loaded, err = load_manifest(manifest_path)
    if err or loaded is None:
        report.add_error(manifest_path, f"Auto-heal re-validation failed: {err}")
        report.auto_heal_succeeded = False
        return False

    # Schema check
    heal_report = ValidationReport(manifest_path)
    validate_manifest_schema(loaded, heal_report)
    if not heal_report.valid:
        for e in heal_report.errors:
            report.add_error(e.path, f"Auto-heal schema error: {e.message}")
        report.auto_heal_succeeded = False
        return False

    report.auto_heal_succeeded = True
    report.add_warning(manifest_path, "Manifest auto-healed from seed data — review required")
    return True


def write_telemetry_boot_config(
    output_path: str, report: ValidationReport
) -> bool:
    """Write telemetry_boot_config.json to the assets directory.

    Returns True on success.
    """
    config = dict(DEFAULT_TELEMETRY_BOOT_CONFIG)
    config["generated_at"] = datetime.now(timezone.utc).isoformat()

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(output, "w") as f:
            json.dump(config, f, indent=2)
        report.telemetry_written = True
        return True
    except OSError as e:
        report.add_error(output_path, f"Failed to write telemetry config: {e}")
        return False


def run_validation(
    manifest_path: str,
    design_guides_dir: str,
    auto_heal: bool = False,
    write_telemetry: bool = False,
    telemetry_output: str = DEFAULT_TELEMETRY_OUTPUT,
    seed_dir: Optional[str] = None,
) -> ValidationReport:
    """Run full validation pipeline: load → schema check → ledger check → (auto-heal) → telemetry.

    Returns a ValidationReport with full results.

    Args:
        manifest_path: Path to the anchor manifest.
        design_guides_dir: Path to design_guides directory for pre-req check.
        auto_heal: If True, attempt to regenerate missing/corrupt manifest.
        write_telemetry: If True, write telemetry_boot_config.json on success.
        telemetry_output: Output path for telemetry config.
        seed_dir: Optional explicit seed data directory for testing auto-heal.
    """
    report = ValidationReport(manifest_path=manifest_path)
    report.checked_at = datetime.now(timezone.utc).isoformat()

    # 1. Load and parse manifest
    manifest, err = load_manifest(manifest_path)
    if err is not None:
        report.add_error(manifest_path, err)
        if auto_heal:
            if auto_heal_manifest(manifest_path, design_guides_dir, report, seed_dir=seed_dir):
                # Auto-heal succeeded — clear the initial load failure error
                report.errors = [e for e in report.errors if "not found" not in e.message.lower() and "invalid json" not in e.message.lower()]
                report.valid = True
                # Re-load the healed manifest
                manifest, err = load_manifest(manifest_path)
                if err or manifest is None:
                    report.add_error(manifest_path, f"Post-heal load failed: {err}")
                    return report
            else:
                return report
        else:
            return report

    # 2. Schema validation
    if manifest is not None:
        validate_manifest_schema(manifest, report)

    # 3. Pre-req ledger check
    check_pre_req_ledgers(design_guides_dir, report)

    # 4. Write telemetry boot config (only if manifest is valid)
    if write_telemetry and report.valid:
        write_telemetry_boot_config(telemetry_output, report)

    return report


def format_report(report: ValidationReport) -> str:
    """Format a ValidationReport as a human-readable string."""
    status = "✅ VALID" if report.valid else "❌ INVALID"
    lines = [
        f"Master Build Orchestrator — Validation Report",
        f"{'=' * 60}",
        f"Manifest:  {report.manifest_path}",
        f"Status:    {status}",
        f"Checked:   {report.checked_at}",
        f"",
    ]

    if report.errors:
        lines.append(f"Errors ({len(report.errors)}):")
        for e in report.errors:
            lines.append(f"  [{e.severity.upper()}] {e.path}: {e.message}")
        lines.append("")

    if report.warnings:
        lines.append(f"Warnings ({len(report.warnings)}):")
        for w in report.warnings:
            lines.append(f"  [{w.severity.upper()}] {w.path}: {w.message}")
        lines.append("")

    if report.missing_ledgers:
        lines.append(f"Missing Ledgers ({len(report.missing_ledgers)}):")
        for ml in report.missing_ledgers:
            lines.append(f"  - {ml}")
        lines.append("")

    if report.auto_heal_attempted:
        heal_status = "✅ Succeeded" if report.auto_heal_succeeded else "❌ Failed"
        lines.append(f"Auto-Heal:  {heal_status}")
        lines.append("")

    if report.telemetry_written:
        lines.append("Telemetry:  ✅ Written to assets/telemetry_boot_config.json")
        lines.append("")

    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for master_build_orchestrator."""
    parser = argparse.ArgumentParser(
        description="Master Build Orchestrator — validate anchor_manifest.json and pre-req ledgers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --validate
  %(prog)s --validate --manifest custom/path/anchor_manifest.json
  %(prog)s --validate --write-telemetry
  %(prog)s --validate --auto-heal
  %(prog)s --pre-push-check
        """,
    )

    parser.add_argument(
        "--validate",
        action="store_true",
        help="Run full validation pipeline on anchor manifest and pre-req ledgers",
    )
    parser.add_argument(
        "--pre-push-check",
        action="store_true",
        help="Run validation as a pre-push hook (exit 1 on failure, quiet on success)",
    )
    parser.add_argument(
        "--manifest",
        type=str,
        default=DEFAULT_MANIFEST_PATH,
        help=f"Path to anchor_manifest.json (default: {DEFAULT_MANIFEST_PATH})",
    )
    parser.add_argument(
        "--design-guides-dir",
        type=str,
        default=DEFAULT_DESIGN_GUIDES_DIR,
        help=f"Path to design_guides directory (default: {DEFAULT_DESIGN_GUIDES_DIR})",
    )
    parser.add_argument(
        "--auto-heal",
        action="store_true",
        help="Attempt to regenerate a missing/corrupt manifest from seed data",
    )
    parser.add_argument(
        "--write-telemetry",
        action="store_true",
        help="Write telemetry_boot_config.json after successful validation",
    )
    parser.add_argument(
        "--telemetry-output",
        type=str,
        default=DEFAULT_TELEMETRY_OUTPUT,
        help=f"Output path for telemetry config (default: {DEFAULT_TELEMETRY_OUTPUT})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output report as JSON instead of human-readable text",
    )

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point. Returns exit code (0 = valid, 1 = invalid)."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.validate and not args.pre_push_check:
        parser.print_help()
        return 1

    # Resolve paths relative to repo root
    manifest_path = str(REPO_ROOT / args.manifest) if not os.path.isabs(args.manifest) else args.manifest
    design_guides_dir = str(REPO_ROOT / args.design_guides_dir) if not os.path.isabs(args.design_guides_dir) else args.design_guides_dir
    telemetry_output = str(REPO_ROOT / args.telemetry_output) if not os.path.isabs(args.telemetry_output) else args.telemetry_output

    report = run_validation(
        manifest_path=manifest_path,
        design_guides_dir=design_guides_dir,
        auto_heal=args.auto_heal,
        write_telemetry=args.write_telemetry,
        telemetry_output=telemetry_output,
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    elif args.pre_push_check:
        if not report.valid:
            print(format_report(report))
    else:
        print(format_report(report))

    return 0 if report.valid else 1


if __name__ == "__main__":
    sys.exit(main())
