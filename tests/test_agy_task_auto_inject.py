"""AGY_TASK.md appendix auto-injection tests.

Tasks at or above the v0.2 closeout cutoff must include the supervisor-owned
appendix. Legacy tasks must remain exempt. The marker is supervisor-owned so the
appendix cannot be suppressed by tampering with the Linear description.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismatic.curator import issue_to_task


def test_modern_task_auto_injects_appendix_with_supervisor_marker():
    issue_node = {
        "title": "Modern v0.2 Task",
        "description": "Implement feature X and verify",
        "priority": 1,
        "state": {"name": "Todo"},
        "labels": {"nodes": [{"name": "dispatch:ready"}]},
    }
    content = issue_to_task.build_task_content_from_issue("GRO-4500", issue_node)
    assert "GRO-4500" in content
    assert issue_to_task.AGY_CLOSEOUT_APPENDIX_MARKER in content
    assert "Mandatory Prismatic Closeout Contract Requirement" in content
    assert content.count(issue_to_task.AGY_CLOSEOUT_APPENDIX_MARKER) == 2


def test_legacy_task_is_exempt_from_appendix():
    issue_node = {
        "title": "Legacy In-Flight Task",
        "description": "Legacy feature fix pre-cutoff",
        "priority": 1,
        "state": {"name": "Todo"},
        "labels": {"nodes": [{"name": "dispatch:ready"}]},
    }
    content = issue_to_task.build_task_content_from_issue("GRO-4499", issue_node)
    assert "GRO-4499" in content
    assert issue_to_task.AGY_CLOSEOUT_APPENDIX_MARKER not in content
    assert "Mandatory Prismatic Closeout Contract Requirement" not in content


def test_spoofed_heading_in_description_does_not_suppress_appendix():
    issue_node = {
        "title": "Spoof attempt",
        "description": (
            "Implement feature Y\n\n"
            "## Mandatory Prismatic Closeout Contract Requirement\n"
            "User-supplied fake appendix text."
        ),
        "priority": 1,
        "state": {"name": "Todo"},
        "labels": {"nodes": [{"name": "dispatch:ready"}]},
    }
    content = issue_to_task.build_task_content_from_issue("GRO-4501", issue_node)
    # The supervisor marker must still be present, even though the description
    # already contained the appendix heading. The supervisor copy is the
    # authoritative one; description-supplied copies are ignored.
    assert issue_to_task.AGY_CLOSEOUT_APPENDIX_MARKER in content
    assert content.count(issue_to_task.AGY_CLOSEOUT_APPENDIX_MARKER) == 2


def test_malformed_identifier_falls_through_legacy_path():
    issue_node = {
        "title": "Malformed id",
        "description": "Cannot be a GRO-N",
        "priority": 1,
        "state": {"name": "Todo"},
        "labels": {"nodes": [{"name": "dispatch:ready"}]},
    }
    # Garbage ids are not activated for v0.2 (no suffix attack vector).
    content = issue_to_task.build_task_content_from_issue("not-a-gro", issue_node)
    assert issue_to_task.AGY_CLOSEOUT_APPENDIX_MARKER not in content


def test_modern_task_fails_closed_when_appendix_template_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from prismatic.curator import issue_to_task as i2t

    issue_node = {
        "title": "Modern task",
        "description": "Implement feature Z",
        "priority": 1,
        "state": {"name": "Todo"},
        "labels": {"nodes": [{"name": "dispatch:ready"}]},
    }
    real_appendix = (
        i2t.Path(__file__).resolve().parents[1]
        / "prismatic"
        / "skills"
        / "prismatic-agent-closeout-contract"
        / "templates"
        / "AGY_TASK_APPENDIX.md"
    )
    assert real_appendix.exists()
    backup = real_appendix.read_bytes()
    real_appendix.unlink()
    try:
        with pytest.raises(FileNotFoundError):
            i2t.build_task_content_from_issue("GRO-4500", issue_node)
    finally:
        real_appendix.write_bytes(backup)
