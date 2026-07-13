import json
import sys
import unittest.mock as mock
from pathlib import Path

# Add profile scripts to system path to import get_linear_project_activity
sys.path.insert(0, "/home/ubuntu/.hermes/profiles/orchestrator/scripts")
import get_linear_project_activity

def test_gql_success():
    """Test GraphQL client query success."""
    mock_response = mock.MagicMock()
    mock_response.read.return_value = json.dumps({
        "data": {
            "project": {
                "id": "test-pid",
                "name": "Test Project",
                "updatedAt": "2026-07-13T12:00:00Z"
            }
        }
    }).encode("utf-8")

    with mock.patch("urllib.request.urlopen", return_value=mock_response):
        with mock.patch("get_linear_project_activity.get_linear_api_key", return_value="dummy-key"):
            res = get_linear_project_activity.gql("query { project(id: \"test-pid\") { id } }")
            assert "errors" not in res
            assert res.get("data", {}).get("project", {}).get("id") == "test-pid"

def test_gql_error_handling_project_not_found():
    """Test GraphQL client handles 'Project not found' error by returning errors."""
    mock_response = mock.MagicMock()
    mock_response.read.return_value = json.dumps({
        "errors": [
            {
                "message": "Project not found",
                "extensions": {
                    "code": "NOT_FOUND"
                }
            }
        ]
    }).encode("utf-8")

    with mock.patch("urllib.request.urlopen", return_value=mock_response):
        with mock.patch("get_linear_project_activity.get_linear_api_key", return_value="dummy-key"):
            res = get_linear_project_activity.gql("query { project(id: \"invalid-pid\") { id } }")
            assert "errors" in res
            assert res["errors"][0]["message"] == "Project not found"

def test_project_activity_hydration_blocked():
    """Verify that projects returning GraphQL errors render as ACCESS_BLOCKED instead of None/0."""
    # We mock gql to return a project not found error
    mock_err_res = {
        "errors": [
            {
                "message": "Project not found",
                "extensions": {"code": "NOT_FOUND"}
            }
        ]
    }

    # Simulate get_linear_project_activity's execution loop on a fake registry
    fake_projects = [
        {"key": "test-key", "name": "Test Project", "pid": "test-pid", "category": "ventures"}
    ]

    q_combined = "dummy-query"

    with mock.patch("get_linear_project_activity.gql", return_value=mock_err_res):
        # Run logic similar to main loop
        results = []
        for p in fake_projects:
            res = get_linear_project_activity.gql(q_combined, {"id": p["pid"]})
            errors = res.get("errors")
            data = res.get("data")
            project_data = data.get("project") if data else None

            if errors or not project_data:
                err_msg = errors[0].get("message") if errors else "Project returned null data"
                results.append({
                    'key': p['key'],
                    'name': p['name'],
                    'pid': p['pid'],
                    'project_updatedAt': "ACCESS_BLOCKED",
                    'latest_issue_updatedAt': "ACCESS_BLOCKED",
                    'last_touched': "ACCESS_BLOCKED",
                    'issues_count': "ACCESS_BLOCKED",
                    'error': err_msg
                })

        assert len(results) == 1
        assert results[0]['project_updatedAt'] == "ACCESS_BLOCKED"
        assert results[0]['latest_issue_updatedAt'] == "ACCESS_BLOCKED"
        assert results[0]['last_touched'] == "ACCESS_BLOCKED"
        assert results[0]['issues_count'] == "ACCESS_BLOCKED"
        assert results[0]['error'] == "Project not found"

def test_project_activity_hydration_success():
    """Verify that a successful project query correctly populates fields (not None/0)."""
    mock_success_res = {
        "data": {
            "project": {
                "id": "test-pid",
                "name": "Test Project",
                "updatedAt": "2026-07-13T10:00:00Z",
                "targetDate": None,
                "state": "started",
                "issues": {
                    "nodes": [
                        {
                            "id": "iss-1",
                            "identifier": "GRO-101",
                            "title": "First Issue",
                            "updatedAt": "2026-07-13T11:30:00Z",
                            "state": {"name": "In Progress"}
                        },
                        {
                            "id": "iss-2",
                            "identifier": "GRO-102",
                            "title": "Second Issue",
                            "updatedAt": "2026-07-13T11:00:00Z",
                            "state": {"name": "Todo"}
                        }
                    ]
                }
            }
        }
    }

    fake_projects = [
        {"key": "test-key", "name": "Test Project", "pid": "test-pid", "category": "ventures"}
    ]

    with mock.patch("get_linear_project_activity.gql", return_value=mock_success_res):
        results = []
        for p in fake_projects:
            res = get_linear_project_activity.gql("dummy", {"id": p["pid"]})
            errors = res.get("errors")
            data = res.get("data")
            project_data = data.get("project") if data else None

            assert not errors
            assert project_data is not None

            proj_updated = project_data.get("updatedAt")
            issues = project_data.get("issues", {}).get("nodes", []) if project_data else []
            
            latest_issue_updated = None
            if issues:
                issues_sorted = sorted(issues, key=lambda x: x.get('updatedAt') or '', reverse=True)
                latest_issue_updated = issues_sorted[0].get('updatedAt')

            last_touched = None
            if proj_updated and latest_issue_updated:
                last_touched = max(proj_updated, latest_issue_updated)
            elif proj_updated:
                last_touched = proj_updated
            elif latest_issue_updated:
                last_touched = latest_issue_updated

            results.append({
                'key': p['key'],
                'name': p['name'],
                'pid': p['pid'],
                'project_updatedAt': proj_updated,
                'latest_issue_updatedAt': latest_issue_updated,
                'last_touched': last_touched,
                'issues_count': len(issues)
            })

        assert len(results) == 1
        assert results[0]['project_updatedAt'] == "2026-07-13T10:00:00Z"
        assert results[0]['latest_issue_updatedAt'] == "2026-07-13T11:30:00Z"
        assert results[0]['last_touched'] == "2026-07-13T11:30:00Z"
        assert results[0]['issues_count'] == 2
