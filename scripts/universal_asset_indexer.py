#!/usr/bin/env python3
"""
Universal Asset Indexer — DAG cycle detector & referential integrity checker.

Builds a directed graph from an asset catalog (JSON or discovered from plugin
manifests / config files), runs Tarjan's SCC algorithm to detect circular
dependencies, and verifies that all inter-asset references resolve.

Usage:
    # Build catalog from plugin manifests and scan for cycles
    python3 scripts/universal_asset_indexer.py --detect-cycles

    # Verify referential integrity (no dangling references)
    python3 scripts/universal_asset_indexer.py --verify-integrity

    # Run all checks and write a DAG closure index
    python3 scripts/universal_asset_indexer.py \\
        --catalog vault/asset_catalog.json \\
        --output reports/asset_dag_index.json \\
        --detect-cycles --verify-integrity

    # Multi-GPU adjacency exponentiation (stub — MVP placeholder)
    python3 scripts/universal_asset_indexer.py --gpu-adj-exp

Exit codes:
    0 — All checks passed
    1 — Cycles detected or integrity violations found
    2 — Catalog file not found
    3 — Parser / schema error

Part of GRO-1624 — Universal Asset Indexer.
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path


# ── Data structures ────────────────────────────────────────────────


class AssetNode:
    """A single node in the asset dependency graph."""

    __slots__ = ("asset_id", "name", "asset_type", "file_path", "deps")

    def __init__(
        self,
        asset_id: str,
        name: str = "",
        asset_type: str = "unknown",
        file_path: str = "",
        deps: list[str] | None = None,
    ):
        self.asset_id = asset_id
        self.name = name or asset_id
        self.asset_type = asset_type
        self.file_path = file_path
        self.deps = deps or []


class Catalog:
    """In-memory representation of the full asset catalog."""

    def __init__(self):
        self.nodes: dict[str, AssetNode] = {}
        self.source_path: str | None = None

    def add_node(self, node: AssetNode) -> None:
        self.nodes[node.asset_id] = node

    def get(self, asset_id: str) -> AssetNode | None:
        return self.nodes.get(asset_id)

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return sum(len(n.deps) for n in self.nodes.values())


# ── Catalog loaders ────────────────────────────────────────────────


def load_catalog_from_json(path: str | Path) -> Catalog:
    """Load an asset catalog from a JSON file.

    Expected format:
    {
      "version": "1.0",
      "assets": [
        {
          "asset_id": "...",
          "name": "...",
          "type": "...",
          "file_path": "...",
          "dependencies": ["asset_id_1", "asset_id_2"]
        },
        ...
      ]
    }
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Catalog not found: {path}")

    raw = json.loads(path.read_text())
    catalog = Catalog()
    catalog.source_path = str(path)

    assets_raw = raw.get("assets", [])

    for entry in assets_raw:
        node = AssetNode(
            asset_id=entry["asset_id"],
            name=entry.get("name", ""),
            asset_type=entry.get("type", "unknown"),
            file_path=entry.get("file_path", ""),
            deps=entry.get("dependencies", entry.get("deps", [])),
        )
        catalog.add_node(node)

    return catalog


def discover_plugin_manifests(
    base_dir: str | Path = ".",
) -> list[Path]:
    """Discover plugin-manifest.yaml and dashboard/manifest.json files."""
    base = Path(base_dir)
    manifests: list[Path] = []

    # Plugin manifests
    for p in sorted(base.rglob("plugin-manifest.yaml")):
        manifests.append(p)

    # Dashboard manifests
    for p in sorted(base.rglob("**/dashboard/manifest.json")):
        manifests.append(p)

    return manifests


def build_catalog_from_plugins(
    base_dir: str | Path = ".",
) -> Catalog:
    """Build a catalog by scanning plugin manifests for dependency edges."""
    import yaml  # lazy import

    catalog = Catalog()
    edges: list[tuple[str, str]] = []

    manifests = discover_plugin_manifests(base_dir)

    for mpath in manifests:
        raw_text = mpath.read_text()
        if mpath.suffix in (".yaml", ".yml"):
            data = yaml.safe_load(raw_text)
        else:
            data = json.loads(raw_text)

        plugin_name = data.get("name", mpath.stem)

        # YAML manifests have a `dependencies.pip` list
        if "dependencies" in data:
            deps_conf = data["dependencies"]
            if isinstance(deps_conf, dict):
                pip_deps = deps_conf.get("pip", [])
            elif isinstance(deps_conf, list):
                pip_deps = deps_conf
            else:
                pip_deps = []
            for dep in pip_deps:
                edges.append((plugin_name, dep))

        # JSON manifests (dashboard) reference an `entry` JS file
        if "entry" in data and data.get("tab") is not None:
            entry_path = mpath.parent / data["entry"]
            if entry_path.exists():
                edge_id = f"file://{entry_path}"
                edges.append((plugin_name, edge_id))

    # Build catalog nodes
    for mpath in manifests:
        raw_text = mpath.read_text()
        if mpath.suffix in (".yaml", ".yml"):
            data = yaml.safe_load(raw_text)
        else:
            data = json.loads(raw_text)
        pname = data.get("name", mpath.stem)
        node = AssetNode(
            asset_id=pname,
            name=pname,
            asset_type="plugin",
            file_path=str(mpath),
        )
        catalog.add_node(node)

    # Add edges
    for src_id, dst_id in edges:
        src = catalog.get(src_id)
        if src is not None and dst_id not in src.deps:
            src.deps.append(dst_id)

    # Add external reference nodes so the graph is complete
    all_mentioned: set[str] = set()
    for node in catalog.nodes.values():
        all_mentioned.update(node.deps)
    for dep_id in all_mentioned:
        if catalog.get(dep_id) is None:
            catalog.add_node(
                AssetNode(
                    asset_id=dep_id,
                    name=dep_id,
                    asset_type="external",
                    file_path="",
                )
            )

    return catalog


def build_catalog(
    catalog_path: str | Path | None = None,
    base_dir: str | Path | None = None,
) -> Catalog:
    """Build a catalog from path or by auto-discovering plugins."""
    if catalog_path:
        return load_catalog_from_json(catalog_path)

    base = base_dir or os.getcwd()
    return build_catalog_from_plugins(base)


# ── Tarjan's SCC algorithm ──────────────────────────────────────────


class CycleDetector:
    """Tarjan's strongly connected components algorithm.

    Any SCC with more than one node (or a single node with a self-loop)
    represents a circular dependency.
    """

    def __init__(self, catalog: Catalog):
        self.catalog = catalog
        self._index_counter = 0
        self._stack: list[str] = []
        self._indices: dict[str, int] = {}
        self._lowlink: dict[str, int] = {}
        self._on_stack: set[str] = set()
        self._scc_storage: list[list[str]] = []
        self.cycles: list[list[str]] = []

    def detect(self) -> list[list[str]]:
        """Run Tarjan's SCC. Returns list of cycles."""
        self._scc_storage.clear()
        self._index_counter = 0
        self._stack.clear()
        self._indices.clear()
        self._lowlink.clear()
        self._on_stack.clear()

        for asset_id in list(self.catalog.nodes.keys()):
            if asset_id not in self._indices:
                self._strongconnect(asset_id)

        # Filter: SCCs of size > 1 are cycles; size == 1 with self-loop is cycle
        real_cycles: list[list[str]] = []
        for scc in self._scc_storage:
            if len(scc) > 1:
                real_cycles.append(scc)
            elif len(scc) == 1:
                node = self.catalog.get(scc[0])
                if node and scc[0] in node.deps:
                    real_cycles.append(scc)
        self.cycles = real_cycles
        return real_cycles

    def _strongconnect(self, v: str) -> None:
        self._indices[v] = self._index_counter
        self._lowlink[v] = self._index_counter
        self._index_counter += 1
        self._stack.append(v)
        self._on_stack.add(v)

        node = self.catalog.get(v)
        if node:
            for w in node.deps:
                if w not in self._indices:
                    self._strongconnect(w)
                    self._lowlink[v] = min(self._lowlink[v], self._lowlink[w])
                elif w in self._on_stack:
                    self._lowlink[v] = min(self._lowlink[v], self._indices[w])

        if self._lowlink[v] == self._indices[v]:
            scc: list[str] = []
            while True:
                w = self._stack.pop()
                self._on_stack.discard(w)
                scc.append(w)
                if w == v:
                    break
            self._scc_storage.append(scc)


def detect_cycles(catalog: Catalog) -> list[list[str]]:
    """Convenience wrapper — detect cycles via Tarjan's SCC."""
    detector = CycleDetector(catalog)
    return detector.detect()


# ── Referential integrity ────────────────────────────────────────────


class IntegrityReport:
    """Report of referential integrity violations."""

    def __init__(self):
        self.broken_links: list[dict] = []

    @property
    def has_violations(self) -> bool:
        return bool(self.broken_links)

    def to_dict(self) -> dict:
        return {"broken_links": self.broken_links}


def verify_integrity(catalog: Catalog) -> IntegrityReport:
    """Check that all inter-asset references resolve to known nodes."""
    report = IntegrityReport()

    for node in catalog.nodes.values():
        for dep_id in node.deps:
            if catalog.get(dep_id) is None:
                report.broken_links.append(
                    {
                        "source": node.asset_id,
                        "target": dep_id,
                        "source_file": node.file_path,
                    }
                )

    return report


# ── DAG closure index ────────────────────────────────────────────────


def build_closure_index(catalog: Catalog) -> dict:
    """Compute the DAG transitive closure index.

    Returns a dict mapping each asset_id to its full transitive
    dependency set (list of asset_ids).
    """
    closure: dict[str, list[str]] = {}
    for asset_id in catalog.nodes:
        visited: set[str] = set()
        queue = list(catalog.nodes[asset_id].deps)
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            current_node = catalog.get(current)
            if current_node:
                for dep in current_node.deps:
                    if dep not in visited:
                        queue.append(dep)
        closure[asset_id] = list(visited)
    return closure


# ── Multi-GPU adjacency stub ────────────────────────────────────────


def adjacency_matrix_exponentiation(
    catalog: Catalog, max_iterations: int = 100
) -> dict:
    """Stub for multi-GPU adjacency matrix exponentiation.

    This is an MVP placeholder. The real implementation would:
    - Build an N×N boolean adjacency matrix
    - Use torch.distributed to shard rows across GPUs
    - Compute path lengths via repeated squaring (matrix exponentiation)
    - Identify cycles by monitoring diagonal entries

    Current stub: returns transitive closure using CPU-based BFS.
    """
    _ = max_iterations
    return build_closure_index(catalog)


# ── Formatting helpers ──────────────────────────────────────────────


def format_cycle(cycle: list[str], catalog: Catalog) -> str:
    """Format a detected cycle as a readable string."""
    parts: list[str] = []
    for i, asset_id in enumerate(cycle):
        node = catalog.get(asset_id)
        path_hint = f" ({node.file_path})" if node and node.file_path else ""
        arrow = " → "
        parts.append(f"{asset_id}{path_hint}{arrow}")
    parts.append(cycle[0])  # close the cycle
    return "".join(parts)


def format_broken_link(link: dict) -> str:
    """Format a broken-link entry."""
    source = link["source"]
    target = link["target"]
    sf = link.get("source_file", "")
    hint = f" (in {sf})" if sf else ""
    return f"  {source}{hint} → MISSING: {target}"


# ── CLI ──────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Universal Asset Indexer — DAG cycle detector & integrity checker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --detect-cycles
  %(prog)s --catalog vault/asset_catalog.json --detect-cycles --verify-integrity
  %(prog)s --output reports/dag_index.json --detect-cycles
  %(prog)s --gpu-adj-exp
        """,
    )

    parser.add_argument(
        "--catalog",
        type=str,
        default=None,
        help="Path to asset_catalog.json (auto-discovers if omitted)",
    )
    parser.add_argument(
        "--base-dir",
        type=str,
        default=None,
        help="Base directory for plugin scanning (default: cwd)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Write DAG closure index to this path (JSON)",
    )
    parser.add_argument(
        "--detect-cycles",
        action="store_true",
        help="Run cycle detection; exit 1 if cycles found",
    )
    parser.add_argument(
        "--verify-integrity",
        action="store_true",
        help="Check referential integrity; exit 1 if broken links found",
    )
    parser.add_argument(
        "--gpu-adj-exp",
        action="store_true",
        help="Multi-GPU adjacency matrix exponentiation (MVP stub)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON to stdout",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print intermediate diagnostics",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # ── Load catalog ────────────────────────────────────────────────
    try:
        catalog = build_catalog(
            catalog_path=args.catalog,
            base_dir=args.base_dir,
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: Parse error — {exc}", file=sys.stderr)
        return 3

    if args.verbose:
        print(
            f"Catalog: {catalog.node_count} nodes, "
            f"{catalog.edge_count} edges"
            + (f" (from {catalog.source_path})" if catalog.source_path else "")
        )

    exit_code = 0
    results: dict = {
        "catalog": {
            "source": catalog.source_path or "auto-discovered",
            "node_count": catalog.node_count,
            "edge_count": catalog.edge_count,
        },
        "cycles": [],
        "integrity": None,
        "closure_index": None,
    }

    # ── Cycle detection ─────────────────────────────────────────────
    if args.detect_cycles:
        cycles = detect_cycles(catalog)
        results["cycles"] = [
            {
                "members": c,
                "display": format_cycle(c, catalog),
            }
            for c in cycles
        ]
        if cycles:
            exit_code = 1
            print(
                f"\u26a0  Detected {len(cycles)} circular "
                f"dependenc{'y' if len(cycles)==1 else 'ies'}:",
                file=sys.stderr,
            )
            for cycle in cycles:
                print(
                    f"  Cycle: {format_cycle(cycle, catalog)}",
                    file=sys.stderr,
                )
        else:
            print("✓ No circular dependencies detected.")

    # ── Integrity check ─────────────────────────────────────────────
    if args.verify_integrity:
        integrity = verify_integrity(catalog)
        results["integrity"] = integrity.to_dict()
        if integrity.has_violations:
            exit_code = 1
            print(
                f"\u26a0  Found {len(integrity.broken_links)} broken "
                f"reference{'s' if len(integrity.broken_links)!=1 else ''}:",
                file=sys.stderr,
            )
            for link in integrity.broken_links:
                print(format_broken_link(link), file=sys.stderr)
        else:
            print("✓ All references resolve (no broken links).")

    # ── DAG closure index ────────────────────────────────────────────
    if args.output:
        closure = build_closure_index(catalog)
        results["closure_index"] = closure
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(closure, indent=2))
        if args.verbose:
            print(f"Wrote closure index ({len(closure)} entries) to {out_path}")

    # ── GPU adjacency exponentiation (stub) ──────────────────────────
    if args.gpu_adj_exp:
        if args.verbose:
            print("Multi-GPU adjacency matrix exponentiation (MVP stub)...")
        closure = adjacency_matrix_exponentiation(catalog)
        results["gpu_adj_exp"] = {
            "status": "stub",
            "entries": len(closure),
        }
        if args.verbose:
            print("  Done (CPU-based transitive closure used).")

    # ── JSON output ──────────────────────────────────────────────────
    if args.json:
        print(json.dumps(results, indent=2))

    return exit_code


# ── Entry point ──────────────────────────────────────────────────────


if __name__ == "__main__":
    sys.exit(main())
