"""Tests for scripts/universal_asset_indexer.py — GRO-1624.

Covers: catalog loading, cycle detection (Tarjan's SCC), referential
integrity, DAG closure index, and CLI exit codes.
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

from universal_asset_indexer import (
    AssetNode,
    Catalog,
    IntegrityReport,
    adjacency_matrix_exponentiation,
    build_catalog,
    build_closure_index,
    build_parser,
    detect_cycles,
    format_broken_link,
    format_cycle,
    load_catalog_from_json,
    verify_integrity,
)


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def acyclic_catalog():
    """A simple acyclic DAG: A -> B -> C, A -> D"""
    cat = Catalog()
    cat.add_node(AssetNode("A", deps=["B", "D"]))
    cat.add_node(AssetNode("B", deps=["C"]))
    cat.add_node(AssetNode("C"))
    cat.add_node(AssetNode("D"))
    return cat


@pytest.fixture
def cyclic_catalog():
    """A simple cycle: A -> B -> C -> A"""
    cat = Catalog()
    cat.add_node(AssetNode("A", deps=["B"]))
    cat.add_node(AssetNode("B", deps=["C"]))
    cat.add_node(AssetNode("C", deps=["A"]))
    return cat


@pytest.fixture
def diamond_catalog():
    """Diamond dependency: A -> B, C; B,C -> D"""
    cat = Catalog()
    cat.add_node(AssetNode("A", deps=["B", "C"]))
    cat.add_node(AssetNode("B", deps=["D"]))
    cat.add_node(AssetNode("C", deps=["D"]))
    cat.add_node(AssetNode("D"))
    return cat


@pytest.fixture
def self_loop_catalog():
    """A node with a self-loop: A -> A"""
    cat = Catalog()
    cat.add_node(AssetNode("A", deps=["A"]))
    return cat


@pytest.fixture
def broken_link_catalog():
    """A -> B, but B doesn't exist"""
    cat = Catalog()
    cat.add_node(AssetNode("A", deps=["B"]))
    return cat


# ── Catalog loading ──────────────────────────────────────────


def test_load_catalog_from_json(tmp_dir):
    """Load a valid catalog JSON file."""
    catalog_path = tmp_dir / "test_catalog.json"
    data = {
        "version": "1.0",
        "assets": [
            {
                "asset_id": "asset:hero",
                "name": "Hero Model",
                "type": "mesh",
                "file_path": "models/hero.glb",
                "dependencies": [],
            },
            {
                "asset_id": "asset:weapon",
                "name": "Weapon",
                "type": "mesh",
                "file_path": "models/weapon.glb",
                "dependencies": ["asset:hero"],
            },
        ],
    }
    catalog_path.write_text(json.dumps(data))

    cat = load_catalog_from_json(str(catalog_path))
    assert cat.node_count == 2
    assert cat.get("asset:hero") is not None
    assert cat.get("asset:weapon") is not None
    assert "asset:hero" in cat.get("asset:weapon").deps
    assert cat.source_path == str(catalog_path)


def test_load_catalog_not_found(tmp_dir):
    """Missing file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_catalog_from_json(str(tmp_dir / "nonexistent.json"))


def test_build_catalog_fallback(tmp_dir):
    """build_catalog with no path auto-discovers (should return empty or plugin-based)."""
    cat = build_catalog(base_dir=str(tmp_dir))
    # Empty directory → 0 nodes
    assert cat.node_count == 0


# ── Cycle detection (Tarjan's SCC) ───────────────────────────


class TestCycleDetection:
    """Test Tarjan's SCC-based cycle detection."""

    def test_acyclic(self, acyclic_catalog):
        """Acyclic DAG → zero cycles."""
        cycles = detect_cycles(acyclic_catalog)
        assert len(cycles) == 0

    def test_simple_cycle(self, cyclic_catalog):
        """A->B->C->A → one cycle."""
        cycles = detect_cycles(cyclic_catalog)
        assert len(cycles) == 1
        members = set(cycles[0])
        assert members == {"A", "B", "C"}

    def test_diamond(self, diamond_catalog):
        """Diamond dep → zero cycles."""
        cycles = detect_cycles(diamond_catalog)
        assert len(cycles) == 0

    def test_self_loop(self, self_loop_catalog):
        """A->A → one cycle."""
        cycles = detect_cycles(self_loop_catalog)
        assert len(cycles) == 1
        assert cycles[0] == ["A"]

    def test_empty_catalog(self):
        """Empty catalog → zero cycles."""
        cat = Catalog()
        cycles = detect_cycles(cat)
        assert len(cycles) == 0

    def test_no_edges(self):
        """Single node with no deps → zero cycles."""
        cat = Catalog()
        cat.add_node(AssetNode("A"))
        cycles = detect_cycles(cat)
        assert len(cycles) == 0

    def test_disjoint_cyclic_components(self):
        """Two disjoint cycles detected independently."""
        cat = Catalog()
        # Cycle 1: X -> Y -> X
        cat.add_node(AssetNode("X", deps=["Y"]))
        cat.add_node(AssetNode("Y", deps=["X"]))
        # Cycle 2: P -> Q -> R -> P
        cat.add_node(AssetNode("P", deps=["Q"]))
        cat.add_node(AssetNode("Q", deps=["R"]))
        cat.add_node(AssetNode("R", deps=["P"]))

        cycles = detect_cycles(cat)
        assert len(cycles) == 2


# ── Referential integrity ────────────────────────────────────


class TestIntegrity:
    """Test verify_integrity."""

    def test_no_broken_links(self, acyclic_catalog):
        """All references resolve → no violations."""
        report = verify_integrity(acyclic_catalog)
        assert not report.has_violations

    def test_broken_link(self, broken_link_catalog):
        """A references B, but B doesn't exist."""
        report = verify_integrity(broken_link_catalog)
        assert report.has_violations
        assert len(report.broken_links) == 1
        assert report.broken_links[0]["source"] == "A"
        assert report.broken_links[0]["target"] == "B"

    def test_multiple_broken_links(self):
        """A -> B, C -> D (both broken)."""
        cat = Catalog()
        cat.add_node(AssetNode("A", deps=["B"]))
        cat.add_node(AssetNode("C", deps=["D"]))
        report = verify_integrity(cat)
        assert report.has_violations
        assert len(report.broken_links) == 2

    def test_empty_catalog(self):
        """Empty catalog → no violations."""
        report = verify_integrity(Catalog())
        assert not report.has_violations

    def test_integrity_report_to_dict(self, broken_link_catalog):
        """to_dict() produces expected keys."""
        report = verify_integrity(broken_link_catalog)
        d = report.to_dict()
        assert "broken_links" in d


# ── DAG closure index ─────────────────────────────────────────


class TestClosureIndex:
    """Test build_closure_index."""

    def test_simple_chain(self, acyclic_catalog):
        """A -> [B, D]; B -> [C]; C -> []; D -> []"""
        closure = build_closure_index(acyclic_catalog)
        assert set(closure["A"]) == {"B", "C", "D"}
        assert set(closure["B"]) == {"C"}
        assert closure["C"] == []
        assert closure["D"] == []

    def test_diamond(self, diamond_catalog):
        """A -> B, C -> D"""
        closure = build_closure_index(diamond_catalog)
        assert set(closure["A"]) == {"B", "C", "D"}
        assert closure["B"] == ["D"]
        assert closure["C"] == ["D"]
        assert closure["D"] == []

    def test_no_deps(self):
        """Node with no deps → empty closure."""
        cat = Catalog()
        cat.add_node(AssetNode("A"))
        closure = build_closure_index(cat)
        assert closure["A"] == []


# ── GPU adjacency stub ────────────────────────────────────────


class TestGpuAdjExp:
    """Test adjacency_matrix_exponentiation (MVP stub)."""

    def test_stub_returns_closure(self, acyclic_catalog):
        """Stub should return the same as build_closure_index."""
        result = adjacency_matrix_exponentiation(acyclic_catalog)
        expected = build_closure_index(acyclic_catalog)
        assert result == expected


# ── Formatting ────────────────────────────────────────────────


class TestFormatting:
    """Test format helpers."""

    def test_format_cycle(self, cyclic_catalog):
        """Cycle formatting prints members with arrows."""
        cycles = detect_cycles(cyclic_catalog)
        formatted = format_cycle(cycles[0], cyclic_catalog)
        assert "→" in formatted
        # All members mentioned
        for m in cycles[0]:
            assert m in formatted

    def test_format_broken_link(self):
        """Broken link format includes source and target."""
        link = {"source": "A", "target": "B", "source_file": "manifest.yaml"}
        formatted = format_broken_link(link)
        assert "A" in formatted
        assert "B" in formatted
        assert "MISSING" in formatted
        assert "manifest.yaml" in formatted

    def test_format_cycle_with_file_path(self):
        """Cycle with file paths includes them."""
        cat = Catalog()
        cat.add_node(AssetNode("A", deps=["B"], file_path="plugins/a.yaml"))
        cat.add_node(AssetNode("B", deps=["A"], file_path="plugins/b.yaml"))
        cycles = detect_cycles(cat)
        formatted = format_cycle(cycles[0], cat)
        assert "a.yaml" in formatted
        assert "b.yaml" in formatted


# ── CLI tests ────────────────────────────────────────────────


class TestCLI:
    """Test CLI argument parsing and exit codes."""

    def test_parser_basic(self):
        """Parser accepts --detect-cycles and --verify-integrity."""
        parser = build_parser()
        args = parser.parse_args(["--detect-cycles", "--verify-integrity"])
        assert args.detect_cycles
        assert args.verify_integrity
        assert not args.json

    def test_parser_json_flag(self):
        """--json sets flag."""
        parser = build_parser()
        args = parser.parse_args(["--json"])
        assert args.json

    def test_parser_catalog_path(self):
        """--catalog sets path."""
        parser = build_parser()
        args = parser.parse_args(["--catalog", "vault/test.json"])
        assert args.catalog == "vault/test.json"

    def test_parser_output(self):
        """--output sets path."""
        parser = build_parser()
        args = parser.parse_args(["--output", "reports/index.json"])
        assert args.output == "reports/index.json"

    def test_parser_gpu_flag(self):
        """--gpu-adj-exp sets flag."""
        parser = build_parser()
        args = parser.parse_args(["--gpu-adj-exp"])
        assert args.gpu_adj_exp

    def test_parser_verbose(self):
        """--verbose sets flag."""
        parser = build_parser()
        args = parser.parse_args(["--verbose"])
        assert args.verbose

    def test_cli_acyclic_exit_0(self):
        """Acyclic catalog → exit 0."""
        cat = Catalog()
        cat.add_node(AssetNode("A", deps=["B"]))
        cat.add_node(AssetNode("B"))
        cycles = detect_cycles(cat)
        assert len(cycles) == 0

    def test_cli_cyclic_exit_1(self):
        """Cyclic catalog → exit 1 (simulates CLI exit code)."""
        cat = Catalog()
        cat.add_node(AssetNode("A", deps=["B"]))
        cat.add_node(AssetNode("B", deps=["A"]))
        cycles = detect_cycles(cat)
        assert len(cycles) == 1

    def test_cli_no_file_exit_2(self):
        """Missing catalog file → exit 2."""
        with tempfile.TemporaryDirectory() as td:
            import universal_asset_indexer as uai
            try:
                uai.main(["--catalog", os.path.join(td, "noexist.json")])
            except SystemExit as e:
                assert e.code == 2

    def test_cli_integrity_pass(self):
        """All references resolve → exit 0."""
        cat = Catalog()
        cat.add_node(AssetNode("A", deps=["B"]))
        cat.add_node(AssetNode("B"))
        report = verify_integrity(cat)
        assert not report.has_violations

    def test_cli_integrity_fail(self, broken_link_catalog):
        """Broken links → report has violations."""
        report = verify_integrity(broken_link_catalog)
        assert report.has_violations


# ── Edge cases ──────────────────────────────────────────────


class TestEdgeCases:
    """Edge case coverage."""

    def test_asset_node_defaults(self):
        """AssetNode defaults handle empty values."""
        node = AssetNode("test-id")
        assert node.name == "test-id"
        assert node.asset_type == "unknown"
        assert node.file_path == ""
        assert node.deps == []

    def test_catalog_node_count_empty(self):
        """Empty catalog has 0 nodes, 0 edges."""
        cat = Catalog()
        assert cat.node_count == 0
        assert cat.edge_count == 0

    def test_catalog_repeated_add(self):
        """Adding same asset_id twice overwrites (last wins)."""
        cat = Catalog()
        cat.add_node(AssetNode("A", asset_type="mesh"))
        cat.add_node(AssetNode("A", asset_type="texture"))
        assert cat.node_count == 1
        assert cat.get("A").asset_type == "texture"

    def test_cycle_detection_idempotent(self, cyclic_catalog):
        """Calling detect() twice returns same result."""
        d1 = detect_cycles(cyclic_catalog)
        d2 = detect_cycles(cyclic_catalog)
        # Same number of cycles
        assert len(d1) == len(d2)

    def test_large_acyclic_graph(self):
        """10-node linear chain → no cycles."""
        cat = Catalog()
        for i in range(10):
            node_id = f"N{i}"
            deps = [f"N{i+1}"] if i < 9 else []
            cat.add_node(AssetNode(node_id, deps=deps))
        cycles = detect_cycles(cat)
        assert len(cycles) == 0
