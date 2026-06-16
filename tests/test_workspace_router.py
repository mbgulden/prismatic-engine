"""
tests/test_workspace_router.py — Unit tests for prismatic/workspace_router.py

Covers:
- parse_namespace — valid, edge-case, and malformed references
- WorkspaceRouter construction from dict and YAML
- get_repo_path — success, unknown repo
- resolve_absolute — success, path traversal guard, unknown repo
- detect_repo_from_path — exact match, prefix match, unmatched
- WorkspaceViolationError on boundary escape
"""

import json
import os
import tempfile
from pathlib import Path

import pytest
import yaml

from prismatic.workspace_router import (
    RepoWorkspace,
    WorkspaceRouter,
    WorkspaceViolationError,
    parse_namespace,
)


# ═══════════════════════════════════════════════════════════════════
# parse_namespace
# ═══════════════════════════════════════════════════════════════════


class TestParseNamespace:
    def test_simple(self) -> None:
        assert parse_namespace("repo:path/to/file.py") == (
            "repo",
            "path/to/file.py",
        )

    def test_with_dots(self) -> None:
        assert parse_namespace("hd-engine:api/core.go") == (
            "hd-engine",
            "api/core.go",
        )

    def test_deep_nested(self) -> None:
        assert parse_namespace("a:b/c/d/e.py") == ("a", "b/c/d/e.py")

    def test_strips_whitespace(self) -> None:
        assert parse_namespace("  repo :  path/file  ") == (
            "repo",
            "path/file",
        )

    # --- Edge cases ---

    def test_too_many_colons(self) -> None:
        with pytest.raises(ValueError, match="exactly one ':'"):
            parse_namespace("a:b:c")

    def test_no_colon(self) -> None:
        with pytest.raises(ValueError, match="exactly one ':'"):
            parse_namespace("just-a-string")

    def test_empty_repo_name(self) -> None:
        with pytest.raises(ValueError, match="Empty repo name"):
            parse_namespace(":path/file")

    def test_empty_path(self) -> None:
        with pytest.raises(ValueError, match="Empty relative path"):
            parse_namespace("repo:")


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def sample_config() -> dict:
    return {
        "swarm_workspaces": {
            "prismatic-engine": {
                "path": "/home/ubuntu/work/prismatic-engine",
                "default_branch": "main",
                "staging_branch": "deploy-fresh",
                "owner": "ned",
            },
            "active-oahu-static": {
                "path": "/home/ubuntu/work/active-oahu-static",
                "default_branch": "main",
            },
            "hd-engine": {
                "path": "/home/ubuntu/work/hd-engine",
                "default_branch": "master",
            },
        }
    }


@pytest.fixture
def router(sample_config: dict) -> WorkspaceRouter:
    return WorkspaceRouter(sample_config)


# ═══════════════════════════════════════════════════════════════════
# Construction
# ═══════════════════════════════════════════════════════════════════


class TestWorkspaceRouterConstruction:
    def test_empty_config(self) -> None:
        router = WorkspaceRouter({})
        assert len(router) == 0
        assert router.workspace_names == []

    def test_with_swarm_workspaces(self, sample_config: dict) -> None:
        router = WorkspaceRouter(sample_config)
        assert len(router) == 3
        assert router.workspace_names == [
            "active-oahu-static",
            "hd-engine",
            "prismatic-engine",
        ]

    def test_swarm_workspaces_not_a_dict(self) -> None:
        with pytest.raises(TypeError, match="must be a mapping"):
            WorkspaceRouter({"swarm_workspaces": "not-a-dict"})

    def test_contains(self, router: WorkspaceRouter) -> None:
        assert "prismatic-engine" in router
        assert "active-oahu-static" in router
        assert "nonexistent" not in router

    def test_iteration(self, router: WorkspaceRouter) -> None:
        names = [ws.name for ws in router]
        assert sorted(names) == [
            "active-oahu-static",
            "hd-engine",
            "prismatic-engine",
        ]

    def test_from_yaml(self, sample_config: dict) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump(sample_config, f)
            tmp_path = f.name
        try:
            router = WorkspaceRouter.from_yaml(tmp_path)
            assert len(router) == 3
            assert "prismatic-engine" in router
        finally:
            os.unlink(tmp_path)

    def test_from_yaml_empty_file(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            tmp_path = f.name
        try:
            router = WorkspaceRouter.from_yaml(tmp_path)
            assert len(router) == 0
        finally:
            os.unlink(tmp_path)

    def test_missing_path_field(self) -> None:
        with pytest.raises(TypeError, match="requires a 'path' string"):
            WorkspaceRouter({"swarm_workspaces": {"my-repo": {}}})

    def test_invalid_entry_type(self) -> None:
        with pytest.raises(TypeError, match="must be a mapping"):
            WorkspaceRouter({"swarm_workspaces": {"my-repo": "just-a-string"}})

    def test_repo_workspace_dataclass(self) -> None:
        ws = RepoWorkspace(
            name="test",
            path=Path("/tmp"),
            default_branch="develop",
            staging_branch="staging-test",
            owner="ned",
        )
        assert ws.name == "test"
        assert ws.default_branch == "develop"
        assert ws.staging_branch == "staging-test"
        assert ws.owner == "ned"


# ═══════════════════════════════════════════════════════════════════
# get_repo_path
# ═══════════════════════════════════════════════════════════════════


class TestGetRepoPath:
    def test_known_repo(self, router: WorkspaceRouter) -> None:
        path = router.get_repo_path("prismatic-engine")
        assert path == Path("/home/ubuntu/work/prismatic-engine")
        assert isinstance(path, Path)

    def test_unknown_repo(self, router: WorkspaceRouter) -> None:
        with pytest.raises(KeyError, match="Unknown workspace"):
            router.get_repo_path("nonexistent")

    def test_get_repo_accessor(self, router: WorkspaceRouter) -> None:
        ws = router.get_repo("hd-engine")
        assert ws is not None
        assert ws.name == "hd-engine"
        assert ws.path == Path("/home/ubuntu/work/hd-engine")
        assert ws.default_branch == "master"

    def test_get_repo_nonexistent(self, router: WorkspaceRouter) -> None:
        assert router.get_repo("nonexistent") is None


# ═══════════════════════════════════════════════════════════════════
# resolve_absolute
# ═══════════════════════════════════════════════════════════════════


class TestResolveAbsolute:
    def test_simple_resolution(self, router: WorkspaceRouter) -> None:
        path = router.resolve_absolute(
            "prismatic-engine:prismatic/lock.py"
        )
        assert path == Path(
            "/home/ubuntu/work/prismatic-engine/prismatic/lock.py"
        )

    def test_nested_repo(self, router: WorkspaceRouter) -> None:
        path = router.resolve_absolute(
            "active-oahu-static:content/tours/mokulua.md"
        )
        assert path == Path(
            "/home/ubuntu/work/active-oahu-static/content/tours/mokulua.md"
        )

    def test_root_path(self, router: WorkspaceRouter) -> None:
        path = router.resolve_absolute("hd-engine:.")
        assert path == Path("/home/ubuntu/work/hd-engine")

    def test_dot_dot_traversal_fails(self, router: WorkspaceRouter) -> None:
        """Path traversal beyond workspace root must raise."""
        with pytest.raises(WorkspaceViolationError, match="escapes workspace"):
            router.resolve_absolute("prismatic-engine:../../etc/passwd")

    def test_absolute_subpath_in_ref(self, router: WorkspaceRouter) -> None:
        """Even if the relative part starts with /, it should be treated
        as relative and joined to the workspace root."""
        with pytest.raises(WorkspaceViolationError, match="escapes workspace"):
            router.resolve_absolute("prismatic-engine:/etc/passwd")

    def test_unknown_repo(self, router: WorkspaceRouter) -> None:
        with pytest.raises(KeyError, match="Unknown workspace"):
            router.resolve_absolute("unknown-repo:api/core.go")

    def test_malformed_ref(self, router: WorkspaceRouter) -> None:
        with pytest.raises(ValueError, match="exactly one ':'"):
            router.resolve_absolute("bad-ref-no-colon")


# ═══════════════════════════════════════════════════════════════════
# detect_repo_from_path
# ═══════════════════════════════════════════════════════════════════


class TestDetectRepoFromPath:
    def test_exact_match(self, router: WorkspaceRouter) -> None:
        repo_name, rel = router.detect_repo_from_path(
            "/home/ubuntu/work/prismatic-engine"
        )
        assert repo_name == "prismatic-engine"
        assert rel == Path(".")

    def test_nested_file(self, router: WorkspaceRouter) -> None:
        repo_name, rel = router.detect_repo_from_path(
            "/home/ubuntu/work/prismatic-engine/prismatic/lock.py"
        )
        assert repo_name == "prismatic-engine"
        assert rel == Path("prismatic/lock.py")

    def test_another_repo(self, router: WorkspaceRouter) -> None:
        repo_name, rel = router.detect_repo_from_path(
            "/home/ubuntu/work/hd-engine/api/core.go"
        )
        assert repo_name == "hd-engine"
        assert rel == Path("api/core.go")

    def test_longest_prefix_wins(self, router: WorkspaceRouter, tmp_path: Path) -> None:
        """Two workspaces with nested roots — the more specific wins."""
        config = {
            "swarm_workspaces": {
                "parent": {"path": str(tmp_path)},
                "child": {"path": str(tmp_path / "sub")},
            }
        }
        r = WorkspaceRouter(config)
        repo_name, rel = r.detect_repo_from_path(tmp_path / "sub" / "file.py")
        assert repo_name == "child"
        assert rel == Path("file.py")

    def test_unmatched_path(self, router: WorkspaceRouter) -> None:
        with pytest.raises(WorkspaceViolationError, match="does not belong"):
            router.detect_repo_from_path("/nonexistent/path")

    def test_outside_all_repos(self, router: WorkspaceRouter) -> None:
        with pytest.raises(WorkspaceViolationError, match="does not belong"):
            router.detect_repo_from_path("/tmp/some-other-path")


# ═══════════════════════════════════════════════════════════════════
# Edge cases — registration edge
# ═══════════════════════════════════════════════════════════════════


class TestRegistrationEdgeCases:
    def test_duplicate_workspace_last_wins(self) -> None:
        config = {
            "swarm_workspaces": {
                "my-repo": {"path": "/tmp/first"},
                "my-repo": {"path": "/tmp/second"},
            }
        }
        # Python dict dedup: last key wins
        router = WorkspaceRouter(config)
        assert router.get_repo("my-repo") is not None
        assert router.get_repo_path("my-repo") == Path("/tmp/second")

    def test_path_is_resolved(self) -> None:
        """The workspace path should be resolved to an absolute path
        during construction."""
        config = {
            "swarm_workspaces": {
                "relative": {"path": "."},
            }
        }
        router = WorkspaceRouter(config)
        ws = router.get_repo("relative")
        assert ws is not None
        assert ws.path.is_absolute()

    def test_workspace_names_sorted(self, router: WorkspaceRouter) -> None:
        names = router.workspace_names
        assert names == sorted(names)
