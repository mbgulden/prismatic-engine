from __future__ import annotations

import ctypes
import errno
import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prismatic.gateway import workspace_tree as wt

WORKSPACE_ID = "ws-0123456789abcdef0123456789abcdef"


def _registry(tmp_path: Path, root: Path, **entry_updates: object) -> Path:
    entry = {
        "workspace_id": WORKSPACE_ID,
        "display_name": "Operator workspace",
        "root": str(root),
        "enabled": True,
    }
    entry.update(entry_updates)
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps({"schema_version": 1, "workspaces": [entry]}), encoding="utf-8"
    )
    path.chmod(0o600)
    return path


def test_missing_configuration_is_empty_and_has_no_implicit_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(wt.REGISTRY_ENV, raising=False)
    with wt.load_registry() as registry:
        assert wt.list_workspaces(registry) == {
            "ok": True,
            "workspaces": [],
            "workspace_count": 0,
            "max_preview_bytes": 524288,
        }


@pytest.mark.parametrize(
    "document",
    [
        '{"schema_version":1,"schema_version":1,"workspaces":[]}',
        '{"schema_version":1,"workspaces":[],"extra":true}',
        '{"schema_version":2,"workspaces":[]}',
        '{"schema_version":1,"workspaces":{}}',
        '{"schema_version":1,"workspaces":[],"number":NaN}',
        '{"schema_version":1,"workspaces":[],"number":Infinity}',
    ],
)
def test_invalid_registry_shapes_fail_closed(tmp_path: Path, document: str) -> None:
    path = tmp_path / "registry.json"
    path.write_text(document, encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(wt.RegistryError) as caught, wt.load_registry(str(path)):
        pass
    assert caught.value.detail == "workspace registry unavailable"
    assert str(path) not in str(caught.value)


def test_registry_rejects_unsafe_file_and_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    registry = _registry(tmp_path, root)
    registry.chmod(0o622)
    with pytest.raises(wt.RegistryError), wt.load_registry(str(registry)):
        pass
    registry.chmod(0o600)
    link = tmp_path / "registry-link"
    link.symlink_to(registry)
    with pytest.raises(wt.RegistryError), wt.load_registry(str(link)):
        pass


def test_registry_duplicate_ids_and_opened_root_identities_fail(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    entry = {
        "workspace_id": WORKSPACE_ID,
        "display_name": "One",
        "root": str(root),
        "enabled": True,
    }
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps({"schema_version": 1, "workspaces": [entry, entry]}),
        encoding="utf-8",
    )
    path.chmod(0o600)
    with pytest.raises(wt.RegistryError), wt.load_registry(str(path)):
        pass

    duplicate_root = dict(entry, workspace_id="ws-11111111111111111111111111111111")
    path.write_text(
        json.dumps({"schema_version": 1, "workspaces": [entry, duplicate_root]}),
        encoding="utf-8",
    )
    with pytest.raises(wt.RegistryError), wt.load_registry(str(path)):
        pass


def test_disabled_workspace_is_omitted_and_resolves_as_unknown(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    registry_file = _registry(tmp_path, root, enabled=False)
    with wt.load_registry(str(registry_file)) as registry:
        assert wt.list_workspaces(registry)["workspaces"] == []
        with pytest.raises(wt.WorkspaceTreeError) as caught:
            registry.resolve(WORKSPACE_ID)
    assert caught.value.status_code == 404


@pytest.mark.parametrize(
    "value",
    [
        "/etc/passwd",
        "//server/share",
        "C:/secret.txt",
        "\\\\server\\share",
        "../secret.txt",
        "a/../b",
        "a/./b",
        "a//b",
        ".env",
        "a/.git/config",
        "a\\b",
        "a%2fb",
        "a%252fb",
        "nul\x00byte",
        "line\nfeed",
        "ＮＦＫＣ.txt",
    ],
)
def test_relative_path_policy_rejects_unsafe_forms(value: str) -> None:
    with pytest.raises(wt.WorkspaceTreeError) as caught:
        wt.validate_relative_path(value, allow_empty=False)
    assert caught.value.status_code == 400
    assert caught.value.detail == "invalid workspace path"


def test_preview_and_lazy_node_are_bounded_relative_and_path_free(
    tmp_path: Path,
) -> None:
    root = tmp_path / "opaque-root-secret"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "readme.md").write_text("hello\nworld\n", encoding="utf-8")
    (root / "docs" / ".env").write_text("SECRET=x", encoding="utf-8")
    registry_file = _registry(tmp_path, root)

    with wt.load_registry(str(registry_file)) as registry:
        listed = wt.list_workspaces(registry)
        node = wt.get_node(registry, WORKSPACE_ID, "", 2)
        preview = wt.get_preview(registry, WORKSPACE_ID, "docs/readme.md")

    assert listed["workspaces"] == [
        {"workspace_id": WORKSPACE_ID, "name": "Operator workspace"}
    ]
    assert preview == {
        "ok": True,
        "workspace_id": WORKSPACE_ID,
        "workspace_name": "Operator workspace",
        "name": "readme.md",
        "relative_path": "docs/readme.md",
        "size": 12,
        "content": "hello\nworld\n",
        "lines": 3,
    }
    encoded = json.dumps([listed, node, preview])
    assert str(root) not in encoded
    assert '"path"' not in encoded
    assert '"root"' not in encoded
    docs = node["tree"]["children"][0]
    assert [item["name"] for item in docs["children"]] == ["readme.md"]


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        "credentials.json",
        "api-token.txt",
        "private-key.pem",
        "id_rsa",
        "session.json",
        "data.db",
        "database.sqlite3",
        "config.key",
    ],
)
def test_sensitive_and_non_allowlisted_previews_are_default_denied(
    tmp_path: Path, name: str
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / name).write_text("do not disclose", encoding="utf-8")
    registry_file = _registry(tmp_path, root)
    with wt.load_registry(str(registry_file)) as registry:
        with pytest.raises(wt.WorkspaceTreeError) as caught:
            wt.get_preview(registry, WORKSPACE_ID, name)
    assert caught.value.status_code in {400, 403}


@pytest.mark.parametrize("payload", [b"nul\x00byte", b"\xff\xfe", b"x" * (524288 + 1)])
def test_preview_rejects_binary_non_utf8_and_oversize(
    tmp_path: Path, payload: bytes
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "file.txt").write_bytes(payload)
    registry_file = _registry(tmp_path, root)
    with wt.load_registry(str(registry_file)) as registry:
        with pytest.raises(wt.WorkspaceTreeError) as caught:
            wt.get_preview(registry, WORKSPACE_ID, "file.txt")
    assert caught.value.status_code == 403


def test_preview_rejects_symlink_fifo_and_hardlink_without_blocking(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "source.txt"
    source.write_text("text", encoding="utf-8")
    os.link(source, root / "hardlink.txt")
    (root / "link.txt").symlink_to(source)
    os.mkfifo(root / "pipe.txt")
    registry_file = _registry(tmp_path, root)
    with wt.load_registry(str(registry_file)) as registry:
        for name in ("source.txt", "hardlink.txt", "link.txt", "pipe.txt"):
            with pytest.raises(wt.WorkspaceTreeError):
                wt.get_preview(registry, WORKSPACE_ID, name)


def test_open_root_fd_pins_identity_after_path_replacement(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "safe.txt").write_text("safe", encoding="utf-8")
    registry_file = _registry(tmp_path, root)
    with wt.load_registry(str(registry_file)) as registry:
        root.rename(tmp_path / "original")
        root.mkdir()
        (root / "safe.txt").write_text("retargeted", encoding="utf-8")
        assert wt.get_preview(registry, WORKSPACE_ID, "safe.txt")["content"] == "safe"


def test_open_root_closes_new_child_fd_when_fstat_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    real_open = os.open
    real_fstat = os.fstat
    child_fds: list[int] = []

    def recording_open(path: str, flags: int, *args: Any, **kwargs: Any) -> int:
        opened = real_open(path, flags, *args, **kwargs)
        if kwargs.get("dir_fd") is not None:
            child_fds.append(opened)
        return opened

    def failing_child_fstat(fd: int) -> os.stat_result:
        if child_fds and fd == child_fds[-1]:
            raise OSError(errno.EIO, "injected child fstat failure")
        return real_fstat(fd)

    monkeypatch.setattr(os, "open", recording_open)
    monkeypatch.setattr(os, "fstat", failing_child_fstat)
    with pytest.raises(OSError, match="injected child fstat failure"):
        wt._open_root(str(root))
    assert child_fds
    leaked_candidate = child_fds[0]
    monkeypatch.setattr(os, "fstat", real_fstat)
    with pytest.raises(OSError) as caught:
        os.fstat(leaked_candidate)
    assert caught.value.errno == errno.EBADF


@pytest.mark.parametrize("failure_kind", ["fstat", "type-validation"])
def test_open_root_failure_closes_parent_and_child_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_kind: str
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    real_open = os.open
    real_close = os.close
    real_fstat = os.fstat
    parent_fds: list[int] = []
    child_fds: list[int] = []
    close_counts: dict[int, int] = {}

    def recording_open(path: str, flags: int, *args: Any, **kwargs: Any) -> int:
        opened = real_open(path, flags, *args, **kwargs)
        (child_fds if kwargs.get("dir_fd") is not None else parent_fds).append(opened)
        return opened

    def injected_fstat(fd: int) -> Any:
        if child_fds and fd == child_fds[-1]:
            if failure_kind == "fstat":
                raise OSError(errno.EIO, "injected child fstat failure")
            metadata = real_fstat(fd)
            values = list(metadata)
            values[0] = stat.S_IFREG | 0o600
            return os.stat_result(values)
        return real_fstat(fd)

    def recording_close(fd: int) -> None:
        close_counts[fd] = close_counts.get(fd, 0) + 1
        real_close(fd)

    monkeypatch.setattr(os, "open", recording_open)
    monkeypatch.setattr(os, "fstat", injected_fstat)
    monkeypatch.setattr(os, "close", recording_close)
    with pytest.raises((OSError, wt.RegistryError)):
        wt._open_root(str(root))
    assert close_counts[parent_fds[0]] == 1
    assert close_counts[child_fds[0]] == 1


def test_open_root_parent_close_failure_is_never_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    real_open = os.open
    real_close = os.close
    real_fstat = os.fstat
    parent_fds: list[int] = []
    child_fds: list[int] = []
    close_counts: dict[int, int] = {}

    def recording_open(path: str, flags: int, *args: Any, **kwargs: Any) -> int:
        opened = real_open(path, flags, *args, **kwargs)
        (child_fds if kwargs.get("dir_fd") is not None else parent_fds).append(opened)
        return opened

    def failing_parent_close(fd: int) -> None:
        close_counts[fd] = close_counts.get(fd, 0) + 1
        real_close(fd)
        if parent_fds and fd == parent_fds[0]:
            raise OSError(errno.EINTR, "injected parent close failure")

    monkeypatch.setattr(os, "open", recording_open)
    monkeypatch.setattr(os, "close", failing_parent_close)
    with pytest.raises(OSError, match="injected parent close failure"):
        wt._open_root(str(root))
    assert close_counts[parent_fds[0]] == 1
    assert close_counts[child_fds[0]] == 1
    for closed_fd in (parent_fds[0], child_fds[0]):
        with pytest.raises(OSError) as caught:
            real_fstat(closed_fd)
        assert caught.value.errno == errno.EBADF


@pytest.mark.parametrize("escape_kind", ["cross-device", "same-filesystem-bind"])
def test_openat2_complete_resolve_mask_denial_never_downgrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, escape_kind: str
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    registry_file = _registry(tmp_path, root)
    with wt.load_registry(str(registry_file)) as registry:
        workspace = registry.resolve(WORKSPACE_ID)
        monkeypatch.setattr(wt, "_probe_openat2", lambda: True)

        def deny_cross_mount_at_syscall(
            number: int,
            _dir_fd: int,
            _path: ctypes.c_char_p,
            how_pointer: Any,
            size: int,
        ) -> int:
            assert number == wt._OPENAT2_NR
            assert size == ctypes.sizeof(wt._OpenHow)
            how = ctypes.cast(how_pointer, ctypes.POINTER(wt._OpenHow)).contents
            assert how.resolve == wt._OPENAT2_RESOLVE
            assert how.resolve == (
                wt._RESOLVE_BENEATH
                | wt._RESOLVE_NO_SYMLINKS
                | wt._RESOLVE_NO_MAGICLINKS
                | wt._RESOLVE_NO_XDEV
            )
            raise OSError(errno.EXDEV, f"{escape_kind} denied")

        monkeypatch.setattr(wt, "_syscall", deny_cross_mount_at_syscall)
        monkeypatch.setattr(
            wt,
            "_fallback_open",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("downgrade")
            ),
        )
        with pytest.raises(OSError):
            wt._secure_open(workspace, "missing", directory=True)


def test_forced_fallback_root_descriptor_is_valid_and_owned_by_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "file.txt").write_text("ok", encoding="utf-8")
    registry_file = _registry(tmp_path, root)
    with wt.load_registry(str(registry_file)) as registry:
        monkeypatch.setattr(wt, "_probe_openat2", lambda: False)
        node = wt.get_node(registry, WORKSPACE_ID, "", 1)
    tree = node["tree"]
    assert tree["relative_path"] == ""
    assert [child["name"] for child in tree["children"]] == ["file.txt"]


@pytest.mark.parametrize(
    ("relative_path", "stage"),
    [("one/file.txt", "intermediate-parent"), ("file.txt", "final-parent")],
)
def test_forced_fallback_parent_close_failure_never_retries_and_closes_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative_path: str,
    stage: str,
) -> None:
    root = tmp_path / "root"
    (root / "one").mkdir(parents=True)
    (root / "one" / "file.txt").write_text("nested", encoding="utf-8")
    (root / "file.txt").write_text("root", encoding="utf-8")
    registry_file = _registry(tmp_path, root)
    with wt.load_registry(str(registry_file)) as registry:
        workspace = registry.resolve(WORKSPACE_ID)
        real_dup = os.dup
        real_open = os.open
        real_close = os.close
        real_fstat = os.fstat
        duplicated_parents: list[int] = []
        opened_children: list[int] = []
        close_counts: dict[int, int] = {}

        def recording_dup(fd: int) -> int:
            duplicated = real_dup(fd)
            duplicated_parents.append(duplicated)
            return duplicated

        def recording_open(path: str, flags: int, *args: Any, **kwargs: Any) -> int:
            opened = real_open(path, flags, *args, **kwargs)
            opened_children.append(opened)
            return opened

        def failing_parent_close(fd: int) -> None:
            close_counts[fd] = close_counts.get(fd, 0) + 1
            real_close(fd)
            if duplicated_parents and fd == duplicated_parents[0]:
                raise OSError(errno.EINTR, f"injected {stage} close failure")

        monkeypatch.setattr(os, "dup", recording_dup)
        monkeypatch.setattr(os, "open", recording_open)
        monkeypatch.setattr(os, "close", failing_parent_close)
        with pytest.raises(OSError, match=f"injected {stage} close failure"):
            wt._fallback_open(workspace, relative_path.split("/"), directory=False)

        parent_fd = duplicated_parents[0]
        child_fd = opened_children[0]
        assert close_counts[parent_fd] == 1
        assert close_counts[child_fd] == 1
        for closed_fd in (parent_fd, child_fd):
            with pytest.raises(OSError) as caught:
                real_fstat(closed_fd)
            assert caught.value.errno == errno.EBADF


def test_forced_fallback_uses_required_flags_and_denies_intermediate_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    (root / "one" / "two").mkdir(parents=True)
    (root / "one" / "two" / "file.txt").write_text("ok", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (root / "escape").symlink_to(outside, target_is_directory=True)
    registry_file = _registry(tmp_path, root)
    with wt.load_registry(str(registry_file)) as registry:
        workspace = registry.resolve(WORKSPACE_ID)
        monkeypatch.setattr(wt, "_probe_openat2", lambda: False)
        real_open = os.open
        calls: list[tuple[str, int]] = []

        def recording_open(path: str, flags: int, *args: Any, **kwargs: Any) -> int:
            if kwargs.get("dir_fd") is not None:
                calls.append((str(path), flags))
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", recording_open)
        fd = wt._secure_open(workspace, "one/two/file.txt", directory=False)
        os.close(fd)
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        assert calls[-3:] == [
            ("one", directory_flags),
            ("two", directory_flags),
            ("file.txt", file_flags),
        ]
        with pytest.raises(OSError):
            wt._secure_open(workspace, "escape/secret.txt", directory=False)


def test_forced_fallback_checks_mount_identity_for_every_component(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    (root / "one" / "two").mkdir(parents=True)
    (root / "one" / "two" / "file.txt").write_text("ok", encoding="utf-8")
    registry_file = _registry(tmp_path, root)
    with wt.load_registry(str(registry_file)) as registry:
        workspace = registry.resolve(WORKSPACE_ID)
        monkeypatch.setattr(wt, "_probe_openat2", lambda: False)
        real_mount_id = wt._mount_id
        calls: list[int] = []

        def recording_mount_id(fd: int) -> int:
            calls.append(fd)
            return real_mount_id(fd)

        monkeypatch.setattr(wt, "_mount_id", recording_mount_id)
        fd = wt._secure_open(workspace, "one/two/file.txt", directory=False)
        os.close(fd)
        assert len(calls) == 3

        monkeypatch.setattr(wt, "_mount_id", lambda _fd: workspace.mount_id + 1)
        with pytest.raises(OSError):
            wt._secure_open(workspace, "one/two/file.txt", directory=False)


@pytest.mark.parametrize("escape_kind", ["cross-device", "same-filesystem-bind"])
def test_forced_fallback_rejects_synthetic_mount_identity_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, escape_kind: str
) -> None:
    """Deterministic statx fixture for hosts without private mount namespaces."""
    root = tmp_path / "root"
    mounted = root / "mounted"
    mounted.mkdir(parents=True)
    (mounted / "file.txt").write_text("ok", encoding="utf-8")
    registry_file = _registry(tmp_path, root)
    with wt.load_registry(str(registry_file)) as registry:
        workspace = registry.resolve(WORKSPACE_ID)
        monkeypatch.setattr(wt, "_probe_openat2", lambda: False)
        real_mount_id = wt._mount_id

        def synthetic_mount_id(fd: int) -> int:
            target = os.readlink(f"/proc/self/fd/{fd}")
            if target.endswith("/mounted"):
                return workspace.mount_id + 1
            return real_mount_id(fd)

        monkeypatch.setattr(wt, "_mount_id", synthetic_mount_id)
        with pytest.raises(OSError):
            wt._secure_open(workspace, "mounted/file.txt", directory=False)
    assert escape_kind in {"cross-device", "same-filesystem-bind"}


def test_routes_return_contract_shapes_and_never_registry_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prismatic.gateway import server

    root = tmp_path / "private-root"
    root.mkdir()
    (root / "hello.md").write_text("hello", encoding="utf-8")
    registry_file = _registry(tmp_path, root)
    monkeypatch.setenv(wt.REGISTRY_ENV, str(registry_file))
    monkeypatch.setattr(wt, "_probe_openat2", lambda: False)
    with TestClient(server.app) as client:
        listing = client.get("/api/workspaces")
        node = client.get(
            "/api/workspace-tree/node",
            params={"workspace_id": WORKSPACE_ID, "path": "", "depth": 1},
        )
        preview = client.get(
            "/api/workspace-tree/preview",
            params={"workspace_id": WORKSPACE_ID, "path": "hello.md"},
        )
        resolved = client.get(
            "/api/workspace-tree/resolve", params={"file": "hello.md"}
        )
        rejected = client.get(
            "/api/workspace-tree/resolve", params={"file": "../hello.md"}
        )
        legacy = client.get(
            "/workspace-tree",
            params={"workspace_id": WORKSPACE_ID, "path": "hello.md"},
        )
    assert listing.status_code == node.status_code == preview.status_code == 200
    assert resolved.status_code == 200
    assert resolved.json() == {
        "ok": True,
        "workspace_id": WORKSPACE_ID,
        "relative_path": "hello.md",
    }
    assert rejected.status_code == 400
    combined = listing.text + node.text + preview.text + resolved.text + legacy.text
    assert str(root) not in combined
    assert str(registry_file) not in combined
    assert '"root"' not in combined
    assert '"path"' not in listing.text + node.text + preview.text
