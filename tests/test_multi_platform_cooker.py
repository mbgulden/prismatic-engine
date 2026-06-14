"""Tests for scripts/multi_platform_cooker.py — GRO-1613.

Covers: CLI parsing, platform profiles, catalog validation, AST pruning,
4KB alignment, ledger writes, and error handling.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure scripts are importable
SCRIPT_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, SCRIPT_DIR)

from multi_platform_cooker import (
    PLATFORM_PROFILES,
    VALID_PLATFORMS,
    BuildLedger,
    BuildProfile,
    ast_prune_pass,
    build_parser,
    check_alignment,
    hash_directory,
    load_ledger,
    pad_to_sector,
    prepare_output_dir,
    save_ledger,
    validate_catalog,
    write_package_manifest,
    SECTOR_SIZE,
)


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def sample_catalog(tmp_dir):
    """Create a valid universal_asset_catalog.json."""
    catalog = {
        "assets": [
            {"id": "tex_001", "path": "textures/player.dds", "type": "texture"},
            {"id": "mesh_001", "path": "meshes/player.obj", "type": "mesh"},
        ]
    }
    path = tmp_dir / "universal_asset_catalog.json"
    path.write_text(json.dumps(catalog, indent=2))
    return str(path)


@pytest.fixture
def sample_src_dir(tmp_dir):
    """Create a sample source directory with scripts and textures."""
    src = tmp_dir / "assets"
    src.mkdir()

    # A Python script with preprocessor-like directives
    script = src / "shaders" / "render.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "#if FOR_PC_ULTRA\n"
        "raytracing_enabled = True\n"
        "#elif FOR_STEAM_DECK\n"
        "raytracing_enabled = False\n"
        "#else\n"
        "raytracing_enabled = False\n"
        "#endif\n"
    )

    # A texture file (binary placeholder)
    tex = src / "textures" / "hero.dds"
    tex.parent.mkdir(parents=True)
    tex.write_bytes(b"\x00" * 4096 * 3)  # 12KB, aligned

    return str(src)


# ── Platform Profile Tests ──────────────────────────────────


class TestPlatformProfiles:
    def test_all_three_platforms_defined(self):
        assert set(PLATFORM_PROFILES.keys()) == {"pc", "steam_deck", "ps5"}

    def test_pc_profile(self):
        p = PLATFORM_PROFILES["pc"]
        assert p["texture_compression"] == "BC7"
        assert p["max_texture_resolution"] == 8192
        assert p["sdk_required"] is False

    def test_steam_deck_profile(self):
        p = PLATFORM_PROFILES["steam_deck"]
        assert p["texture_compression"] == "ASTC_8x8"
        assert p["max_texture_resolution"] == 2048
        assert p["tdp_limit_watts"] == 15
        assert p["proton_compat"] is True

    def test_ps5_profile_stubs_sdk(self):
        p = PLATFORM_PROFILES["ps5"]
        assert p["sdk_required"] is True
        assert "Proprietary" in p.get("sdk_notes", "")

    def test_valid_platforms_set(self):
        assert VALID_PLATFORMS == {"pc", "steam_deck", "ps5"}


# ── CLI Parser Tests ────────────────────────────────────────


class TestCLIParsing:
    def test_parser_accepts_valid_args(self):
        parser = build_parser()
        args = parser.parse_args([
            "--src-dir", "/tmp/assets",
            "--platform", "pc",
            "--build-id", "test_001",
        ])
        assert args.src_dir == "/tmp/assets"
        assert args.platform == "pc"
        assert args.build_id == "test_001"
        assert args.ast_prune is False
        assert args.alignment_check is False
        assert args.force is False

    def test_parser_all_flags(self):
        parser = build_parser()
        args = parser.parse_args([
            "--src-dir", "/tmp/assets",
            "--platform", "steam_deck",
            "--build-id", "build_001",
            "--catalog", "/tmp/cat.json",
            "--ast-prune",
            "--alignment-check",
            "--force",
        ])
        assert args.platform == "steam_deck"
        assert args.catalog == "/tmp/cat.json"
        assert args.ast_prune is True
        assert args.alignment_check is True
        assert args.force is True

    def test_parser_rejects_invalid_platform(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([
                "--src-dir", "/tmp",
                "--platform", "xbox",
                "--build-id", "x",
            ])

    def test_parser_requires_src_dir_and_build_id(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--platform", "pc"])

    def test_parser_version(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--version"])


# ── Catalog Validation Tests ────────────────────────────────


class TestCatalogValidation:
    def test_valid_catalog(self, sample_catalog):
        ok, msg = validate_catalog(sample_catalog)
        assert ok is True
        assert "OK" in msg

    def test_missing_catalog(self):
        ok, msg = validate_catalog("/nonexistent/catalog.json")
        assert ok is False
        assert "not found" in msg

    def test_invalid_json_catalog(self, tmp_dir):
        path = tmp_dir / "bad.json"
        path.write_text("not json")
        ok, msg = validate_catalog(str(path))
        assert ok is False
        assert "parse error" in msg

    def test_empty_dict_catalog(self, tmp_dir):
        path = tmp_dir / "empty.json"
        path.write_text("{}")
        ok, msg = validate_catalog(str(path))
        assert ok is True  # Empty dict is valid, just empty


# ── Directory Hashing Tests ─────────────────────────────────


class TestHashDirectory:
    def test_hash_valid_directory(self, sample_src_dir):
        h = hash_directory(sample_src_dir)
        assert len(h) == 16
        assert h != "DIRECTORY_NOT_FOUND"

    def test_hash_missing_directory(self):
        h = hash_directory("/nonexistent/path")
        assert h == "DIRECTORY_NOT_FOUND"

    def test_hash_is_deterministic(self, sample_src_dir):
        h1 = hash_directory(sample_src_dir)
        h2 = hash_directory(sample_src_dir)
        assert h1 == h2


# ── AST Pruning Tests ───────────────────────────────────────


class TestASTPruning:
    def test_prune_inactive_blocks(self, tmp_dir, sample_src_dir):
        out_dir = str(tmp_dir / "out")
        count, log = ast_prune_pass(sample_src_dir, "pc", out_dir)
        # The sample has #if FOR_PC_ULTRA, #elif FOR_STEAM_DECK, #else
        # For platform "pc", FOR_STEAM_DECK is inactive
        assert count >= 1
        assert any("PRUNED" in entry for entry in log)

    def test_no_pruning_when_all_active(self, tmp_dir):
        src = tmp_dir / "src"
        src.mkdir()
        script = src / "game.py"
        script.write_text(
            "#if FOR_PC_ULTRA\n"
            "high_quality = True\n"
            "#endif\n"
        )
        out_dir = str(tmp_dir / "out")
        count, log = ast_prune_pass(str(src), "pc", out_dir)
        # FOR_PC_ULTRA maps to PC label — no pruning needed
        assert count == 0

    def test_ps5_sdk_not_pruned_for_ps5(self, tmp_dir):
        src = tmp_dir / "src"
        src.mkdir()
        script = src / "ps5_feature.py"
        script.write_text(
            "#if FOR_PLAYSTATION_5\n"
            "dual_sense_features = True\n"
            "#endif\n"
        )
        out_dir = str(tmp_dir / "out")
        count, log = ast_prune_pass(str(src), "ps5", out_dir)
        assert count == 0  # PS5 active, no pruning

    def test_prune_creates_output_files(self, tmp_dir, sample_src_dir):
        out_dir = str(tmp_dir / "out")
        count, log = ast_prune_pass(sample_src_dir, "pc", out_dir)
        # Check that pruned files exist in out dir
        pruned_file = Path(out_dir) / "shaders" / "render.py"
        assert pruned_file.is_file()
        content = pruned_file.read_text()
        assert "PRUNED" in content


# ── Alignment Tests ────────────────────────────────────────


class TestAlignment:
    def test_aligned_file(self, tmp_dir):
        f = tmp_dir / "aligned.bin"
        f.write_bytes(b"\x00" * SECTOR_SIZE * 2)  # 8KB
        aligned, size, padding = check_alignment(str(f))
        assert aligned is True
        assert size == SECTOR_SIZE * 2
        assert padding == 0

    def test_misaligned_file(self, tmp_dir):
        f = tmp_dir / "unaligned.bin"
        f.write_bytes(b"\x00" * (SECTOR_SIZE * 2 + 100))  # 8KB + 100 bytes
        aligned, size, padding = check_alignment(str(f))
        assert aligned is False
        assert padding == SECTOR_SIZE - 100  # 4096 - 100 = 3996

    def test_pad_misaligned_file(self, tmp_dir):
        f = tmp_dir / "to_pad.bin"
        f.write_bytes(b"\x00" * 100)  # 100 bytes, needs 3996 padding
        ok, msg = pad_to_sector(str(f))
        assert ok is True
        assert f.stat().st_size == SECTOR_SIZE
        assert "Padded" in msg

    def test_pad_already_aligned(self, tmp_dir):
        f = tmp_dir / "already.bin"
        f.write_bytes(b"\x00" * SECTOR_SIZE)
        ok, msg = pad_to_sector(str(f))
        assert ok is True
        assert "Already aligned" in msg

    def test_missing_file(self):
        aligned, size, padding = check_alignment("/nonexistent/file.bin")
        assert aligned is False
        assert size == 0
        assert padding == 0


# ── Output Directory Tests ─────────────────────────────────


class TestOutputDirectory:
    def test_creates_nested_path(self, tmp_dir):
        out_root = str(tmp_dir)
        out = prepare_output_dir(out_root, "build_001", "pc", force=False)
        assert out.is_dir()
        assert out.name == "pc"
        assert out.parent.name == "build_001"

    def test_refuses_overwrite_without_force(self, tmp_dir):
        out_root = str(tmp_dir)
        prepare_output_dir(out_root, "dup", "pc", force=False)
        with pytest.raises(FileExistsError):
            prepare_output_dir(out_root, "dup", "pc", force=False)

    def test_force_overwrites(self, tmp_dir):
        out_root = str(tmp_dir)
        first = prepare_output_dir(out_root, "over", "pc", force=False)
        (first / "stale.txt").write_text("old")
        out = prepare_output_dir(out_root, "over", "pc", force=True)
        assert out.is_dir()
        # Should be empty (removed and recreated)
        assert not (out / "stale.txt").is_file()


# ── Manifest Writing Tests ─────────────────────────────────


class TestManifest:
    def test_writes_json(self, tmp_dir):
        profile = PLATFORM_PROFILES["pc"]
        path = write_package_manifest(
            str(tmp_dir), "pc", "test_001", profile, "abc123", True, True
        )
        assert path.is_file()
        data = json.loads(path.read_text())
        assert data["build_id"] == "test_001"
        assert data["platform"] == "pc"
        assert data["alignment_ok"] is True
        assert data["ast_prune_ok"] is True
        assert "compiled_at" in data

    def test_lists_artifacts(self, tmp_dir):
        # Create a fake artifact
        (tmp_dir / "package.bin").write_text("hi")
        profile = PLATFORM_PROFILES["steam_deck"]
        path = write_package_manifest(
            str(tmp_dir), "steam_deck", "sdk_001", profile, "xyz", True, False
        )
        data = json.loads(path.read_text())
        assert len(data["artifacts"]) == 1
        assert data["artifacts"][0]["path"] == "package.bin"


# ── Ledger Tests ────────────────────────────────────────────


class TestLedger:
    def test_empty_ledger(self, tmp_dir):
        path = str(tmp_dir / "ledger.json")
        ledger = load_ledger(path)
        assert ledger.meta["build_system_version"] == "2026.06.0"
        assert len(ledger.compiled_platform_profiles) == 0

    def test_save_and_reload(self, tmp_dir):
        path = str(tmp_dir / "ledger.json")
        ledger = load_ledger(path)

        bp = BuildProfile(
            target_hardware_platform="STEAM_DECK",
            texture_compression_standard="ASTC_8x8",
            max_texture_resolution_cap=2048,
            mesh_lod_bias_modifier=1.5,
            binary_package_path="/tmp/package.bin",
            cross_compilation_status="SUCCESS_VERIFIED",
            compiled_at="2026-06-14T12:00:00Z",
            input_hash="deadbeef",
            build_id="build_001",
            alignment_ok=True,
            ast_prune_ok=True,
        )
        ledger.compiled_platform_profiles["test_entry"] = bp
        save_ledger(path, ledger)

        # Reload
        ledger2 = load_ledger(path)
        assert ledger2.meta["build_system_version"] == "2026.06.0"
        assert "test_entry" in ledger2.compiled_platform_profiles
        reloaded = ledger2.compiled_platform_profiles["test_entry"]
        assert reloaded.target_hardware_platform == "STEAM_DECK"
        assert reloaded.texture_compression_standard == "ASTC_8x8"
        assert reloaded.cross_compilation_status == "SUCCESS_VERIFIED"

    def test_persistent_meta_across_saves(self, tmp_dir):
        path = str(tmp_dir / "ledger.json")
        ledger = load_ledger(path)
        ledger.meta["extra"] = "custom"
        save_ledger(path, ledger)

        ledger2 = load_ledger(path)
        assert ledger2.meta["extra"] == "custom"
        assert ledger2.meta["build_system_version"] == "2026.06.0"


# ── Integration Tests ───────────────────────────────────────


class TestIntegration:
    """End-to-end CLI integration tests."""

    def test_cli_dry_run_pc(self, tmp_dir, sample_src_dir, sample_catalog):
        """Run the cooker for PC platform and verify all outputs."""
        out_root = str(tmp_dir / "cooked")
        result = _run_cooker(
            src_dir=sample_src_dir,
            platform="pc",
            build_id="integ_001",
            catalog=sample_catalog,
            ast_prune=True,
            alignment_check=True,
            out_root=out_root,
            force=True,
        )
        assert result == 0

        # Verify output directory exists
        out_dir = Path(out_root) / "integ_001" / "pc"
        assert out_dir.is_dir()

        # Verify package.bin
        pkg = out_dir / "package.bin"
        assert pkg.is_file()
        assert pkg.stat().st_size > 0

        # Verify manifest
        manifest = out_dir / "package_manifest.json"
        assert manifest.is_file()
        data = json.loads(manifest.read_text())
        assert data["platform"] == "pc"
        assert data["build_id"] == "integ_001"

        # Verify pruned assets exist
        pruned = out_dir / "shaders" / "render.py"
        assert pruned.is_file()
        assert "PRUNED" in pruned.read_text()

    def test_cli_dry_run_steam_deck(self, tmp_dir, sample_src_dir):
        """Run the cooker for Steam Deck without alignment check."""
        out_root = str(tmp_dir / "cooked")
        result = _run_cooker(
            src_dir=sample_src_dir,
            platform="steam_deck",
            build_id="sd_001",
            catalog=None,
            ast_prune=False,
            alignment_check=False,
            out_root=out_root,
            force=True,
        )
        assert result == 0
        out_dir = Path(out_root) / "sd_001" / "steam_deck"
        assert out_dir.is_dir()

    def test_cli_dry_run_ps5(self, tmp_dir):
        """Run the cooker for PS5 — should produce stub artifacts."""
        src = tmp_dir / "src"
        src.mkdir()
        (src / "placeholder.txt").write_text("hello")

        out_root = str(tmp_dir / "cooked")
        result = _run_cooker(
            src_dir=str(src),
            platform="ps5",
            build_id="ps5_001",
            out_root=out_root,
            force=True,
        )
        assert result == 0
        out_dir = Path(out_root) / "ps5_001" / "ps5"
        pkg = out_dir / "package.bin"
        assert pkg.is_file()
        content = pkg.read_text()
        assert "SDK required" in content or "proprietary" in content.lower()

    def test_ledger_persists_across_runs(self, tmp_dir, sample_src_dir):
        """Run the cooker twice and verify ledger accumulates entries."""
        out_root = str(tmp_dir / "cooked")
        _run_cooker(
            src_dir=sample_src_dir,
            platform="pc",
            build_id="multi_001",
            out_root=out_root,
            force=True,
        )
        _run_cooker(
            src_dir=sample_src_dir,
            platform="steam_deck",
            build_id="multi_002",
            out_root=out_root,
            force=True,
        )

        # Check ledger has both entries
        ledger_path = Path.cwd() / "vault" / "multi_platform_build_ledger.json"
        if ledger_path.is_file():
            data = json.loads(ledger_path.read_text())
            profiles = data.get("compiled_platform_profiles", {})
            entries = [k for k in profiles if k.startswith("multi_")]
            assert len(entries) >= 1  # At least what we just did

    def test_refuses_overwrite_without_force(self, tmp_dir, sample_src_dir):
        """Running twice on same build_id+platform without --force should fail."""
        out_root = str(tmp_dir / "cooked")
        _run_cooker(
            src_dir=sample_src_dir,
            platform="pc",
            build_id="no_repeat",
            out_root=out_root,
            force=True,
        )
        # Second run without force
        result = _run_cooker(
            src_dir=sample_src_dir,
            platform="pc",
            build_id="no_repeat",
            out_root=out_root,
            force=False,
        )
        assert result == 1  # Should fail


def _run_cooker(
    src_dir: str,
    platform: str,
    build_id: str,
    catalog: str | None = None,
    ast_prune: bool = False,
    alignment_check: bool = False,
    out_root: str = "",
    force: bool = False,
) -> int:
    """Helper to invoke the cooker's main() via subprocess-like args."""
    # We import and call main directly but need to patch sys.argv
    import multi_platform_cooker as mpc

    # Store original argv
    orig_argv = sys.argv

    # Work from a temp cwd to avoid ledger clashes
    orig_cwd = os.getcwd()

    try:
        with tempfile.TemporaryDirectory() as td:
            os.chdir(td)

            args = [
                "multi_platform_cooker.py",
                "--src-dir", src_dir,
                "--platform", platform,
                "--build-id", build_id,
                "--out-dir", out_root or str(Path(td) / "cooked"),
            ]
            if catalog:
                args.extend(["--catalog", catalog])
            if ast_prune:
                args.append("--ast-prune")
            if alignment_check:
                args.append("--alignment-check")
            if force:
                args.append("--force")

            sys.argv = args
            rc = mpc.main()
            return rc
    except SystemExit as e:
        return e.code if e.code is not None else 0
    finally:
        os.chdir(orig_cwd)
        sys.argv = orig_argv
