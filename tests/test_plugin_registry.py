"""
tests/test_plugin_registry.py — Unit tests for PluginMarketplaceRegistry.

Covers:
- Plugin indexing from plugin-manifest.yaml files
- List, get, search, and pagination operations
- Full-text search across name, author, and description
- Filter by tag, hook, and author
- Re-index and upsert behaviour
- Registry client convenience methods
"""

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import yaml


# ── Test Helpers ────────────────────────────────────────────────────────────


def _make_manifest(
    plugin_dir: Path,
    name: str,
    version: str = "1.0.0",
    description: str = "",
    author: str = "",
    entry_point: str = "plugin:MyPlugin",
    tags: list | None = None,
    hooks: list | None = None,
    extra: dict | None = None,
) -> Path:
    """Create a ``plugin-manifest.yaml`` in *plugin_dir*."""
    manifest = {
        "name": name,
        "version": version,
        "description": description or f"The {name} plugin",
        "author": author or "Test Author",
        "entry_point": entry_point,
        "core_version_constraint": ">=1.0.0, <2.0.0",
        "dependencies": {"pip": []},
        "hooks": hooks or [],
        "personas": [],
    }
    if tags:
        manifest["tags"] = tags
    if extra:
        manifest.update(extra)

    path = plugin_dir / "plugin-manifest.yaml"
    with open(path, "w") as f:
        yaml.dump(manifest, f, default_flow_style=False)
    return path


# ── Tests ───────────────────────────────────────────────────────────────────


class TestPluginMarketplaceRegistry(unittest.TestCase):
    """Core registry tests."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "test_registry.db")
        self.plugins_dir = os.path.join(self.tmp_dir, "plugins")
        os.makedirs(self.plugins_dir, exist_ok=True)

        # Lazy import to avoid module-level failures
        from prismatic.plugins.registry import PluginMarketplaceRegistry

        self.registry = PluginMarketplaceRegistry(db_path=self.db_path)

    def tearDown(self):
        self.registry.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _create_plugin(self, name: str, **kwargs) -> str:
        """Create a plugin manifest and return its name."""
        plugin_dir = os.path.join(self.plugins_dir, name)
        os.makedirs(plugin_dir, exist_ok=True)
        _make_manifest(Path(plugin_dir), name=name, **kwargs)
        return name

    # ── Indexing ──────────────────────────────────────────────────────

    def test_index_empty_directory(self):
        count = self.registry.index_plugins(self.plugins_dir)
        self.assertEqual(count, 0)

    def test_index_single_plugin(self):
        self._create_plugin("test-plugin")
        count = self.registry.index_plugins(self.plugins_dir)
        self.assertEqual(count, 1)

    def test_index_multiple_plugins(self):
        self._create_plugin("plugin-a")
        self._create_plugin("plugin-b")
        self._create_plugin("plugin-c")
        count = self.registry.index_plugins(self.plugins_dir)
        self.assertEqual(count, 3)

    def test_index_skips_non_manifest_dirs(self):
        """Directories without plugin-manifest.yaml are skipped."""
        os.makedirs(os.path.join(self.plugins_dir, "not-a-plugin"), exist_ok=True)
        self._create_plugin("real-plugin")
        count = self.registry.index_plugins(self.plugins_dir)
        self.assertEqual(count, 1)

    def test_index_upserts_existing(self):
        self._create_plugin("my-plugin", version="1.0.0")
        self.registry.index_plugins(self.plugins_dir)

        # Update the manifest
        self._create_plugin("my-plugin", version="2.0.0")
        count = self.registry.index_plugins(self.plugins_dir)

        self.assertEqual(count, 1)  # upserted, not duplicated
        info = self.registry.get_plugin("my-plugin")
        assert info is not None
        self.assertEqual(info.version, "2.0.0")

    def test_reindex_drops_and_reloads(self):
        self._create_plugin("plugin-a")
        self._create_plugin("plugin-b")
        self.registry.index_plugins(self.plugins_dir)

        # Remove one and reindex
        shutil.rmtree(os.path.join(self.plugins_dir, "plugin-a"))
        count = self.registry.reindex(self.plugins_dir)
        self.assertEqual(count, 1)

        info = self.registry.get_plugin("plugin-b")
        assert info is not None
        self.assertEqual(info.name, "plugin-b")
        self.assertIsNone(self.registry.get_plugin("plugin-a"))

    # ── List / Pagination ─────────────────────────────────────────────

    def test_list_plugins_pagination(self):
        for i in range(10):
            self._create_plugin(f"plugin-{i:03d}")
        self.registry.index_plugins(self.plugins_dir)

        # Page 1: 3 items
        page1 = self.registry.list_plugins(offset=0, limit=3)
        self.assertEqual(len(page1.items), 3)
        self.assertEqual(page1.total, 10)
        self.assertTrue(page1.has_next)
        self.assertFalse(page1.has_previous)

        # Page 2: 3 items
        page2 = self.registry.list_plugins(offset=3, limit=3)
        self.assertEqual(len(page2.items), 3)
        self.assertTrue(page2.has_next)
        self.assertTrue(page2.has_previous)

        # Last page: 1 item
        page_last = self.registry.list_plugins(offset=9, limit=3)
        self.assertEqual(len(page_last.items), 1)
        self.assertFalse(page_last.has_next)
        self.assertTrue(page_last.has_previous)

    def test_list_plugins_ordered_by_name(self):
        self._create_plugin("zeta-plugin")
        self._create_plugin("alpha-plugin")
        self._create_plugin("beta-plugin")
        self.registry.index_plugins(self.plugins_dir)

        result = self.registry.list_plugins()
        names = [p.name for p in result.items]
        self.assertEqual(names, sorted(names))

    # ── Get ───────────────────────────────────────────────────────────

    def test_get_plugin_found(self):
        self._create_plugin(
            "my-plugin",
            version="2.1.0",
            description="A test plugin",
            author="Ned",
        )
        self.registry.index_plugins(self.plugins_dir)

        info = self.registry.get_plugin("my-plugin")
        assert info is not None
        self.assertEqual(info.name, "my-plugin")
        self.assertEqual(info.version, "2.1.0")
        self.assertEqual(info.description, "A test plugin")
        self.assertEqual(info.author, "Ned")

    def test_get_plugin_not_found(self):
        self.assertIsNone(self.registry.get_plugin("nonexistent"))

    def test_get_plugin_returns_tags(self):
        self._create_plugin("tagged-plugin", tags=["gpu", "observability", "monitoring"])
        self.registry.index_plugins(self.plugins_dir)

        info = self.registry.get_plugin("tagged-plugin")
        assert info is not None
        self.assertIn("gpu", info.tags)
        self.assertIn("observability", info.tags)

    def test_get_plugin_returns_hooks(self):
        self._create_plugin(
            "hooky-plugin",
            hooks=["on_init", "before_task_execution", "on_state_transition"],
        )
        self.registry.index_plugins(self.plugins_dir)

        info = self.registry.get_plugin("hooky-plugin")
        assert info is not None
        self.assertIn("on_init", info.hooks)
        self.assertIn("on_state_transition", info.hooks)

    # ── Search (Full-Text) ────────────────────────────────────────────

    def test_search_by_name(self):
        self._create_plugin("vram-observability", description="Tracks GPU memory usage")
        self._create_plugin("command-deck", description="Rich command palette for agents")
        self.registry.index_plugins(self.plugins_dir)

        result = self.registry.search_plugins(query="vram")
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].name, "vram-observability")

    def test_search_by_author(self):
        self._create_plugin("plugin-a", author="Fred")
        self._create_plugin("plugin-b", author="Ned")
        self._create_plugin("plugin-c", author="Fred")
        self.registry.index_plugins(self.plugins_dir)

        result = self.registry.search_plugins(author="Fred")
        self.assertEqual(len(result.items), 2)
        names = {p.name for p in result.items}
        self.assertEqual(names, {"plugin-a", "plugin-c"})

    def test_search_by_tag(self):
        self._create_plugin("gpu-mon", tags=["gpu", "monitoring"])
        self._create_plugin("log-watch", tags=["logging", "observability"])
        self._create_plugin("vram-tool", tags=["gpu", "memory"])
        self.registry.index_plugins(self.plugins_dir)

        result = self.registry.search_plugins(tag="gpu")
        self.assertEqual(len(result.items), 2)
        names = {p.name for p in result.items}
        self.assertEqual(names, {"gpu-mon", "vram-tool"})

    def test_search_by_hook(self):
        self._create_plugin("with-init", hooks=["on_init"])
        self._create_plugin("with-all", hooks=["on_init", "before_task_execution"])
        self._create_plugin("no-hooks", hooks=[])
        self.registry.index_plugins(self.plugins_dir)

        result = self.registry.search_plugins(hook="on_init")
        self.assertEqual(len(result.items), 2)

        result2 = self.registry.search_plugins(hook="before_task_execution")
        self.assertEqual(len(result2.items), 1)
        self.assertEqual(result2.items[0].name, "with-all")

    def test_search_combined_filters(self):
        self._create_plugin(
            "gpu-observer", author="Fred", tags=["gpu", "monitoring"],
            description="GPU observability plugin",
        )
        self._create_plugin(
            "gpu-trainer", author="Ned", tags=["gpu", "training"],
            description="GPU training workload manager",
        )
        self._create_plugin(
            "log-shipper", author="Fred", tags=["logging"],
            description="Log aggregation",
        )
        self.registry.index_plugins(self.plugins_dir)

        # Fred's GPU plugins only
        result = self.registry.search_plugins(author="Fred", tag="gpu")
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].name, "gpu-observer")

    def test_search_empty_results(self):
        self._create_plugin("alpha")
        self.registry.index_plugins(self.plugins_dir)

        result = self.registry.search_plugins(query="zzzznonexistent")
        self.assertEqual(len(result.items), 0)
        self.assertEqual(result.total, 0)

    def test_search_missing_plugin_dir(self):
        """Searching on an empty registry returns no results, not an error."""
        result = self.registry.search_plugins(query="anything")
        self.assertEqual(len(result.items), 0)
        self.assertEqual(result.total, 0)

    # ── Search Result Properties ─────────────────────────────────────

    def test_search_result_has_next_previous(self):
        for i in range(5):
            self._create_plugin(f"p-{i:03d}")
        self.registry.index_plugins(self.plugins_dir)

        # First page
        r1 = self.registry.search_plugins(limit=2)
        self.assertTrue(r1.has_next)
        self.assertFalse(r1.has_previous)

        # Middle page
        r2 = self.registry.search_plugins(offset=2, limit=2)
        self.assertTrue(r2.has_next)
        self.assertTrue(r2.has_previous)

        # Last page
        r3 = self.registry.search_plugins(offset=4, limit=2)
        self.assertFalse(r3.has_next)
        self.assertTrue(r3.has_previous)

    def test_search_result_to_dict(self):
        self._create_plugin("alpha")
        self.registry.index_plugins(self.plugins_dir)

        result = self.registry.search_plugins(limit=1)
        d = result.to_dict()
        self.assertIn("items", d)
        self.assertIn("total", d)
        self.assertIn("offset", d)
        self.assertIn("limit", d)
        self.assertIn("has_next", d)
        self.assertIn("has_previous", d)
        self.assertEqual(d["total"], 1)

    # ── Error Handling ───────────────────────────────────────────────

    def test_index_invalid_yaml(self):
        """A malformed manifest is skipped (not a crash)."""
        bad_dir = os.path.join(self.plugins_dir, "bad-plugin")
        os.makedirs(bad_dir, exist_ok=True)
        with open(os.path.join(bad_dir, "plugin-manifest.yaml"), "w") as f:
            f.write("{{{{invalid yaml::: broke")

        self._create_plugin("good-plugin")
        count = self.registry.index_plugins(self.plugins_dir)
        self.assertEqual(count, 1)  # only the good one

    def test_index_missing_name_field(self):
        """A manifest without 'name' is skipped."""
        bad_dir = os.path.join(self.plugins_dir, "nameless")
        os.makedirs(bad_dir, exist_ok=True)
        _make_manifest(Path(bad_dir), name="", description="no name")

        self._create_plugin("real-plugin")
        count = self.registry.index_plugins(self.plugins_dir)
        self.assertEqual(count, 1)

    def test_double_close_safe(self):
        """Calling close() twice should not raise."""
        self.registry.close()
        self.registry.close()  # no-op

    def test_manifest_path_preserved(self):
        self._create_plugin("known-path")
        self.registry.index_plugins(self.plugins_dir)

        info = self.registry.get_plugin("known-path")
        assert info is not None
        self.assertIn("known-path", info.manifest_path)
        self.assertTrue(info.manifest_path.endswith("plugin-manifest.yaml"))


# ── MarketRegistryClient Tests ──────────────────────────────────────────────


class TestMarketRegistryClient(unittest.TestCase):
    """Convenience client tests."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp_dir, "client_test.db")
        self.plugins_dir = os.path.join(self.tmp_dir, "plugins")
        os.makedirs(self.plugins_dir, exist_ok=True)

        from prismatic.plugins.registry_client import MarketRegistryClient

        self.client = MarketRegistryClient(db_path=self.db_path)

    def tearDown(self):
        self.client.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _add_plugin(self, name: str, **kwargs):
        plugin_dir = os.path.join(self.plugins_dir, name)
        os.makedirs(plugin_dir, exist_ok=True)
        _make_manifest(Path(plugin_dir), name=name, **kwargs)

    def test_search_convenience(self):
        self._add_plugin("alpha", description="GPU memory monitor")
        self._add_plugin("beta", description="CPU load tracker")
        self.client.index_plugins(self.plugins_dir)

        results = self.client.search("GPU")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, "alpha")

    def test_by_tag(self):
        self._add_plugin("a", tags=["gpu"])
        self._add_plugin("b", tags=["logging"])
        self.client.index_plugins(self.plugins_dir)

        results = self.client.by_tag("gpu")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, "a")

    def test_by_author(self):
        self._add_plugin("x", author="Ned")
        self._add_plugin("y", author="Fred")
        self.client.index_plugins(self.plugins_dir)

        results = self.client.by_author("Ned")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, "x")

    def test_all(self):
        self._add_plugin("p1")
        self._add_plugin("p2")
        self._add_plugin("p3")
        self.client.index_plugins(self.plugins_dir)

        all_plugins = self.client.all()
        self.assertEqual(len(all_plugins), 3)

    def test_count(self):
        self._add_plugin("a")
        self._add_plugin("b")
        self.client.index_plugins(self.plugins_dir)

        self.assertEqual(self.client.count(), 2)

    def test_health(self):
        self._add_plugin("healthy")
        self.client.index_plugins(self.plugins_dir)

        health = self.client.health()
        self.assertEqual(health["status"], "ok")
        self.assertIn("plugin_count", health)
        self.assertEqual(health["plugin_count"], 1)

    def test_reindex(self):
        self._add_plugin("initial")
        self.client.index_plugins(self.plugins_dir)
        self.assertEqual(self.client.count(), 1)

        shutil.rmtree(os.path.join(self.plugins_dir, "initial"))
        self._add_plugin("replacement")
        self.client.reindex(self.plugins_dir)
        self.assertEqual(self.client.count(), 1)
        self.assertIsNotNone(self.client.get_plugin("replacement"))
        self.assertIsNone(self.client.get_plugin("initial"))


if __name__ == "__main__":
    unittest.main()
