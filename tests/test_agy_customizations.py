from __future__ import annotations

import hashlib
import json
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
    assert all(item["action"] == "create" for item in installed["plan"])

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
    assert all(item["action"] == "unchanged" for item in repeated["plan"])


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

    code, upgraded = customizations.install_bundle(tmp_path)
    assert code == 0
    actions = {item["path"]: item["action"] for item in upgraded["plan"]}
    assert actions[relative] == "update-managed"
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
    victim = tmp_path / ".agents" / "user-note.md"
    victim.parent.mkdir(parents=True)
    victim.write_text("preserve")
    manifest = tmp_path / customizations.MANAGED_REL
    manifest.write_text(
        json.dumps(
            {
                "schema": customizations.MANAGED_SCHEMA,
                "files": {
                    ".agents/user-note.md": hashlib.sha256(b"preserve").hexdigest()
                },
            }
        )
    )
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
