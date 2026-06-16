"""
Tests for the Version Compatibility Resolver (GRO-1821).

Covers:
- PluginVersionInfo creation
- Core version compatibility checks (known-good and known-conflicting)
- Transitive dependency traversal (satisfied and missing)
- Cross-plugin conflict detection (overlapping and conflicting constraints)
- Full resolution report generation
- Compatibility matrix building
- Edge cases: missing plugins, circular deps, empty deps, wildcard constraints
- PluginLoader integration (preflight_compatibility)
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import yaml

from prismatic.core.compat import (
    CompatibilityMatrix,
    ConflictInfo,
    PluginVersionInfo,
    ResolutionReport,
    ResolutionResult,
    VersionResolver,
)
from prismatic.core.registry import PluginLoader


# ═══════════════════════════════════════════════════════════════
# PluginVersionInfo tests
# ═══════════════════════════════════════════════════════════════


class TestPluginVersionInfo(unittest.TestCase):
    """PluginVersionInfo data model validation."""

    def test_minimal_plugin_info(self) -> None:
        """A minimal plugin info with no dependencies should work."""
        info = PluginVersionInfo(
            name="my-plugin",
            version="1.2.3",
            core_version_constraint=">=1.0.0",
        )
        self.assertEqual(info.name, "my-plugin")
        self.assertEqual(info.version, "1.2.3")
        self.assertEqual(info.core_version_constraint, ">=1.0.0")
        self.assertEqual(info.plugin_dependencies, {})

    def test_plugin_info_with_deps(self) -> None:
        """Plugin info with plugin dependencies should store them."""
        info = PluginVersionInfo(
            name="my-plugin",
            version="1.2.3",
            core_version_constraint=">=1.0.0",
            plugin_dependencies={"data-lake": ">=1.0.0", "logger": ">=2.0.0"},
        )
        self.assertEqual(len(info.plugin_dependencies), 2)
        self.assertEqual(info.plugin_dependencies["data-lake"], ">=1.0.0")

    def test_plugin_info_is_frozen(self) -> None:
        """PluginVersionInfo should be immutable."""
        info = PluginVersionInfo(
            name="frozen", version="1.0", core_version_constraint=">=1.0"
        )
        with self.assertRaises(AttributeError):
            info.name = "new-name"  # type: ignore[misc]


# ═══════════════════════════════════════════════════════════════
# VersionResolver - Core Compatibility tests
# ═══════════════════════════════════════════════════════════════


class TestCoreCompatibility(unittest.TestCase):
    """Known-good and known-conflicting core version scenarios."""

    def setUp(self) -> None:
        self.resolver = VersionResolver()

    def test_core_version_match(self) -> None:
        """Core version within the constraint should succeed."""
        plugin = PluginVersionInfo(
            name="test", version="1.0", core_version_constraint=">=1.0.0, <3.0.0"
        )
        self.assertTrue(
            self.resolver.check_core_compatibility(plugin, "2.5.0")
        )

    def test_core_version_exact_boundary(self) -> None:
        """Core version at the exact lower boundary should be compatible."""
        plugin = PluginVersionInfo(
            name="test", version="1.0", core_version_constraint=">=1.0.0"
        )
        self.assertTrue(
            self.resolver.check_core_compatibility(plugin, "1.0.0")
        )

    def test_core_version_below_minimum(self) -> None:
        """Core version below the constraint should fail."""
        plugin = PluginVersionInfo(
            name="test", version="1.0", core_version_constraint=">=2.0.0"
        )
        self.assertFalse(
            self.resolver.check_core_compatibility(plugin, "1.5.0")
        )

    def test_core_version_above_maximum(self) -> None:
        """Core version above the constraint should fail."""
        plugin = PluginVersionInfo(
            name="test", version="1.0", core_version_constraint="<2.0.0"
        )
        self.assertFalse(
            self.resolver.check_core_compatibility(plugin, "3.0.0")
        )

    def test_wildcard_constraint(self) -> None:
        """Wildcard ('*') constraint should always be compatible."""
        plugin = PluginVersionInfo(
            name="test", version="1.0", core_version_constraint="*"
        )
        self.assertTrue(
            self.resolver.check_core_compatibility(plugin, "99.0.0")
        )

    def test_empty_constraint(self) -> None:
        """Empty constraint string should be treated as compatible."""
        plugin = PluginVersionInfo(
            name="test", version="1.0", core_version_constraint=""
        )
        self.assertTrue(
            self.resolver.check_core_compatibility(plugin, "0.1.0")
        )

    def test_invalid_core_version_returns_false(self) -> None:
        """An unparseable core version should return False."""
        plugin = PluginVersionInfo(
            name="test", version="1.0", core_version_constraint=">=1.0.0"
        )
        self.assertFalse(
            self.resolver.check_core_compatibility(plugin, "not-a-version")
        )

    def test_invalid_constraint_string_treated_as_wildcard(self) -> None:
        """An unparseable constraint string should be treated as compatible."""
        plugin = PluginVersionInfo(
            name="test",
            version="1.0",
            core_version_constraint="totally-bogus!!",
        )
        self.assertTrue(
            self.resolver.check_core_compatibility(plugin, "1.0.0")
        )


# ═══════════════════════════════════════════════════════════════
# VersionResolver - Transitive Dependency tests
# ═══════════════════════════════════════════════════════════════


class TestTransitiveDependencies(unittest.TestCase):
    """Transitive dependency traversal: satisfied and missing."""

    def setUp(self) -> None:
        self.resolver = VersionResolver()

    def _make_plugin_map(self) -> dict:
        return {
            "a": PluginVersionInfo(
                name="a", version="1.0.0",
                core_version_constraint=">=1.0.0",
                plugin_dependencies={"b": ">=1.0.0"},
            ),
            "b": PluginVersionInfo(
                name="b", version="2.0.0",
                core_version_constraint=">=1.0.0",
                plugin_dependencies={"c": ">=1.0.0"},
            ),
            "c": PluginVersionInfo(
                name="c", version="3.0.0",
                core_version_constraint=">=1.0.0",
            ),
        }

    def test_all_deps_satisfied(self) -> None:
        """A chain of three plugins where all deps are satisfied."""
        plugin_map = self._make_plugin_map()
        issues = self.resolver.check_transitive_deps(
            plugin_map["a"], plugin_map, "2.0.0"
        )
        self.assertEqual(issues, [])

    def test_missing_dependency(self) -> None:
        """A plugin requiring a dependency that doesn't exist."""
        plugin_map = self._make_plugin_map()
        # Add a dependency that doesn't exist
        plugin_a = PluginVersionInfo(
            name="a", version="1.0.0",
            core_version_constraint=">=1.0.0",
            plugin_dependencies={"nonexistent": ">=1.0.0"},
        )
        issues = self.resolver.check_transitive_deps(
            plugin_a, plugin_map, "2.0.0"
        )
        self.assertGreater(len(issues), 0)
        self.assertTrue(
            any("Missing plugin dependency" in i for i in issues)
        )

    def test_version_mismatch_in_dep(self) -> None:
        """A dependency whose version doesn't satisfy the constraint."""
        plugin_map = self._make_plugin_map()
        # b requires c >=1.0.0 but let's say c is 0.5.0
        plugin_c = PluginVersionInfo(
            name="c", version="0.5.0",
            core_version_constraint=">=1.0.0",
        )
        plugin_map["c"] = plugin_c
        issues = self.resolver.check_transitive_deps(
            plugin_map["a"], plugin_map, "2.0.0"
        )
        self.assertGreater(len(issues), 0)
        self.assertTrue(
            any("does not satisfy constraint" in i for i in issues)
        )

    def test_circular_dependency(self) -> None:
        """Circular dependencies should be detected."""
        plugin_map = {
            "a": PluginVersionInfo(
                name="a", version="1.0.0",
                core_version_constraint=">=1.0.0",
                plugin_dependencies={"b": ">=1.0.0"},
            ),
            "b": PluginVersionInfo(
                name="b", version="1.0.0",
                core_version_constraint=">=1.0.0",
                plugin_dependencies={"a": ">=1.0.0"},
            ),
        }
        issues = self.resolver.check_transitive_deps(
            plugin_map["a"], plugin_map, "2.0.0"
        )
        self.assertGreater(len(issues), 0)
        self.assertTrue(
            any("Circular dependency" in i for i in issues)
        )

    def test_no_deps(self) -> None:
        """A plugin with no dependencies should pass cleanly."""
        plugin = PluginVersionInfo(
            name="standalone", version="1.0.0",
            core_version_constraint=">=1.0.0",
        )
        issues = self.resolver.check_transitive_deps(
            plugin, {}, "2.0.0"
        )
        self.assertEqual(issues, [])

    def test_transitive_core_mismatch(self) -> None:
        """A transitive dep that requires a different core version."""
        plugin_map = {
            "a": PluginVersionInfo(
                name="a", version="1.0.0",
                core_version_constraint=">=1.0.0",
                plugin_dependencies={"b": ">=1.0.0"},
            ),
            "b": PluginVersionInfo(
                name="b", version="1.0.0",
                core_version_constraint=">=5.0.0",  # needs core 5+
                plugin_dependencies={},
            ),
        }
        issues = self.resolver.check_transitive_deps(
            plugin_map["a"], plugin_map, "2.0.0"
        )
        self.assertGreater(len(issues), 0)
        self.assertTrue(
            any("requires core" in i for i in issues)
        )


# ═══════════════════════════════════════════════════════════════
# VersionResolver - Cross-Plugin Conflict Detection
# ═══════════════════════════════════════════════════════════════


class TestConflictDetection(unittest.TestCase):
    """Cross-plugin version conflict detection."""

    def setUp(self) -> None:
        self.resolver = VersionResolver()

    def test_no_conflicts(self) -> None:
        """Plugins with compatible version requirements."""
        plugins = [
            PluginVersionInfo(
                name="a", version="1.0.0",
                core_version_constraint=">=1.0.0",
                plugin_dependencies={"shared": ">=1.0.0"},
            ),
            PluginVersionInfo(
                name="b", version="2.0.0",
                core_version_constraint=">=1.0.0",
                plugin_dependencies={"shared": ">=1.0.0"},
            ),
            PluginVersionInfo(
                name="shared", version="2.0.0",
                core_version_constraint=">=1.0.0",
            ),
        ]
        conflicts = self.resolver.detect_conflicts(plugins)
        self.assertEqual(conflicts, [])

    def test_conflicting_requirements(self) -> None:
        """Two plugins requiring incompatible versions of the same dep."""
        plugins = [
            PluginVersionInfo(
                name="a", version="1.0.0",
                core_version_constraint=">=1.0.0",
                plugin_dependencies={"shared": "<=1.0.0"},
            ),
            PluginVersionInfo(
                name="b", version="2.0.0",
                core_version_constraint=">=1.0.0",
                plugin_dependencies={"shared": ">=2.0.0"},
            ),
            PluginVersionInfo(
                name="shared", version="1.5.0",
                core_version_constraint=">=1.0.0",
            ),
        ]
        conflicts = self.resolver.detect_conflicts(plugins)
        self.assertGreater(len(conflicts), 0)
        self.assertTrue(
            any("<=1.0.0" in c.reason and ">=2.0.0" in c.reason for c in conflicts)
        )

    def test_self_constraint_satisfied(self) -> None:
        """A plugin that is a dependency should satisfy its own version."""
        plugins = [
            PluginVersionInfo(
                name="a", version="1.0.0",
                core_version_constraint=">=1.0.0",
                plugin_dependencies={"b": "==1.0.0"},
            ),
            PluginVersionInfo(
                name="b", version="1.0.0",
                core_version_constraint=">=1.0.0",
            ),
        ]
        conflicts = self.resolver.detect_conflicts(plugins)
        # b's self-constraint ==1.0.0 matches a's requirement ==1.0.0
        self.assertEqual(conflicts, [])

    def test_multiple_dependency_conflicts(self) -> None:
        """Multiple conflicting dependencies should all be reported."""
        plugins = [
            PluginVersionInfo(
                name="a", version="1.0.0",
                core_version_constraint=">=1.0.0",
                plugin_dependencies={"x": "<2.0", "y": ">=3.0"},
            ),
            PluginVersionInfo(
                name="b", version="2.0.0",
                core_version_constraint=">=1.0.0",
                plugin_dependencies={"x": ">=2.0", "y": "<3.0"},
            ),
            PluginVersionInfo(
                name="x", version="2.5.0",
                core_version_constraint=">=1.0.0",
            ),
            PluginVersionInfo(
                name="y", version="3.0.0",
                core_version_constraint=">=1.0.0",
            ),
        ]
        conflicts = self.resolver.detect_conflicts(plugins)
        self.assertGreaterEqual(len(conflicts), 2)


# ═══════════════════════════════════════════════════════════════
# VersionResolver - Full Resolution Report
# ═══════════════════════════════════════════════════════════════


class TestFullResolution(unittest.TestCase):
    """End-to-end resolution report generation."""

    def setUp(self) -> None:
        self.resolver = VersionResolver()

    def test_all_plugins_pass(self) -> None:
        """All plugins compatible should result in a clean report."""
        plugins = [
            PluginVersionInfo(
                name="a", version="1.0.0",
                core_version_constraint=">=1.0.0",
            ),
            PluginVersionInfo(
                name="b", version="2.0.0",
                core_version_constraint=">=1.0.0",
            ),
        ]
        report = self.resolver.resolve(plugins, "1.5.0")
        self.assertIsInstance(report, ResolutionReport)
        self.assertEqual(len(report.results), 2)
        self.assertTrue(all(r.compatible for r in report.results))
        self.assertEqual(len(report.global_conflicts), 0)
        self.assertIsNotNone(report.matrix)

    def test_some_plugins_blocked(self) -> None:
        """Plugins with incompatible requirements should be blocked."""
        plugins = [
            PluginVersionInfo(
                name="old", version="1.0.0",
                core_version_constraint=">=0.5.0, <1.0.0",
            ),
            PluginVersionInfo(
                name="new", version="2.0.0",
                core_version_constraint=">=2.0.0",
            ),
        ]
        report = self.resolver.resolve(plugins, "1.5.0")
        self.assertFalse(report.results[0].compatible)
        self.assertFalse(report.results[1].compatible)

    def test_report_contains_blocking_issues(self) -> None:
        """Blocking issues should provide human-readable explanations."""
        plugins = [
            PluginVersionInfo(
                name="a", version="1.0.0",
                core_version_constraint=">=5.0.0",
            ),
        ]
        report = self.resolver.resolve(plugins, "1.0.0")
        blocking = report.results[0].blocking_issues
        self.assertGreater(len(blocking), 0)
        self.assertTrue(any("Core version" in i for i in blocking))

    def test_empty_plugin_list(self) -> None:
        """Empty plugin list should produce a minimal report."""
        report = self.resolver.resolve([], "1.0.0")
        self.assertEqual(len(report.results), 0)
        self.assertEqual(report.summary, "Version compatibility resolution for 0 plugin(s): 0 compatible, 0 blocked.")

    def test_conflict_appears_in_report(self) -> None:
        """Cross-plugin conflicts should appear in the report."""
        plugins = [
            PluginVersionInfo(
                name="a", version="1.0.0",
                core_version_constraint=">=1.0.0",
                plugin_dependencies={"shared": "<2.0"},
            ),
            PluginVersionInfo(
                name="b", version="2.0.0",
                core_version_constraint=">=1.0.0",
                plugin_dependencies={"shared": ">=3.0"},
            ),
            PluginVersionInfo(
                name="shared", version="2.5.0",
                core_version_constraint=">=1.0.0",
            ),
        ]
        report = self.resolver.resolve(plugins, "2.0.0")
        self.assertGreater(len(report.global_conflicts), 0)
        self.assertIn("Cross-plugin conflicts", report.summary)


# ═══════════════════════════════════════════════════════════════
# Compatibility Matrix tests
# ═══════════════════════════════════════════════════════════════


class TestCompatibilityMatrix(unittest.TestCase):
    """Pairwise compatibility matrix generation."""

    def setUp(self) -> None:
        self.resolver = VersionResolver()

    def test_matrix_all_same(self) -> None:
        """Matrix diagonal should be 'same'."""
        plugins = [
            PluginVersionInfo(
                name="a", version="1.0.0",
                core_version_constraint=">=1.0.0",
            ),
            PluginVersionInfo(
                name="b", version="2.0.0",
                core_version_constraint=">=1.0.0",
            ),
        ]
        matrix = self.resolver.build_compatibility_matrix(plugins, "1.5.0")
        self.assertEqual(matrix.rows, ["a", "b"])
        self.assertEqual(matrix.columns, ["a", "b"])
        self.assertEqual(matrix.cells[0][0], "same")
        self.assertEqual(matrix.cells[1][1], "same")

    def test_matrix_all_ok(self) -> None:
        """Non-conflicting plugins should show 'ok'."""
        plugins = [
            PluginVersionInfo(
                name="a", version="1.0.0",
                core_version_constraint=">=1.0.0",
                plugin_dependencies={"b": ">=1.0.0"},
            ),
            PluginVersionInfo(
                name="b", version="2.0.0",
                core_version_constraint=">=1.0.0",
            ),
        ]
        matrix = self.resolver.build_compatibility_matrix(plugins, "1.5.0")
        self.assertEqual(matrix.cells[0][1], "ok")
        self.assertEqual(matrix.cells[1][0], "ok")

    def test_matrix_conflict(self) -> None:
        """Conflicting requirements should show 'conflict'."""
        plugins = [
            PluginVersionInfo(
                name="a", version="1.0.0",
                core_version_constraint=">=1.0.0",
                plugin_dependencies={"b": "<=1.0.0"},
            ),
            PluginVersionInfo(
                name="b", version="2.0.0",
                core_version_constraint=">=1.0.0",
            ),
        ]
        matrix = self.resolver.build_compatibility_matrix(plugins, "1.5.0")
        self.assertEqual(matrix.cells[0][1], "conflict")
        self.assertEqual(matrix.cells[1][0], "conflict")

    def test_matrix_to_dict_roundtrip(self) -> None:
        """Matrix serialisation should roundtrip cleanly."""
        matrix = CompatibilityMatrix(
            rows=["a", "b"],
            columns=["a", "b"],
            cells=[["same", "ok"], ["ok", "same"]],
        )
        d = matrix.to_dict()
        restored = CompatibilityMatrix.from_dict(d)
        self.assertEqual(restored.rows, matrix.rows)
        self.assertEqual(restored.columns, matrix.columns)
        self.assertEqual(restored.cells, matrix.cells)

    def test_matrix_single_plugin(self) -> None:
        """A single plugin should produce a 1x1 matrix."""
        plugins = [
            PluginVersionInfo(
                name="solo", version="1.0.0",
                core_version_constraint=">=1.0.0",
            ),
        ]
        matrix = self.resolver.build_compatibility_matrix(plugins, "1.0.0")
        self.assertEqual(matrix.rows, ["solo"])
        self.assertEqual(matrix.cells[0][0], "same")


# ═══════════════════════════════════════════════════════════════
# ResolutionResult data model tests
# ═══════════════════════════════════════════════════════════════


class TestResolutionResult(unittest.TestCase):
    """ResolutionResult data model."""

    def test_compatible_has_no_blocking_issues(self) -> None:
        """A compatible result should have no blocking issues."""
        result = ResolutionResult(
            plugin="ok", version="1.0",
            compatible=True,
        )
        self.assertEqual(result.blocking_issues, [])
        self.assertEqual(result.peer_conflicts, [])
        self.assertTrue(result.core_compatible)
        self.assertTrue(result.transitive_deps_ok)

    def test_blocked_plugin_lists_issues(self) -> None:
        """A blocked plugin should explain why."""
        result = ResolutionResult(
            plugin="bad",
            version="1.0",
            compatible=False,
            core_compatible=False,
            blocking_issues=["Core version 0.5 does not satisfy >=1.0.0"],
        )
        self.assertFalse(result.compatible)
        self.assertFalse(result.core_compatible)
        self.assertEqual(len(result.blocking_issues), 1)


# ═══════════════════════════════════════════════════════════════
# PluginLoader Integration tests
# ═══════════════════════════════════════════════════════════════


class TestPluginLoaderIntegration(unittest.TestCase):
    """Integration tests for PluginLoader.preflight_compatibility."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="prismatic-compat-test-")

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_manifest(
        self, plugin_dir: str, name: str,
        version: str = "1.0.0",
        core_constraint: str = ">=1.0.0",
        plugin_deps: dict | None = None,
    ) -> Path:
        """Helper to write a manifest file and return its path."""
        plugin_path = Path(self.tmpdir) / plugin_dir
        plugin_path.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": "1.0.0",
            "name": name,
            "version": version,
            "description": f"Test plugin {name}",
            "entry_point": f"{plugin_dir}.plugin:{name.capitalize()}Plugin",
            "core_version_constraint": core_constraint,
            "dependencies": {
                "pip": [],
            },
            "personas": [],
            "hooks": ["on_init"],
        }
        if plugin_deps:
            manifest["dependencies"]["plugins"] = plugin_deps
        manifest_path = plugin_path / "plugin-manifest.yaml"
        with open(manifest_path, "w") as fh:
            yaml.dump(manifest, fh, default_flow_style=False)
        return manifest_path

    def test_preflight_with_override(self) -> None:
        """preflight_compatibility with override should use provided infos."""
        loader = PluginLoader(core_version="1.0.0", plugins_dir=self.tmpdir)
        infos = [
            PluginVersionInfo(
                name="custom", version="2.0.0",
                core_version_constraint=">=1.0.0",
            ),
        ]
        report = loader.preflight_compatibility(plugins_override=infos)
        self.assertEqual(len(report.results), 1)
        self.assertTrue(report.results[0].compatible)

    def test_preflight_no_plugins(self) -> None:
        """Preflight on empty directory should produce empty report."""
        loader = PluginLoader(core_version="1.0.0", plugins_dir=self.tmpdir)
        report = loader.preflight_compatibility()
        self.assertEqual(len(report.results), 0)

    def test_preflight_scans_directory(self) -> None:
        """Preflight should discover plugins from the filesystem."""
        self._write_manifest("plugin-a", "plugin-a")
        self._write_manifest("plugin-b", "plugin-b")

        loader = PluginLoader(core_version="1.0.0", plugins_dir=self.tmpdir)
        report = loader.preflight_compatibility()
        self.assertEqual(len(report.results), 2)
        self.assertTrue(all(r.compatible for r in report.results))

    def test_preflight_detects_incompatible_plugin(self) -> None:
        """Preflight should catch plugins that don't match the core version."""
        self._write_manifest(
            "old-plugin", "old",
            core_constraint=">=5.0.0",
        )
        self._write_manifest(
            "new-plugin", "new",
            core_constraint=">=1.0.0",
        )

        loader = PluginLoader(core_version="2.0.0", plugins_dir=self.tmpdir)
        report = loader.preflight_compatibility()
        results_by_name = {r.plugin: r for r in report.results}
        self.assertFalse(results_by_name["old"].compatible)
        self.assertTrue(results_by_name["new"].compatible)

    def test_preflight_detects_plugin_deps(self) -> None:
        """Preflight should read plugin dependencies from manifests."""
        self._write_manifest(
            "plugin-a", "plugin-a",
            plugin_deps={"plugin-b": ">=1.0.0"},
        )
        self._write_manifest(
            "plugin-b", "plugin-b",
        )
        loader = PluginLoader(core_version="2.0.0", plugins_dir=self.tmpdir)
        report = loader.preflight_compatibility()
        results_by_name = {r.plugin: r for r in report.results}
        self.assertTrue(results_by_name["plugin-a"].compatible)
        self.assertTrue(results_by_name["plugin-b"].compatible)

    def test_preflight_detects_missing_dep(self) -> None:
        """Preflight should flag missing plugin dependencies."""
        self._write_manifest(
            "plugin-a", "plugin-a",
            plugin_deps={"nonexistent": ">=1.0.0"},
        )
        self._write_manifest("plugin-b", "plugin-b")

        loader = PluginLoader(core_version="2.0.0", plugins_dir=self.tmpdir)
        report = loader.preflight_compatibility()
        results_by_name = {r.plugin: r for r in report.results}
        self.assertFalse(results_by_name["plugin-a"].compatible)

    def test_preflight_includes_matrix(self) -> None:
        """Preflight report should include a compatibility matrix."""
        self._write_manifest("plugin-a", "plugin-a")
        self._write_manifest("plugin-b", "plugin-b")

        loader = PluginLoader(core_version="2.0.0", plugins_dir=self.tmpdir)
        report = loader.preflight_compatibility()
        self.assertIsNotNone(report.matrix)
        self.assertEqual(len(report.matrix.rows), 2)
        self.assertEqual(len(report.matrix.columns), 2)


if __name__ == "__main__":
    unittest.main()
