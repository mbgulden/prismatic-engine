"""Tests for first-run workspace optimization."""

from __future__ import annotations

import json

from prismatic.workspace_optimizer import HIGH_OVERHEAD_PLUGINS, optimize_workspace


def test_optimize_workspace_creates_three_ignore_files_and_manifest(tmp_path, monkeypatch):
    home = tmp_path / "home"
    settings_dir = home / ".gemini" / "antigravity-cli"
    settings_dir.mkdir(parents=True)
    settings_path = settings_dir / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "permissions": {
                    "allow": [
                        "plugin:search-documents:read",
                        "plugin:visualization-server:run",
                        "shell:git status",
                    ]
                }
            }
        )
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AGY_BIN", str(tmp_path / "missing-agy-bin"))

    workspace = tmp_path / "workspace"
    result = optimize_workspace(workspace)

    assert result.workspace == workspace.resolve()
    assert {path.name for path in result.ignore_files} == {
        ".gitignore",
        ".geminiignore",
        ".antigravityignore",
    }
    for ignore_file in result.ignore_files:
        text = ignore_file.read_text()
        assert "prismatic-engine optimize-workspace" in text
        assert "node_modules/" in text
        assert ".antigravity/" in text

    manifest = workspace / ".prismatic" / "disabled_plugins.json"
    disabled = json.loads(manifest.read_text())["disabled_plugins"]
    assert disabled == sorted(HIGH_OVERHEAD_PLUGINS)

    settings = json.loads(settings_path.read_text())
    assert settings["permissions"]["allow"] == ["shell:git status"]


def test_optimize_workspace_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGY_BIN", str(tmp_path / "missing-agy-bin"))

    workspace = tmp_path / "workspace"
    first = optimize_workspace(workspace)
    before = {path.name: path.read_text() for path in first.ignore_files}

    second = optimize_workspace(workspace)
    after = {path.name: path.read_text() for path in second.ignore_files}

    assert before == after
    for text in after.values():
        assert text.count("# >>> prismatic-engine optimize-workspace >>>") == 1
