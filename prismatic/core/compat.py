"""
Version Compatibility Resolver — Semantic version conflict detection and
constraint solver for the Prismatic plugin ecosystem.

Extends the basic ``SpecifierSet`` check in ``PluginLoader`` with:

1. **Transitive dependency traversal** — checks a plugin's declared plugin
   dependencies against the core engine version and each other.
2. **Cross-plugin conflict detection** — identifies plugins whose version
   constraints on the same dependency are mutually exclusive.
3. **Resolution report** — summarises which plugins can / cannot be loaded
   together and why.
4. **Compatibility matrix** — a JSON-serialisable matrix that the marketplace
   UI or CLI can consume.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Set, Tuple

from packaging.specifiers import SpecifierSet, InvalidSpecifier
from packaging.version import Version, InvalidVersion

logger = logging.getLogger("prismatic.compat")


# ── Public data models ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class PluginVersionInfo:
    """Normalised version metadata extracted from a ``plugin-manifest.yaml``.

    Parameters
    ----------
    name:
        Unique plugin identifier (e.g. ``"vram-observability"``).
    version:
        The plugin's own SemVer version string.
    core_version_constraint:
        SemVer range the plugin requires of the core engine
        (e.g. ``">=2.0.0, <3.0.0"``).
    plugin_dependencies:
        Map of plugin name → required SemVer constraint that *other*
        plugins must satisfy (e.g. ``{"data-lake": ">=1.0.0"}``).
    """

    name: str
    version: str
    core_version_constraint: str
    plugin_dependencies: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ConflictInfo:
    """Describes a version conflict between two plugins.

    Parameters
    ----------
    plugin_a:
        The first plugin involved in the conflict.
    constraint_a:
        The constraint that *plugin_a* imposes.
    plugin_b:
        The second plugin involved.
    constraint_b:
        The constraint that *plugin_b* imposes (or the version of
        *plugin_b* if it *is* the dependency).
    dependency_name:
        The shared dependency at the centre of the conflict.
    reason:
        Human-readable explanation.
    """

    plugin_a: str
    constraint_a: str
    plugin_b: str
    constraint_b: str
    dependency_name: str
    reason: str


@dataclass(frozen=True)
class ResolutionResult:
    """Outcome for a single plugin after resolution.

    Parameters
    ----------
    plugin:
        Plugin identifier.
    version:
        Plugin's own version.
    compatible:
        Whether the plugin can be loaded given the current engine and
        peer plugins.
    core_compatible:
        Whether the plugin's core version constraint is satisfied.
    transitive_deps_ok:
        Whether all transitive plugin dependencies are satisfied.
    peer_conflicts:
        List of conflicts with other plugins being loaded.
    blocking_issues:
        Human-readable list of reasons this plugin cannot be loaded.
    """

    plugin: str
    version: str
    compatible: bool
    core_compatible: bool = True
    transitive_deps_ok: bool = True
    peer_conflicts: List[ConflictInfo] = field(default_factory=list)
    blocking_issues: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class CompatibilityMatrix:
    """Pairwise plugin compatibility matrix for marketplace UI consumption.

    ``rows`` and ``columns`` are ordered identically so the matrix can be
    rendered as a 2-D grid where ``cells[i][j]`` corresponds to the
    compatibility of ``rows[i]`` with ``columns[j]``.

    Each cell is ``"ok"``, ``"conflict"``, or ``"same"`` (diagonal).
    """

    rows: List[str] = field(default_factory=list)
    columns: List[str] = field(default_factory=list)
    cells: List[List[str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Return a plain dict suitable for JSON serialisation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CompatibilityMatrix:
        """Reconstruct from a dict produced by ``to_dict``."""
        return cls(**data)


@dataclass(frozen=True)
class ResolutionReport:
    """Complete resolution result for a set of plugins.

    Parameters
    ----------
    core_version:
        The core engine version used for resolution.
    results:
        Per-plugin resolution results.
    global_conflicts:
        Cross-plugin conflicts that prevent loading the set together.
    summary:
        Short human-readable summary.
    matrix:
        Pairwise compatibility matrix (for marketplace UI).
    """

    core_version: str
    results: List[ResolutionResult] = field(default_factory=list)
    global_conflicts: List[ConflictInfo] = field(default_factory=list)
    summary: str = ""
    matrix: Optional[CompatibilityMatrix] = None


# ── Helper utilities ────────────────────────────────────────────────────────


def _specs_overlap(spec_a: SpecifierSet, spec_b: SpecifierSet) -> bool:
    """Check if two SpecifierSets have any version in common.

    Uses candidate version sampling at boundary points.  Returns
    ``True`` if at least one version satisfies both sets.
    """
    combined = spec_a & spec_b
    # Extract the boundary version from each individual specifier.
    # If any boundary version satisfies the entire combined set there is
    # at least one overlapping version.
    candidates: List[str] = []
    for spec in combined:
        # spec is a Specifier — its version string is the boundary
        candidates.append(str(spec.version))
        # Also try one patch version above and below the boundary
        try:
            v = Version(str(spec.version))
            major, minor, *_ = v.release
            micro = v.micro if hasattr(v, "micro") else 0
            # Try nearby versions (±2 patch levels)
            for delta in (-2, -1, 1, 2):
                nv = micro + delta
                if nv >= 0:
                    candidates.append(f"{major}.{minor}.{nv}")
        except Exception:
            pass

    # Also try a few general candidates
    candidates.extend(["0.0.1", "0.1.0", "0.5.0", "1.0.0", "1.5.0",
                       "2.0.0", "2.5.0", "3.0.0", "5.0.0", "10.0.0"])

    for c in candidates:
        try:
            if Version(c) in combined:
                return True
        except InvalidVersion:
            continue
    return False


def _parse_constraint(constraint_str: str) -> Optional[SpecifierSet]:
    """Safely parse a version constraint string.

    Returns ``None`` if the string is empty, ``"*"``, or unparseable.
    """
    if not constraint_str or constraint_str.strip() in ("", "*"):
        return None
    try:
        return SpecifierSet(constraint_str)
    except InvalidSpecifier:
        logger.warning("Unable to parse constraint: %s", constraint_str)
        return None


def _compatible(
    version_str: str, constraint_str: str, label: str = "unknown"
) -> bool:
    """Check if *version_str* satisfies *constraint_str*.

    A missing or ``"*"`` constraint is treated as compatible.
    """
    spec = _parse_constraint(constraint_str)
    if spec is None:
        return True
    try:
        return Version(version_str) in spec
    except InvalidVersion:
        logger.warning("Invalid version '%s' for %s", version_str, label)
        return False


# ── VersionResolver ──────────────────────────────────────────────────────────


class VersionResolver:
    """Resolves plugin version compatibility for the Prismatic Engine.

    Usage
    -----
    >>> resolver = VersionResolver()
    >>> plugins = [
    ...     PluginVersionInfo(name="a", version="1.0.0",
    ...                       core_version_constraint=">=1.0.0",
    ...                       plugin_dependencies={"b": ">=1.0.0"}),
    ...     PluginVersionInfo(name="b", version="2.0.0",
    ...                       core_version_constraint=">=1.0.0"),
    ... ]
    >>> report = resolver.resolve(plugins, core_version="1.5.0")
    """

    # ── public API ──────────────────────────────────────────────────────────

    def resolve(
        self,
        plugins: List[PluginVersionInfo],
        core_version: str,
    ) -> ResolutionReport:
        """Run full resolution across all *plugins* for *core_version*.

        1. Check each plugin's core version constraint.
        2. Traverse transitive plugin dependencies.
        3. Detect cross-plugin version conflicts.
        4. Build a compatibility matrix.
        5. Produce a summary.
        """
        results: List[ResolutionResult] = []
        all_conflicts: List[ConflictInfo] = []
        plugin_map = {p.name: p for p in plugins}

        for plugin in plugins:
            result = self._resolve_single(plugin, plugin_map, core_version)
            results.append(result)
            all_conflicts.extend(result.peer_conflicts)

        # Deduplicate global conflicts
        seen: Set[str] = set()
        unique_conflicts: List[ConflictInfo] = []
        for c in all_conflicts:
            key = f"{sorted([c.plugin_a, c.plugin_b])[0]}::{sorted([c.plugin_a, c.plugin_b])[1]}::{c.dependency_name}"
            if key not in seen:
                seen.add(key)
                unique_conflicts.append(c)

        matrix = self._build_matrix(plugins, plugin_map, core_version)
        summary = self._summarise(results, unique_conflicts)

        return ResolutionReport(
            core_version=core_version,
            results=results,
            global_conflicts=unique_conflicts,
            summary=summary,
            matrix=matrix,
        )

    def build_compatibility_matrix(
        self,
        plugins: List[PluginVersionInfo],
        core_version: str,
    ) -> CompatibilityMatrix:
        """Return a pairwise compatibility matrix without a full resolution.

        Convenience wrapper around :meth:`_build_matrix`.
        """
        plugin_map = {p.name: p for p in plugins}
        return self._build_matrix(plugins, plugin_map, core_version)

    # ── core check helpers (also used by PluginLoader) ──────────────────────

    @staticmethod
    def check_core_compatibility(
        plugin: PluginVersionInfo, core_version: str
    ) -> bool:
        """Check a single plugin's ``core_version_constraint``.

        This is the logic-level equivalent of the existing
        ``SpecifierSet`` check in ``PluginLoader._load_plugin``.
        """
        return _compatible(
            core_version,
            plugin.core_version_constraint,
            f"plugin {plugin.name} / core constraint",
        )

    @staticmethod
    def check_transitive_deps(
        plugin: PluginVersionInfo,
        all_plugins: Dict[str, PluginVersionInfo],
        core_version: str,
    ) -> List[str]:
        """Check all transitive plugin dependencies of *plugin*.

        Returns a list of human-readable issues. An empty list means
        everything is satisfied.
        """
        issues: List[str] = []
        visited: Set[str] = set()

        def _traverse(name: str, constraint: str, chain: List[str]) -> None:
            if name in visited:
                issues.append(
                    f"Circular dependency detected: {' -> '.join(chain + [name])}"
                )
                return
            visited.add(name)

            dep = all_plugins.get(name)
            if dep is None:
                issues.append(
                    f"Missing plugin dependency '{name}' "
                    f"(required {constraint} by "
                    f"{' -> '.join(chain)})"
                )
                return

            # Check that the dependency's version satisfies the constraint
            if not _compatible(dep.version, constraint, name):
                issues.append(
                    f"Plugin '{name}' version {dep.version} does not "
                    f"satisfy constraint {constraint} required by "
                    f"{' -> '.join(chain)}"
                )

            # Check that the dependency itself is core-compatible
            if not _compatible(
                core_version, dep.core_version_constraint, name
            ):
                issues.append(
                    f"Transitive dependency '{name}' requires core "
                    f"{dep.core_version_constraint} but core is "
                    f"{core_version}"
                )

            # Recurse into sub-dependencies
            for sub_name, sub_constraint in dep.plugin_dependencies.items():
                _traverse(sub_name, sub_constraint, chain + [name])

        for dep_name, dep_constraint in plugin.plugin_dependencies.items():
            _traverse(dep_name, dep_constraint, [plugin.name])

        return issues

    @staticmethod
    def detect_conflicts(
        plugins: List[PluginVersionInfo],
    ) -> List[ConflictInfo]:
        """Detect conflicting version requirements between *plugins*.

        Two plugins conflict when they require different (non-overlapping)
        versions of the same shared dependency.
        """
        conflicts: List[ConflictInfo] = []
        plugin_map = {p.name: p for p in plugins}

        # Build a map: dependency_name -> [(requirer, constraint)]
        dep_requirements: Dict[str, List[Tuple[str, str]]] = {}
        for plugin in plugins:
            for dep_name, constraint in plugin.plugin_dependencies.items():
                dep_requirements.setdefault(dep_name, []).append(
                    (plugin.name, constraint)
                )

        # Also add the actual plugin version as an implicit constraint
        # for any plugin that *is* a dependency of another
        for plugin in plugins:
            if plugin.name in dep_requirements:
                dep_requirements[plugin.name].append(
                    (f"{plugin.name} (self)", f"=={plugin.version}")
                )

        for dep_name, requirements in dep_requirements.items():
            # Check pairwise: do any two requirements conflict?
            for i, (req_a, constr_a) in enumerate(requirements):
                spec_a = _parse_constraint(constr_a)
                if spec_a is None:
                    continue
                for j, (req_b, constr_b) in enumerate(requirements):
                    if j <= i:
                        continue
                    spec_b = _parse_constraint(constr_b)
                    if spec_b is None:
                        continue

                    # If the specifiers have no overlapping versions, conflict
                    if not _specs_overlap(spec_a, spec_b):
                        dep_plugin = plugin_map.get(dep_name)
                        dep_ver = (
                            dep_plugin.version if dep_plugin else "unknown"
                        )
                        conflicts.append(
                            ConflictInfo(
                                plugin_a=req_a,
                                constraint_a=constr_a,
                                plugin_b=req_b,
                                constraint_b=constr_b,
                                dependency_name=dep_name,
                                reason=(
                                    f"'{req_a}' requires {dep_name} "
                                    f"{constr_a} but '{req_b}' requires "
                                    f"{dep_name} {constr_b} "
                                    f"(dep is at version {dep_ver})"
                                ),
                            )
                        )

        return conflicts

    # ── internal ─────────────────────────────────────────────────────────────

    def _resolve_single(
        self,
        plugin: PluginVersionInfo,
        plugin_map: Dict[str, PluginVersionInfo],
        core_version: str,
    ) -> ResolutionResult:
        """Resolve a single plugin against the engine and peer set."""
        blocking: List[str] = []
        core_ok = self.check_core_compatibility(plugin, core_version)
        if not core_ok:
            blocking.append(
                f"Core version {core_version} does not satisfy "
                f"constraint {plugin.core_version_constraint}"
            )

        transitive_issues = self.check_transitive_deps(
            plugin, plugin_map, core_version
        )
        deps_ok = len(transitive_issues) == 0
        blocking.extend(transitive_issues)

        # Detect conflicts *this* plugin participates in
        peer_conflicts = [
            c
            for c in self.detect_conflicts(list(plugin_map.values()))
            if c.plugin_a == plugin.name or c.plugin_b == plugin.name
        ]
        blocking.extend(c.reason for c in peer_conflicts)

        compatible = core_ok and deps_ok and len(peer_conflicts) == 0

        return ResolutionResult(
            plugin=plugin.name,
            version=plugin.version,
            compatible=compatible,
            core_compatible=core_ok,
            transitive_deps_ok=deps_ok,
            peer_conflicts=peer_conflicts,
            blocking_issues=blocking,
        )

    def _build_matrix(
        self,
        plugins: List[PluginVersionInfo],
        plugin_map: Dict[str, PluginVersionInfo],
        core_version: str,
    ) -> CompatibilityMatrix:
        """Build a pairwise compatibility matrix."""
        names = sorted(p.name for p in plugins)
        n = len(names)
        cells: List[List[str]] = [
            ["ok"] * n for _ in range(n)
        ]

        for i in range(n):
            for j in range(n):
                if i == j:
                    cells[i][j] = "same"
                elif i > j:
                    # Mirror — only compute upper triangle
                    cells[i][j] = cells[j][i]
                else:
                    a = plugin_map[names[i]]
                    b = plugin_map[names[j]]

                    # Check if a's deps conflict with b's version
                    dep_ok = True
                    for dep_name, constraint in a.plugin_dependencies.items():
                        if dep_name == b.name:
                            if not _compatible(b.version, constraint, b.name):
                                dep_ok = False
                                break

                    # And vice versa
                    if dep_ok:
                        for dep_name, constraint in (
                            b.plugin_dependencies.items()
                        ):
                            if dep_name == a.name:
                                if not _compatible(
                                    a.version, constraint, a.name
                                ):
                                    dep_ok = False
                                    break

                    # Also check both are core-compatible
                    a_core = self.check_core_compatibility(a, core_version)
                    b_core = self.check_core_compatibility(b, core_version)

                    cells[i][j] = "ok" if (dep_ok and a_core and b_core) else "conflict"

        return CompatibilityMatrix(rows=names, columns=names, cells=cells)

    @staticmethod
    def _summarise(
        results: List[ResolutionResult],
        conflicts: List[ConflictInfo],
    ) -> str:
        """Produce a human-readable summary of the resolution."""
        total = len(results)
        compatible = sum(1 for r in results if r.compatible)
        blocked = total - compatible

        lines: List[str] = []
        lines.append(
            f"Version compatibility resolution for {total} plugin(s): "
            f"{compatible} compatible, {blocked} blocked."
        )

        if blocked:
            lines.append("")
            lines.append("Blocked plugins:")
            for r in results:
                if not r.compatible:
                    for issue in r.blocking_issues:
                        lines.append(f"  - {r.plugin}: {issue}")

        if conflicts:
            lines.append("")
            lines.append("Cross-plugin conflicts:")
            for c in conflicts:
                lines.append(f"  - {c.reason}")

        return "\n".join(lines)
