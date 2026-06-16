"""
prismatic/workspace_router.py — Centralized workspace router config and path resolver.

Parses multi-repo workspace topology from ``PRISMATIC_ENGINE.yaml`` (or any
YAML file with a ``swarm_workspaces`` section), resolves logical namespace
references (e.g. ``prismatic-engine:prismatic/lock.py``) to absolute
filesystem paths, and enforces path-boundary safety.

Usage::

    config = {"swarm_workspaces": {
        "prismatic-engine": {
            "path": "/home/ubuntu/work/prismatic-engine",
            "default_branch": "main",
        },
    }}
    router = WorkspaceRouter(config)
    path = router.resolve_absolute("prismatic-engine:prismatic/lock.py")
    # → Path("/home/ubuntu/work/prismatic-engine/prismatic/lock.py")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

logger = logging.getLogger("prismatic.workspace_router")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class WorkspaceViolationError(Exception):
    """Raised when a path resolution would escape the workspace boundary."""


@dataclass(frozen=True)
class RepoWorkspace:
    """Describes a single repository workspace known to the router.

    Attributes:
        name:        Short logical name (e.g. ``prismatic-engine``).
        path:        Absolute filesystem path to the checkout root.
        default_branch: Default git branch (e.g. ``main``).
        staging_branch: Staging/integration branch (e.g. ``deploy-fresh``).
        owner:       Optional lane-owner agent ID for workspace-level routing.
    """

    name: str
    path: Path
    default_branch: str = "main"
    staging_branch: str | None = None
    owner: str | None = None


# ---------------------------------------------------------------------------
# YAML schema validation helpers
# ---------------------------------------------------------------------------


_SCALAR_KEYS = {"path", "default_branch", "staging_branch", "owner"}


def _parse_repo_config(name: str, raw: object) -> RepoWorkspace:
    """Parse a single repo entry from a YAML dictionary.

    Raises ``TypeError`` on invalid or missing data.
    """
    if not isinstance(raw, dict):
        raise TypeError(
            f"Workspace entry {name!r} must be a mapping, "
            f"got {type(raw).__name__}"
        )
    path_raw = raw.get("path")
    if not path_raw or not isinstance(path_raw, str):
        raise TypeError(
            f"Workspace {name!r} requires a 'path' string, "
            f"got {type(path_raw).__name__}: {path_raw!r}"
        )
    return RepoWorkspace(
        name=name,
        path=Path(path_raw).resolve(),
        default_branch=str(raw.get("default_branch", "main")),
        staging_branch=raw.get("staging_branch"),
        owner=raw.get("owner"),
    )


# ---------------------------------------------------------------------------
# Namespace parser
# ---------------------------------------------------------------------------


def parse_namespace(ref: str) -> tuple[str, str]:
    """Split a logical namespace reference into *(repo_name, relative_path)*.

    The expected format is ``<repo_name>:<relative_path>``.

    Examples::

        parse_namespace("prismatic-engine:prismatic/lock.py")
        # → ("prismatic-engine", "prismatic/lock.py")

        parse_namespace("hd-engine:api/core.go")
        # → ("hd-engine", "api/core.go")

    Raises ``ValueError`` if the reference does not contain exactly one ``:``
    or if either part is empty.
    """
    if ref.count(":") != 1:
        raise ValueError(
            f"Namespace reference must contain exactly one ':', got {ref!r}"
        )
    repo_name, rel_path = ref.split(":", 1)
    if not repo_name.strip():
        raise ValueError(f"Empty repo name in namespace reference {ref!r}")
    if not rel_path.strip():
        raise ValueError(f"Empty relative path in namespace reference {ref!r}")
    return repo_name.strip(), rel_path.strip()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class WorkspaceRouter:
    """Central workspace router for multi-repo swarm coordination.

    Loads workspace topology from a parsed YAML config dict and provides
    methods for path resolution, namespace parsing, and repo detection.

    The config dict is expected to be the **already-loaded** YAML content
    (e.g. from ``yaml.safe_load``) containing a ``swarm_workspaces`` key
    mapping repo names to their config dictionaries.

    Example config structure::

        {'swarm_workspaces': {
            'prismatic-engine': {
                'path': '/home/ubuntu/work/prismatic-engine',
                'default_branch': 'main',
                'staging_branch': 'deploy-fresh',
            },
            'active-oahu-static': {
                'path': '/home/ubuntu/work/active-oahu-static',
                'default_branch': 'main',
            },
        }}

    Thread safety: the router is **immutable after construction** — all state
    is built during ``__init__`` and never mutated.
    """

    def __init__(self, config: dict) -> None:
        self._workspaces: dict[str, RepoWorkspace] = {}
        self._load(config)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self, config: dict) -> None:
        raw_workspaces = config.get("swarm_workspaces", {})
        if not isinstance(raw_workspaces, dict):
            raise TypeError(
                f"'swarm_workspaces' must be a mapping, "
                f"got {type(raw_workspaces).__name__}"
            )
        workspaces: dict[str, RepoWorkspace] = {}
        for name, raw in raw_workspaces.items():
            ws = _parse_repo_config(str(name), raw)
            workspaces[ws.name] = ws
        self._workspaces = workspaces
        logger.info(
            "WorkspaceRouter loaded %d workspace(s): %s",
            len(workspaces),
            sorted(workspaces),
        )

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def workspace_names(self) -> list[str]:
        """Return sorted list of registered workspace names."""
        return sorted(self._workspaces)

    @property
    def workspaces(self) -> list[RepoWorkspace]:
        """Return all registered workspaces."""
        return list(self._workspaces.values())

    def __len__(self) -> int:
        return len(self._workspaces)

    def __contains__(self, name: str) -> bool:
        return name in self._workspaces

    def __iter__(self) -> Iterator[RepoWorkspace]:
        return iter(self._workspaces.values())

    def get_repo(self, name: str) -> RepoWorkspace | None:
        """Look up a ``RepoWorkspace`` by its logical name."""
        return self._workspaces.get(name)

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    def get_repo_path(self, repo_name: str) -> Path:
        """Return the absolute filesystem path for a named repository.

        Raises ``KeyError`` if *repo_name* is not registered.
        """
        ws = self._workspaces.get(repo_name)
        if ws is None:
            raise KeyError(
                f"Unknown workspace {repo_name!r}. "
                f"Registered: {sorted(self._workspaces)}"
            )
        return ws.path

    def resolve_absolute(self, namespace_ref: str) -> Path:
        """Resolve a logical namespace reference to an absolute path.

        The reference format is ``<repo_name>:<relative/path>``.

        Raises ``ValueError`` for malformed references, ``KeyError`` for
        unknown repo names, and ``WorkspaceViolationError`` if the resolved
        path would escape the workspace root boundary (path traversal).
        """
        repo_name, rel_path = parse_namespace(namespace_ref)
        ws = self._workspaces.get(repo_name)
        if ws is None:
            raise KeyError(
                f"Unknown workspace {repo_name!r}. "
                f"Registered: {sorted(self._workspaces)}"
            )
        resolved = ws.path.joinpath(rel_path).resolve()

        # Path traversal guard
        if not self._is_within(resolved, ws.path):
            raise WorkspaceViolationError(
                f"Resolved path {resolved} escapes workspace boundary "
                f"{ws.path} for repo {repo_name!r}"
            )
        return resolved

    def detect_repo_from_path(self, absolute_path: str | Path) -> tuple[str, Path]:
        """Detect which workspace a given absolute path belongs to.

        Returns a ``(repo_name, relative_path)`` tuple, where
        *relative_path* is the path component **after** the workspace root.

        If multiple workspaces match, the **longest matching prefix** wins
        (most specific workspace).

        Raises ``WorkspaceViolationError`` if no workspace contains the path.
        """
        resolved = Path(absolute_path).resolve()

        best_name: str | None = None
        best_len = 0

        for name, ws in self._workspaces.items():
            try:
                rel = resolved.relative_to(ws.path)
            except ValueError:
                continue  # not under this workspace
            segments = len(ws.path.parts)
            if segments > best_len:
                best_name = name
                best_len = segments

        if best_name is None:
            raise WorkspaceViolationError(
                f"Path {absolute_path} does not belong to any registered "
                f"workspace. Roots: {sorted(p.path for p in self._workspaces.values())}"
            )
        ws = self._workspaces[best_name]
        rel = resolved.relative_to(ws.path)
        return best_name, rel

    # ------------------------------------------------------------------
    # YAML-loading convenience
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, yaml_path: str | Path) -> WorkspaceRouter:
        """Load workspace configuration from a YAML file.

        The YAML must contain a ``swarm_workspaces`` top-level key.
        """
        import yaml  # defer import — optional dependency

        with open(yaml_path) as f:
            config = yaml.safe_load(f)
        if config is None:
            config = {}
        return cls(config)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_within(child: Path, parent: Path) -> bool:
        """Return ``True`` if *child* is a sub-path of (or equal to) *parent*."""
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False
