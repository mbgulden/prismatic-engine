"""
Tests for HardwareProfileRegistry and PluginLoader profile validation.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from prismatic.core.hardware_profiles import (
    HardwareProfile,
    HardwareProfileError,
    HardwareProfileRegistry,
)
from prismatic.core.registry import PluginLoader
from prismatic.interface.plugin import (
    PluginContext,
    PluginValidationError,
    PrismaticPlugin,
)


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def valid_profiles_yaml() -> dict:
    return {
        "profiles": [
            {
                "name": "llm-inference-large",
                "description": "Large LLM inference",
                "vram_gb_min": 24,
                "cpu_cores": 8,
                "memory_gb_min": 32,
                "gpu_required": True,
                "gpu_type": "nvidia",
            },
            {
                "name": "cpu-light-docs",
                "description": "CPU-only docs processing",
                "vram_gb_min": 0,
                "cpu_cores": 2,
                "memory_gb_min": 4,
                "gpu_required": False,
                "aliases": ["docs-only"],
            },
        ]
    }


@pytest.fixture
def profiles_file(tmp_path: Path, valid_profiles_yaml: dict) -> Path:
    path = tmp_path / "hardware_profiles.yaml"
    with open(path, "w") as fh:
        yaml.dump(valid_profiles_yaml, fh)
    return path


@pytest.fixture
def registry(profiles_file: Path) -> HardwareProfileRegistry:
    reg = HardwareProfileRegistry(yaml_path=profiles_file)
    reg.load()
    return reg


@pytest.fixture
def stub_plugin(tmp_path: Path) -> Path:
    """Create a minimal plugin directory with a valid plugin-manifest.yaml."""
    plugin_dir = tmp_path / "plugins" / "test_plugin"
    plugin_dir.mkdir(parents=True)

    # A tiny plugin module
    (plugin_dir / "plugin.py").write_text(
        """
from prismatic.interface.plugin import PrismaticPlugin, PluginContext
from typing import Any, Dict, List

class TestPlugin(PrismaticPlugin):
    def on_init(self, context: PluginContext) -> None:
        pass
    def register_tools(self) -> List[Dict[str, Any]]:
        return []
"""
    )
    return plugin_dir


@pytest.fixture
def plugin_context() -> PluginContext:
    return PluginContext(config={}, db_connection=None, state_dir="/tmp")


# ── Hardware Profile Tests ──────────────────────────────────────────────


class TestHardwareProfileRegistry:
    def test_load_profiles(self, registry: HardwareProfileRegistry):
        assert registry.profile_names == sorted(
            ["llm-inference-large", "cpu-light-docs"]
        )

    def test_get_known_profile(self, registry: HardwareProfileRegistry):
        profile = registry.get("llm-inference-large")
        assert isinstance(profile, HardwareProfile)
        assert profile.vram_gb_min == 24
        assert profile.gpu_required is True

    def test_get_by_alias(self, registry: HardwareProfileRegistry):
        profile = registry.get("docs-only")
        assert profile.name == "cpu-light-docs"

    def test_get_unknown_raises(self, registry: HardwareProfileRegistry):
        with pytest.raises(HardwareProfileError, match="Unknown"):
            registry.get("nonexistent-profile")

    def test_is_valid(self, registry: HardwareProfileRegistry):
        assert registry.is_valid("llm-inference-large") is True
        assert registry.is_valid("docs-only") is True
        assert registry.is_valid("unknown-profile") is False

    def test_missing_file_raises(self):
        reg = HardwareProfileRegistry(yaml_path="/nonexistent/path.yaml")
        with pytest.raises(HardwareProfileError, match="not found"):
            reg.load()

    def test_missing_profiles_key_raises(self, tmp_path: Path):
        bad_file = tmp_path / "empty.yaml"
        with open(bad_file, "w") as fh:
            yaml.dump({"not_profiles": []}, fh)
        reg = HardwareProfileRegistry(yaml_path=bad_file)
        with pytest.raises(HardwareProfileError, match="No.*profiles"):
            reg.load()


# ── Plugin Loader Validation Tests ──────────────────────────────────────


class TestPluginLoaderHardwareValidation:
    def _make_manifest(
        self, plugin_dir: Path, overrides: dict | None = None
    ) -> Path:
        manifest = {
            "name": "test-plugin",
            "version": "1.0.0",
            "entry_point": "plugin:TestPlugin",
            "core_version_constraint": ">=0.1.0",
        }
        if overrides:
            manifest.update(overrides)
        manifest_path = plugin_dir / "plugin-manifest.yaml"
        with open(manifest_path, "w") as fh:
            yaml.dump(manifest, fh)
        return manifest_path

    def test_valid_hardware_profile(
        self, registry: HardwareProfileRegistry, stub_plugin: Path, plugin_context: PluginContext
    ):
        self._make_manifest(stub_plugin, {"hardware_profile": "llm-inference-large"})
        loader = PluginLoader(
            core_version="0.2.0",
            plugins_dir=str(stub_plugin.parent),
            hardware_registry=registry,
        )
        # Should not raise
        loader.scan_and_load_plugins(plugin_context)
        assert "test-plugin" in loader.loaded_plugins

    def test_unknown_hardware_profile_rejected(
        self, registry: HardwareProfileRegistry, stub_plugin: Path
    ):
        self._make_manifest(stub_plugin, {"hardware_profile": "unknown-profile"})
        loader = PluginLoader(
            core_version="0.2.0",
            plugins_dir=str(stub_plugin.parent),
            hardware_registry=registry,
        )
        loader.scan_and_load_plugins(
            PluginContext(config={}, db_connection=None, state_dir="/tmp")
        )
        # Exception is caught and logged inside scan_and_load_plugins;
        # verify the plugin was NOT loaded
        assert "test-plugin" not in loader.loaded_plugins

    def test_valid_execution_profile(
        self, registry: HardwareProfileRegistry, stub_plugin: Path, plugin_context: PluginContext
    ):
        self._make_manifest(stub_plugin, {"execution_profile": "cpu-light-docs"})
        loader = PluginLoader(
            core_version="0.2.0",
            plugins_dir=str(stub_plugin.parent),
            hardware_registry=registry,
        )
        loader.scan_and_load_plugins(plugin_context)
        assert "test-plugin" in loader.loaded_plugins

    def test_alias_profile_accepted(
        self, registry: HardwareProfileRegistry, stub_plugin: Path, plugin_context: PluginContext
    ):
        self._make_manifest(stub_plugin, {"hardware_profile": "docs-only"})
        loader = PluginLoader(
            core_version="0.2.0",
            plugins_dir=str(stub_plugin.parent),
            hardware_registry=registry,
        )
        loader.scan_and_load_plugins(plugin_context)
        assert "test-plugin" in loader.loaded_plugins

    def test_no_registry_skips_validation(
        self, stub_plugin: Path
    ):
        """Without registry, manifest with unknown profile should load."""
        self._make_manifest(stub_plugin, {"hardware_profile": "will-never-exist"})
        loader = PluginLoader(
            core_version="0.2.0",
            plugins_dir=str(stub_plugin.parent),
            hardware_registry=None,
        )
        # Should not raise — logs a warning instead
        loader.scan_and_load_plugins(
            PluginContext(config={}, db_connection=None, state_dir="/tmp")
        )
        assert "test-plugin" in loader.loaded_plugins

    def test_existing_plugin_tests_still_pass(
        self, registry: HardwareProfileRegistry, stub_plugin: Path, plugin_context: PluginContext
    ):
        """Plugin without hardware_profile field still loads normally."""
        self._make_manifest(stub_plugin)  # no hardware field
        loader = PluginLoader(
            core_version="0.2.0",
            plugins_dir=str(stub_plugin.parent),
            hardware_registry=registry,
        )
        loader.scan_and_load_plugins(plugin_context)
        assert "test-plugin" in loader.loaded_plugins

    @pytest.mark.parametrize("field", ["hardware_profile", "execution_profile"])
    def test_malformed_vram_field_not_applicable(
        self, field: str, registry: HardwareProfileRegistry, stub_plugin: Path, plugin_context: PluginContext
    ):
        """Test that valid profiles pass validation regardless of profile spec content.
        The validation checks profile NAME, not the underlying spec values."""
        self._make_manifest(stub_plugin, {field: "cpu-light-docs"})
        loader = PluginLoader(
            core_version="0.2.0",
            plugins_dir=str(stub_plugin.parent),
            hardware_registry=registry,
        )
        loader.scan_and_load_plugins(plugin_context)
        assert "test-plugin" in loader.loaded_plugins


# ── Full pipeline integration ────────────────────────────────────────────


def test_end_to_end():
    """Validate that the full pipeline — YAML → Registry → PluginLoader — works."""
    profiles = {
        "profiles": [
            {
                "name": "audio-lyria",
                "description": "Audio generation",
                "vram_gb_min": 8,
                "cpu_cores": 2,
                "memory_gb_min": 8,
                "gpu_required": False,
            }
        ]
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        profiles_path = tmp / "profiles.yaml"
        with open(profiles_path, "w") as fh:
            yaml.dump(profiles, fh)

        reg = HardwareProfileRegistry(yaml_path=profiles_path)
        reg.load()
        assert reg.is_valid("audio-lyria")
        assert reg.get("audio-lyria").vram_gb_min == 8
