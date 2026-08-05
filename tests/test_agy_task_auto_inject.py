"""
Unit tests for AGY_TASK prompt closeout appendix auto-injector and GRO-4500 cutoff.
"""

from prismatic.curator.issue_to_task import build_task_content_from_issue


def test_build_task_content_auto_injects_closeout_appendix_post_cutoff():
    issue_node = {
        "title": "Test Task for Closeout Contract",
        "description": "Implement feature X and verify",
        "priority": 1,
        "state": {"name": "Todo"},
        "labels": {"nodes": [{"name": "dispatch:ready"}]},
    }
    content = build_task_content_from_issue("GRO-4500", issue_node)

    assert "GRO-4500" in content
    assert "Mandatory Prismatic Closeout Contract Requirement" in content
    assert "AGY_TASK_RESULT_PACKET_OK" in content


def test_build_task_content_exempts_legacy_issues_pre_cutoff():
    issue_node = {
        "title": "Legacy In-Flight Task",
        "description": "Legacy feature fix pre-cutoff",
        "priority": 1,
        "state": {"name": "Todo"},
        "labels": {"nodes": [{"name": "dispatch:ready"}]},
    }
    content = build_task_content_from_issue("GRO-4499", issue_node)

    assert "GRO-4499" in content
    assert "Mandatory Prismatic Closeout Contract Requirement" not in content
    assert "AGY_TASK_RESULT_PACKET_OK" not in content


def test_build_task_content_auto_inject_is_idempotent():
    issue_node = {
        "title": "Test Task with Pre-existing Appendix",
        "description": "Implement feature Y\n\n## Mandatory Prismatic Closeout Contract Requirement\nPre-existing appendix text",
        "priority": 1,
        "state": {"name": "Todo"},
        "labels": {"nodes": [{"name": "dispatch:ready"}]},
    }
    content = build_task_content_from_issue("GRO-4501", issue_node)

    # Must only contain 1 occurrence of header
    assert content.count("Mandatory Prismatic Closeout Contract Requirement") == 1
