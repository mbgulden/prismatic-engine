# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import json
from pathlib import Path

from prismatic.workspace_optimizer import IGNORE_FILES, optimize_workspace


def test_optimize_workspace_writes_three_ignore_files_and_is_idempotent(
    tmp_path: Path,
) -> None:
    result1 = optimize_workspace(tmp_path, disable_plugins=False)
    result2 = optimize_workspace(tmp_path, disable_plugins=False)

    assert result1["ok"] is True
    assert result2["ok"] is True
    for name in IGNORE_FILES:
        path = tmp_path / name
        assert path.exists()
        text = path.read_text()
        assert "node_modules/" in text
        assert ".git/" in text
        assert ".pytest_cache/" in text
    assert all(item["changed"] is False for item in result2["ignore_files"])


def test_optimize_workspace_preserves_user_edits(tmp_path: Path) -> None:
    custom = tmp_path / ".antigravityignore"
    custom.write_text("# user rule\ncustom-artifact/\n")

    result = optimize_workspace(tmp_path, disable_plugins=False)
    text = custom.read_text()

    assert result["ok"] is True
    assert "custom-artifact/" in text
    assert "node_modules/" in text


def test_workspace_optimizer_cli_json(tmp_path: Path, capsys) -> None:
    from prismatic.workspace_optimizer import main

    rc = main([str(tmp_path), "--no-plugin-disable"])
    out = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert out["ok"] is True
    assert out["workspace"] == str(tmp_path.resolve())
