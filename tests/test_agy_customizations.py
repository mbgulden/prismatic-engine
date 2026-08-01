from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from prismatic import agy_customizations as customizations
from prismatic.agy_cli import cli as agy_cli

EXPECTED_SKILLS = {
    "prismatic-engine-operations",
    "prismatic-agy-execution",
    "prismatic-context-discipline",
    "prismatic-evidence-and-review",
    "prismatic-worktree-safety",
}


def test_shipped_bundle_is_valid_and_secret_free() -> None:
    result = customizations.validate_bundle()
    assert result["ok"] is True
    assert result["errors"] == []
    assert result["file_count"] == 10
    assert set(result["files"]) == customizations.REQUIRED_FILES


def test_checked_in_workspace_bundle_matches_packaged_resources() -> None:
    root = Path(__file__).resolve().parents[1]
    files = customizations.bundle_files()
    for relative, expected in files.items():
        checked_in = root / relative
        assert checked_in.is_file(), relative
        assert checked_in.read_bytes() == expected, relative
    assert not (root / customizations.MANAGED_REL).exists()


def test_install_status_and_idempotent_reinstall(tmp_path: Path) -> None:
    code, installed = customizations.install_bundle(tmp_path)
    assert code == 0
    assert installed["ok"] is True
    assert installed["changed"] is True
    file_actions = {
        item["action"]
        for item in installed["plan"]
        if item["path"] != customizations.MANAGED_REL.as_posix()
    }
    assert file_actions == {"create"}
    assert installed["plan"][-1]["action"] == "create-manifest"

    manifest = json.loads((tmp_path / customizations.MANAGED_REL).read_text())
    assert manifest["schema"] == customizations.MANAGED_SCHEMA
    assert set(manifest["files"]) == customizations.REQUIRED_FILES

    code, status = customizations.status_bundle(tmp_path)
    assert code == 0
    assert status["ok"] is True
    assert status["managed"] is True
    assert {item["state"] for item in status["files"]} == {"current"}

    code, repeated = customizations.install_bundle(tmp_path)
    assert code == 0
    assert repeated["changed"] is False
    assert repeated["would_change"] is False
    assert repeated["plan"][-1]["action"] == "unchanged-manifest"
    assert all(
        item["action"] == "unchanged"
        for item in repeated["plan"]
        if item["path"] != customizations.MANAGED_REL.as_posix()
    )


def test_dry_run_has_no_side_effects(tmp_path: Path) -> None:
    code, result = customizations.install_bundle(tmp_path, dry_run=True)
    assert code == 0
    assert result["changed"] is False
    assert result["would_change"] is True
    assert not (tmp_path / ".agents").exists()


def test_conflict_is_whole_plan_fail_closed(tmp_path: Path) -> None:
    conflict = tmp_path / ".agents" / "skills.json"
    conflict.parent.mkdir(parents=True)
    conflict.write_text('{"entries": [{"path": "my-team-skills"}]}\n')

    code, result = customizations.install_bundle(tmp_path)
    assert code == 2
    assert result["ok"] is False
    assert result["changed"] is False
    assert result["conflicts"] == [".agents/skills.json"]
    assert conflict.read_text() == '{"entries": [{"path": "my-team-skills"}]}\n'
    assert not (tmp_path / ".agents" / "rules" / "prismatic-engine.md").exists()
    assert not (tmp_path / customizations.MANAGED_REL).exists()


def test_force_install_backs_up_conflict(tmp_path: Path) -> None:
    conflict = tmp_path / ".agents" / "skills.json"
    conflict.parent.mkdir(parents=True)
    original = b'{"entries": [{"path": "my-team-skills"}]}\n'
    conflict.write_bytes(original)

    code, result = customizations.install_bundle(tmp_path, force=True)
    assert code == 0
    assert result["ok"] is True
    assert any(item["action"] == "replace-with-backup" for item in result["plan"])
    backups = list((tmp_path / customizations.BACKUP_ROOT_REL).glob("*/skills.json"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert backups[0].stat().st_mode & 0o777 == 0o600


def test_unchanged_managed_file_can_upgrade(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    code, _ = customizations.install_bundle(tmp_path)
    assert code == 0
    original_bundle = customizations.bundle_files()
    changed_bundle = dict(original_bundle)
    relative = ".agents/rules/prismatic-engine.md"
    changed_bundle[relative] = original_bundle[relative] + b"\nManaged upgrade.\n"
    monkeypatch.setattr(customizations, "bundle_files", lambda: changed_bundle)
    monkeypatch.setattr(
        customizations,
        "validate_bundle",
        lambda files=None: {
            "ok": True,
            "schema": customizations.BUNDLE_SCHEMA,
            "file_count": len(changed_bundle),
            "files": {
                name: hashlib.sha256(data).hexdigest()
                for name, data in changed_bundle.items()
            },
            "errors": [],
            "warnings": [],
        },
    )

    code, blocked = customizations.install_bundle(tmp_path)
    assert code == 2
    assert set(blocked["conflicts"]) == {
        relative,
        customizations.MANAGED_REL.as_posix(),
    }

    code, upgraded = customizations.install_bundle(tmp_path, force=True)
    assert code == 0
    actions = {item["path"]: item["action"] for item in upgraded["plan"]}
    assert actions[relative] == "replace-with-backup"
    assert (tmp_path / relative).read_bytes() == changed_bundle[relative]


def test_uninstall_preserves_drift_without_partial_removal(tmp_path: Path) -> None:
    code, _ = customizations.install_bundle(tmp_path)
    assert code == 0
    drifted = tmp_path / ".agents" / "rules" / "prismatic-engine.md"
    drifted.write_text(drifted.read_text() + "\nLocal rule.\n")

    code, blocked = customizations.uninstall_bundle(tmp_path)
    assert code == 2
    assert blocked["conflicts"] == [".agents/rules/prismatic-engine.md"]
    assert all((tmp_path / path).exists() for path in customizations.REQUIRED_FILES)
    assert (tmp_path / customizations.MANAGED_REL).exists()

    code, removed = customizations.uninstall_bundle(tmp_path, force=True)
    assert code == 0
    assert removed["ok"] is True
    assert not any((tmp_path / path).exists() for path in customizations.REQUIRED_FILES)
    assert not (tmp_path / customizations.MANAGED_REL).exists()
    backups = list(
        (tmp_path / customizations.BACKUP_ROOT_REL).glob("*/rules/prismatic-engine.md")
    )
    assert len(backups) == 1
    assert "Local rule." in backups[0].read_text()


def test_refuses_symlink_customization_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".agents").symlink_to(outside, target_is_directory=True)
    with pytest.raises(
        customizations.CustomizationError, match="symlink customization root"
    ):
        customizations.install_bundle(tmp_path)
    assert list(outside.iterdir()) == []


def test_tampered_manifest_cannot_target_unmanaged_agents_file(tmp_path: Path) -> None:
    code, _ = customizations.install_bundle(tmp_path)
    assert code == 0
    victim = tmp_path / ".agents" / "user-note.md"
    victim.write_text("preserve")
    manifest = tmp_path / customizations.MANAGED_REL
    payload = json.loads(manifest.read_text())
    payload["files"][".agents/user-note.md"] = hashlib.sha256(b"preserve").hexdigest()
    manifest.write_text(json.dumps(payload))
    with pytest.raises(
        customizations.CustomizationError, match="unsafe managed manifest"
    ):
        customizations.uninstall_bundle(tmp_path, force=True)
    assert victim.read_text() == "preserve"


def test_non_regular_destination_is_never_replaced(tmp_path: Path) -> None:
    target = tmp_path / ".agents" / "skills.json"
    target.mkdir(parents=True)
    code, result = customizations.install_bundle(tmp_path, force=True)
    assert code == 2
    assert result["changed"] is False
    assert result["conflicts"] == [".agents/skills.json"]
    assert target.is_dir()
    assert not (tmp_path / customizations.MANAGED_REL).exists()


def test_status_rejects_stale_managed_manifest(tmp_path: Path) -> None:
    code, _ = customizations.install_bundle(tmp_path)
    assert code == 0
    manifest_path = tmp_path / customizations.MANAGED_REL
    manifest = json.loads(manifest_path.read_text())
    relative = ".agents/skills.json"
    manifest["files"][relative] = "0" * 64
    manifest_path.write_text(json.dumps(manifest))

    code, result = customizations.status_bundle(tmp_path)
    assert code == 1
    assert result["ok"] is False
    assert result["managed_manifest_current"] is False
    assert {item["state"] for item in result["files"]} == {"current"}


def test_audit_is_secret_safe_and_classifies_hazards(tmp_path: Path) -> None:
    token = tmp_path / "antigravity-oauth-token"
    token.write_text("DO-NOT-RETURN-THIS-VALUE")
    runtime = tmp_path / "brain" / "session" / "transcript.jsonl"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("PRIVATE-TRANSCRIPT")
    skill = tmp_path / "skills" / "unsafe" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: unsafe\ndescription: test\n---\n"
        "Run /home/example/tool with dangerously-skip-permissions.\n"
    )

    result = customizations.audit_customization_root(tmp_path)
    encoded = json.dumps(result)
    assert result["ok"] is True
    assert result["sensitive_path_count"] == 1
    assert result["generated_or_runtime_file_count"] == 1
    assert result["candidate_count"] == 1
    assert result["portable_candidates"][0]["hazards"] == [
        "mutable-user-path",
        "skip-permissions",
    ]
    assert "DO-NOT-RETURN-THIS-VALUE" not in encoded
    assert "PRIVATE-TRANSCRIPT" not in encoded


def test_ancestor_collision_has_no_partial_install(tmp_path: Path) -> None:
    blocker = tmp_path / ".agents" / "skills" / "prismatic-agy-execution"
    blocker.parent.mkdir(parents=True)
    blocker.write_text("not-a-directory")

    code, result = customizations.install_bundle(tmp_path)

    assert code == 2
    assert result["changed"] is False
    assert ".agents/skills/prismatic-agy-execution/SKILL.md" in result["conflicts"]
    assert blocker.read_text() == "not-a-directory"
    assert not (tmp_path / ".agents" / "rules").exists()
    assert not (tmp_path / ".agents" / "skills.json").exists()
    assert not (tmp_path / customizations.MANAGED_REL).exists()


def test_force_install_rejects_symlinked_backup_root(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    outside.mkdir()
    conflict = workspace / ".agents" / "skills.json"
    conflict.parent.mkdir(parents=True)
    conflict.write_text("local")
    (workspace / customizations.BACKUP_ROOT_REL).symlink_to(
        outside, target_is_directory=True
    )

    with pytest.raises(customizations.CustomizationError, match="backup directory"):
        customizations.install_bundle(workspace, force=True)

    assert conflict.read_text() == "local"
    assert list(outside.iterdir()) == []
    assert not (workspace / customizations.MANAGED_REL).exists()
    assert not (workspace / ".agents" / "rules").exists()


def test_force_uninstall_rejects_symlinked_backup_root(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    code, _ = customizations.install_bundle(workspace)
    assert code == 0
    drifted = workspace / ".agents" / "skills.json"
    drifted.write_text("local")
    (workspace / customizations.BACKUP_ROOT_REL).symlink_to(
        outside, target_is_directory=True
    )

    with pytest.raises(customizations.CustomizationError, match="backup directory"):
        customizations.uninstall_bundle(workspace, force=True)

    assert drifted.read_text() == "local"
    assert list(outside.iterdir()) == []
    assert (workspace / customizations.MANAGED_REL).is_file()
    assert all((workspace / path).exists() for path in customizations.REQUIRED_FILES)


def test_backup_operation_collision_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    outside.mkdir()
    conflict = workspace / ".agents" / "skills.json"
    conflict.parent.mkdir(parents=True)
    conflict.write_text("local")
    operation = workspace / customizations.BACKUP_ROOT_REL / "fixed"
    operation.mkdir(parents=True)
    (operation / "skills.json").symlink_to(outside / "sentinel")
    (outside / "sentinel").write_text("outside")
    monkeypatch.setattr(customizations, "_backup_operation_id", lambda: "fixed")

    with pytest.raises(
        customizations.CustomizationError, match="exclusive backup operation"
    ):
        customizations.install_bundle(workspace, force=True)

    assert conflict.read_text() == "local"
    assert (outside / "sentinel").read_text() == "outside"
    assert (operation / "skills.json").is_symlink()


def test_rapid_force_operations_keep_distinct_backups(tmp_path: Path) -> None:
    target = tmp_path / ".agents" / "skills.json"
    target.parent.mkdir(parents=True)
    first = b"original-a"
    second = b"local-b"
    target.write_bytes(first)
    code, _ = customizations.install_bundle(tmp_path, force=True)
    assert code == 0
    target.write_bytes(second)
    code, _ = customizations.install_bundle(tmp_path, force=True)
    assert code == 0

    backups = sorted((tmp_path / customizations.BACKUP_ROOT_REL).glob("*/skills.json"))
    assert len(backups) == 2
    assert {path.read_bytes() for path in backups} == {first, second}
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in backups)


def test_install_transaction_rolls_back_mid_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    original_link = customizations.os.link
    calls = 0

    def fail_second_link(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        target: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected link failure")
        original_link(
            source,
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(customizations.os, "link", fail_second_link)
    with pytest.raises(customizations.CustomizationError, match="transaction failed"):
        customizations.install_bundle(tmp_path)

    assert not any((tmp_path / path).exists() for path in customizations.REQUIRED_FILES)
    assert not (tmp_path / customizations.MANAGED_REL).exists()


def test_force_install_rollback_restores_original_bytes_and_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conflict = tmp_path / ".agents" / "skills.json"
    conflict.parent.mkdir(parents=True)
    original = b"local-content"
    conflict.write_bytes(original)
    conflict.chmod(0o640)
    original_link = customizations.os.link

    def fail_manifest_link(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        target: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        if Path(os.fsdecode(target)) == tmp_path / customizations.MANAGED_REL:
            raise OSError("injected manifest link failure")
        original_link(
            source,
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(customizations.os, "link", fail_manifest_link)
    with pytest.raises(customizations.CustomizationError, match="transaction failed"):
        customizations.install_bundle(tmp_path, force=True)

    assert conflict.read_bytes() == original
    assert conflict.stat().st_mode & 0o777 == 0o640
    assert not (tmp_path / customizations.MANAGED_REL).exists()
    assert not (tmp_path / customizations.BACKUP_ROOT_REL).exists()
    assert not (tmp_path / ".agents" / "rules").exists()


def test_uninstall_transaction_rolls_back_mid_delete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    code, _ = customizations.install_bundle(tmp_path)
    assert code == 0
    mode_target = tmp_path / ".agents" / "skills.json"
    mode_target.chmod(0o600)
    original_capture = customizations._capture_existing
    managed = {tmp_path / path for path in customizations.REQUIRED_FILES}
    managed.add(tmp_path / customizations.MANAGED_REL)
    calls = 0

    def fail_second_capture(path: Path) -> Path:
        nonlocal calls
        if path in managed:
            calls += 1
            if calls == 2:
                raise OSError("injected capture failure")
        return original_capture(path)

    monkeypatch.setattr(customizations, "_capture_existing", fail_second_capture)
    with pytest.raises(customizations.CustomizationError, match="transaction failed"):
        customizations.uninstall_bundle(tmp_path)

    assert all((tmp_path / path).is_file() for path in customizations.REQUIRED_FILES)
    assert mode_target.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / customizations.MANAGED_REL).is_file()
    code, status = customizations.status_bundle(tmp_path)
    assert code == 0
    assert status["ok"] is True


def test_install_preserves_destination_created_after_preflight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    victim = tmp_path / ".agents" / "rules" / "prismatic-engine.md"
    user_bytes = b"created after preflight"
    original_stage = customizations._stage_file
    injected = False

    def stage_then_create(path: Path, data: bytes, mode: int = 0o644) -> Path:
        nonlocal injected
        staged = original_stage(path, data, mode)
        if path == victim and not injected:
            path.write_bytes(user_bytes)
            path.chmod(0o600)
            injected = True
        return staged

    monkeypatch.setattr(customizations, "_stage_file", stage_then_create)
    with pytest.raises(customizations.CustomizationError, match="destination appeared"):
        customizations.install_bundle(tmp_path)

    assert victim.read_bytes() == user_bytes
    assert victim.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / customizations.MANAGED_REL).exists()
    assert not (tmp_path / customizations.BACKUP_ROOT_REL).exists()
    assert not any(
        (tmp_path / relative).exists()
        for relative in customizations.REQUIRED_FILES
        if tmp_path / relative != victim
    )


def test_install_preserves_both_versions_when_destination_appears_after_capture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    victim = tmp_path / ".agents" / "skills.json"
    victim.parent.mkdir(parents=True)
    original_bytes = b"preflight-original"
    new_user_bytes = b"appeared-after-capture"
    victim.write_bytes(original_bytes)
    victim.chmod(0o640)
    original_capture = customizations._capture_existing
    injected = False

    def capture_then_create(path: Path) -> Path:
        nonlocal injected
        hold = original_capture(path)
        if path == victim and not injected:
            path.write_bytes(new_user_bytes)
            path.chmod(0o600)
            injected = True
        return hold

    monkeypatch.setattr(customizations, "_capture_existing", capture_then_create)
    with pytest.raises(
        customizations.CustomizationError, match="original-preserved-at="
    ):
        customizations.install_bundle(tmp_path, force=True)

    assert victim.read_bytes() == new_user_bytes
    assert victim.stat().st_mode & 0o777 == 0o600
    holds = list(victim.parent.glob(f".{victim.name}.prismatic-hold-*"))
    assert len(holds) == 1
    assert holds[0].read_bytes() == original_bytes
    assert holds[0].stat().st_mode & 0o777 == 0o640
    assert not (tmp_path / customizations.MANAGED_REL).exists()
    assert not (tmp_path / customizations.BACKUP_ROOT_REL).exists()


def test_uninstall_preserves_destination_created_after_capture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    code, _ = customizations.install_bundle(tmp_path)
    assert code == 0
    victim = tmp_path / ".agents" / "rules" / "prismatic-engine.md"
    new_user_bytes = b"appeared-after-uninstall-capture"
    original_capture = customizations._capture_existing
    injected = False

    def capture_then_create(path: Path) -> Path:
        nonlocal injected
        hold = original_capture(path)
        if path == victim and not injected:
            path.write_bytes(new_user_bytes)
            path.chmod(0o600)
            injected = True
        return hold

    monkeypatch.setattr(customizations, "_capture_existing", capture_then_create)
    code, result = customizations.uninstall_bundle(tmp_path)

    assert code == 0
    assert result["cleanup_warnings"] == []
    assert victim.read_bytes() == new_user_bytes
    assert victim.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.rglob("*.prismatic-hold-*"))
    assert not (tmp_path / customizations.MANAGED_REL).exists()
    assert not any(
        (tmp_path / relative).exists()
        for relative in customizations.REQUIRED_FILES
        if tmp_path / relative != victim
    )


def test_uninstall_preserves_file_replaced_after_verification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    code, _ = customizations.install_bundle(tmp_path)
    assert code == 0
    victim = tmp_path / ".agents" / "rules" / "prismatic-engine.md"
    user_bytes = b"replaced after verification"
    original_capture = customizations._capture_existing
    injected = False

    def replace_then_capture(path: Path) -> Path:
        nonlocal injected
        if path == victim and not injected:
            path.write_bytes(user_bytes)
            path.chmod(0o600)
            injected = True
        return original_capture(path)

    monkeypatch.setattr(customizations, "_capture_existing", replace_then_capture)
    with pytest.raises(
        customizations.CustomizationError, match="changed after preflight"
    ):
        customizations.uninstall_bundle(tmp_path)

    assert victim.read_bytes() == user_bytes
    assert victim.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / customizations.MANAGED_REL).is_file()
    assert not list(tmp_path.rglob("*.prismatic-hold-*"))


def test_audit_rejects_symlink_root_without_reading_target(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    skill = outside / "skills" / "private" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    private_value = "PRIVATE-ROOT-VALUE"
    skill.write_text(f"---\nname: private\ndescription: {private_value}\n---\nbody\n")
    root_link = tmp_path / "root-link"
    root_link.symlink_to(outside, target_is_directory=True)

    result = customizations.audit_customization_root(root_link)
    encoded = json.dumps(result)

    assert result["ok"] is False
    assert result["error"] == "symlink-root"
    assert result["candidate_count"] == 0
    assert result["symlink_path_count"] == 1
    assert private_value not in encoded
    assert str(outside) not in encoded


def test_manifest_fifo_is_rejected_without_reading(tmp_path: Path) -> None:
    agents = tmp_path / ".agents"
    agents.mkdir()
    os.mkfifo(tmp_path / customizations.MANAGED_REL)

    with pytest.raises(customizations.CustomizationError, match="regular file"):
        customizations.install_bundle(tmp_path)


def test_manifest_non_object_json_is_rejected(tmp_path: Path) -> None:
    manifest = tmp_path / customizations.MANAGED_REL
    manifest.parent.mkdir()
    manifest.write_text("[]")

    with pytest.raises(customizations.CustomizationError, match="manifest object"):
        customizations.install_bundle(tmp_path)


def test_manifest_digest_tampering_cannot_authorize_deletion(
    tmp_path: Path,
) -> None:
    code, _ = customizations.install_bundle(tmp_path)
    assert code == 0
    target = tmp_path / ".agents" / "skills.json"
    user_content = b"USER CONTENT"
    target.write_bytes(user_content)
    manifest_path = tmp_path / customizations.MANAGED_REL
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][".agents/skills.json"] = hashlib.sha256(user_content).hexdigest()
    manifest_path.write_text(json.dumps(manifest))

    for force in (False, True):
        with pytest.raises(
            customizations.CustomizationError, match="trusted shipped bundle"
        ):
            customizations.uninstall_bundle(tmp_path, force=force)
        assert target.read_bytes() == user_content


def test_exact_files_without_manifest_report_adoption_change(tmp_path: Path) -> None:
    for relative, data in customizations.bundle_files().items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    code, preview = customizations.install_bundle(tmp_path, dry_run=True)
    assert code == 0
    assert preview["changed"] is False
    assert preview["would_change"] is True
    assert preview["plan"][-1]["action"] == "create-manifest"

    code, adopted = customizations.install_bundle(tmp_path)
    assert code == 0
    assert adopted["changed"] is True
    assert adopted["would_change"] is True
    assert adopted["plan"][-1]["action"] == "create-manifest"
    assert (tmp_path / customizations.MANAGED_REL).is_file()


def test_audit_never_follows_symlinks_or_returns_secret_metadata(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside-skill.md"
    outside_secret = "password=" + "x" * 24
    outside.write_text(f"---\nname: outside\ndescription: {outside_secret}\n---\n")
    linked = root / "skills" / "linked" / "SKILL.md"
    linked.parent.mkdir(parents=True)
    linked.symlink_to(outside)
    secret_value = "password=" + "y" * 24
    skill = root / "skills" / "private" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        f"---\nname: private\ndescription: {secret_value}\nversion: 1\n---\n"
    )

    result = customizations.audit_customization_root(root)
    encoded = json.dumps(result)

    assert result["symlink_path_count"] == 1
    assert result["candidate_count"] == 1
    candidate = result["portable_candidates"][0]
    assert candidate["sha256"] is None
    assert "possible-secret-material" in candidate["hazards"]
    assert candidate["metadata"] == {
        "frontmatter_fields": ["name", "description", "version"],
        "name_matches_directory": True,
    }
    assert outside_secret not in encoded
    assert secret_value not in encoded


def test_audit_skips_sensitive_parent_and_nonregular_candidate(
    tmp_path: Path,
) -> None:
    sensitive = tmp_path / "credentials" / "AGENTS.md"
    sensitive.parent.mkdir()
    sensitive.write_text("PRIVATE-CONTENT")
    fifo = tmp_path / "skills" / "fifo" / "SKILL.md"
    fifo.parent.mkdir(parents=True)
    os.mkfifo(fifo)

    result = customizations.audit_customization_root(tmp_path)
    encoded = json.dumps(result)

    assert result["sensitive_path_count"] == 1
    assert result["nonregular_path_count"] == 1
    assert result["candidate_count"] == 0
    assert "PRIVATE-CONTENT" not in encoded


def test_audit_marks_non_object_frontmatter_invalid(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "broken" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\n- list-item\n---\nbody\n")

    result = customizations.audit_customization_root(tmp_path)

    assert result["candidate_count"] == 1
    candidate = result["portable_candidates"][0]
    assert "invalid-frontmatter" in candidate["hazards"]
    assert candidate["metadata"] == {
        "frontmatter_fields": [],
        "name_matches_directory": False,
    }


def test_invalid_bundle_rejects_machine_path_and_secret_material() -> None:
    files = customizations.bundle_files()
    relative = ".agents/rules/prismatic-engine.md"
    unsafe = dict(files)
    secret_example = ("api_" + "key=" + ("a" * 16)).encode()
    unsafe[relative] = b"Use /home/example/tool and " + secret_example + b"\n"
    result = customizations.validate_bundle(unsafe)
    assert result["ok"] is False
    assert any("absolute user path" in error for error in result["errors"])
    assert any("secret material" in error for error in result["errors"])


def test_cli_namespace_dispatches_to_customization_cli(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = agy_cli(["customizations", "validate"])
    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["schema"] == customizations.BUNDLE_SCHEMA


def test_bundle_contains_expected_progressive_skills() -> None:
    files = customizations.bundle_files()
    names = {Path(path).parent.name for path in files if path.endswith("/SKILL.md")}
    assert names == EXPECTED_SKILLS
