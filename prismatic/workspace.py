"""
prismatic/workspace.py - Generic WorkspaceRegistry mapping workspaces to
Linear projects, GitHub repos, and local filesystem paths.

Extracted from the Hermes workspace_registry pattern, made fully generic
with configurable YAML loading (no hardcoded paths).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Workspace dataclass
# ---------------------------------------------------------------------------

@dataclass
class Workspace:
    """A workspace represents a named environment with associated metadata,
    filesystem paths, version-control repos, and Linear integration.
    """

    id: str
    name: str
    description: str = ""

    # Filesystem paths
    nas_path: str = ""
    media_path: str = ""
    local_paths: list[str] = field(default_factory=list)

    # Version-control / project references
    github_repos: list[str] = field(default_factory=list)
    linear_projects: list[str] = field(default_factory=list)

    # Agent profiles
    default_profile: str = "default"
    allowed_profiles: list[str] = field(default_factory=lambda: ["default"])

    # Arbitrary tags
    tags: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Workspace:
        """Construct a ``Workspace`` from a dictionary (typically loaded from
        YAML).  Unknown keys are silently ignored."""
        safe_keys = {
            "id", "name", "description", "nas_path", "media_path",
            "local_paths", "github_repos", "linear_projects",
            "default_profile", "allowed_profiles", "tags",
        }
        filtered = {k: v for k, v in data.items() if k in safe_keys}
        return cls(**filtered)


# ---------------------------------------------------------------------------
# WorkspaceRegistry
# ---------------------------------------------------------------------------

class WorkspaceRegistry:
    """Lazy-loaded registry of workspaces, indexed by id, Linear project name,
    GitHub repo name, and local filesystem path.

    Usage::

        reg = WorkspaceRegistry()
        reg.load("/path/to/workspaces.yaml")
        ws = reg.get_by_id("my-workspace")
    """

    def __init__(self):
        self._workspaces: list[Workspace] = []
        self._loaded = False
        self._lock = threading.Lock()

        # Indices
        self._by_id: dict[str, Workspace] = {}
        self._by_linear_project: dict[str, Workspace] = {}
        self._by_github_repo: dict[str, Workspace] = {}
        self._by_local_path: dict[str, Workspace] = {}

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self, yaml_path: str | Path) -> None:
        """Load workspaces from a YAML file.

        The YAML is expected to be a list of workspace dictionaries, or a
        top-level mapping with a ``workspaces`` key containing the list.
        """
        with open(yaml_path) as f:
            raw = yaml.safe_load(f)

        if raw is None:
            raw = []

        # Support both ``{"workspaces": [...]}`` and a bare ``[...]``.
        if isinstance(raw, dict):
            raw = raw.get("workspaces", [])

        if not isinstance(raw, list):
            raise TypeError(
                f"Expected YAML root to be a list or dict with 'workspaces' key, "
                f"got {type(raw).__name__}"
            )

        workspaces = [Workspace.from_dict(item) for item in raw]

        with self._lock:
            self._workspaces = workspaces
            self._rebuild_indices()
            self._loaded = True

    def _rebuild_indices(self) -> None:
        """Rebuild all lookup indices from ``self._workspaces``."""
        self._by_id.clear()
        self._by_linear_project.clear()
        self._by_github_repo.clear()
        self._by_local_path.clear()

        for ws in self._workspaces:
            self._by_id[ws.id] = ws
            for proj in ws.linear_projects:
                self._by_linear_project[proj] = ws
            for repo in ws.github_repos:
                self._by_github_repo[repo] = ws
            for path in ws.local_paths:
                self._by_local_path[path] = ws

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def workspaces(self) -> list[Workspace]:
        """Return the full list of loaded workspaces."""
        return list(self._workspaces)

    def get_by_id(self, workspace_id: str) -> Workspace | None:
        """Look up a workspace by its unique ``id``."""
        return self._by_id.get(workspace_id)

    def get_by_linear_project(self, project_name: str) -> Workspace | None:
        """Look up a workspace by Linear project name."""
        return self._by_linear_project.get(project_name)

    def get_by_github_repo(self, repo_name: str) -> Workspace | None:
        """Look up a workspace by GitHub repository name (e.g. ``org/repo``)."""
        return self._by_github_repo.get(repo_name)

    def get_by_local_path(self, path: str) -> Workspace | None:
        """Look up a workspace by a local filesystem path."""
        return self._by_local_path.get(path)

    # ------------------------------------------------------------------
    # Resolution helpers
    # ------------------------------------------------------------------

    def resolve_workspace_for_issue(self, issue_dict: dict[str, Any]) -> Workspace | None:
        """Try to resolve a workspace for a Linear issue dict.

        Heuristics used (in order):
        1. Exact match on ``issue_dict["project"]["name"]`` via
           ``get_by_linear_project``.
        2. Description keyword matching against workspace ``name``,
           ``description``, and ``tags``.
        """
        if not self._loaded:
            return None

        # Heuristic 1: exact project name match
        project = issue_dict.get("project")
        if isinstance(project, dict):
            project_name = project.get("name")
            if project_name:
                ws = self.get_by_linear_project(project_name)
                if ws:
                    return ws

        # Heuristic 2: description keyword matching
        description = (issue_dict.get("description") or "").lower()
        title = (issue_dict.get("title") or "").lower()
        combined_text = f"{title} {description}"

        best: tuple[int, Workspace | None] = (0, None)
        for ws in self._workspaces:
            score = 0
            for kw in [ws.name.lower(), ws.description.lower()] + [t.lower() for t in ws.tags]:
                if kw and kw in combined_text:
                    score += 1
            if score > best[0]:
                best = (score, ws)

        return best[1]

    def get_workspace_context(self, workspace: Workspace) -> dict[str, Any]:
        """Return a compact dictionary summarising a workspace for prompt
        context / injection."""
        return {
            "id": workspace.id,
            "name": workspace.name,
            "description": workspace.description,
            "nas_path": workspace.nas_path,
            "media_path": workspace.media_path,
            "local_paths": workspace.local_paths,
            "github_repos": workspace.github_repos,
            "linear_projects": workspace.linear_projects,
            "default_profile": workspace.default_profile,
            "allowed_profiles": workspace.allowed_profiles,
            "tags": workspace.tags,
        }


# ---------------------------------------------------------------------------
# Singleton helpers
# ---------------------------------------------------------------------------

_global_registry: WorkspaceRegistry | None = None
_global_registry_lock = threading.Lock()


def get_registry(yaml_path: str | Path | None = None) -> WorkspaceRegistry:
    """Return a shared singleton ``WorkspaceRegistry``.

    If *yaml_path* is provided **and** the registry hasn't been loaded yet,
    it will be loaded automatically.
    """
    global _global_registry
    if _global_registry is None:
        with _global_registry_lock:
            if _global_registry is None:
                _global_registry = WorkspaceRegistry()
                if yaml_path is not None:
                    _global_registry.load(yaml_path)
    elif yaml_path is not None and not _global_registry._loaded:
        _global_registry.load(yaml_path)
    return _global_registry


def resolve_workspace_for_issue(
    issue_dict: dict[str, Any],
    yaml_path: str | Path | None = None,
) -> Workspace | None:
    """Convenience function: resolve a workspace for the given Linear issue
    using the global registry."""
    reg = get_registry(yaml_path=yaml_path)
    return reg.resolve_workspace_for_issue(issue_dict)
