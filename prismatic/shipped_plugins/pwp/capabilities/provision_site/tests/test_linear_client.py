"""Tests for LinearClient — Linear API wrapper used by PWP provision_site
funnel_config / edit-funnel flows (Phase 4).

Coverage:
  - from_env precedence (LINEAR_API_KEY > LINEAR_PERSONAL_TOKEN > LINEAR_TOKEN)
  - direct construction rejects empty
  - retry-on-429 / 5xx
  - create_issue happy path
  - add_comment happy path
  - get_issue_status happy path + not-found
  - assign_agent happy path
  - lookup_user_id_by_email
  - list_issues_by_label (uses GraphQL variables — no string injection)
  - find_issue_by_title dedup
  - GraphQL errors are surfaced as LinearError
  - retry budget exhaustion surfaces LinearError, not silent
"""

from __future__ import annotations

import io
import http.client
import json
import os
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from prismatic.shipped_plugins.pwp.capabilities.provision_site.linear_client import (
    CreateIssueInput,
    LINEAR_API_URL,
    LinearClient,
    LinearError,
    LinearIssue,
)


# --- helpers --------------------------------------------------------------

def _http_response(status: int, body: dict) -> urllib.error.HTTPError:
    """Construct an HTTPError with a JSON body — mirrors urllib.request.urlopen's
    contract on the HTTPError exception path."""
    return urllib.error.HTTPError(
        "https://api.linear.app/graphql",
        status,
        "TestStatus",
        http.client.HTTPMessage(),  # hdrs
        io.BytesIO(json.dumps(body).encode("utf-8")),  # fp
    )


def _ok_response(body: dict):
    """Mock-like object that mimics urllib's context manager (with `.read`)."""
    class _Resp:
        def __init__(self, payload: bytes):
            self._payload = payload

        def read(self) -> bytes:
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    return _Resp(json.dumps(body).encode("utf-8"))


# --- from_env precedence --------------------------------------------------

def test_from_env_prefers_linear_api_key(monkeypatch) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "primary")
    monkeypatch.setenv("LINEAR_PERSONAL_TOKEN", "secondary")
    monkeypatch.setenv("LINEAR_TOKEN", "tertiary")
    c = LinearClient.from_env()
    assert c.token_source == "LINEAR_API_KEY"


def test_from_env_falls_back_to_linear_personal_token(monkeypatch) -> None:
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.setenv("LINEAR_PERSONAL_TOKEN", "secondary")
    monkeypatch.delenv("LINEAR_TOKEN", raising=False)
    c = LinearClient.from_env()
    assert c.token_source == "LINEAR_PERSONAL_TOKEN"


def test_from_env_falls_back_to_linear_token(monkeypatch) -> None:
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("LINEAR_PERSONAL_TOKEN", raising=False)
    monkeypatch.setenv("LINEAR_TOKEN", "tertiary")
    c = LinearClient.from_env()
    assert c.token_source == "LINEAR_TOKEN"


def test_from_env_raises_when_no_token(monkeypatch) -> None:
    for k in ("LINEAR_API_KEY", "LINEAR_PERSONAL_TOKEN", "LINEAR_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ValueError, match="LINEAR_API_KEY"):
        LinearClient.from_env()


def test_direct_construction_rejects_empty() -> None:
    with pytest.raises(ValueError):
        LinearClient(api_key="")


def test_api_url_constant() -> None:
    assert LINEAR_API_URL == "https://api.linear.app/graphql"


# --- request shape --------------------------------------------------------

def test_request_shape_and_authorization_header(monkeypatch) -> None:
    """The Authorization header must carry the raw api_key (no Bearer prefix)
    and Content-Type must be application/json."""
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        # `req.headers` is an http.client.Message; iterate to get all headers
        # case-preserved (dict() folds case).
        captured["headers"] = {k: v for (k, v) in req.header_items()}
        captured["body"] = req.data.decode("utf-8")
        return _ok_response({"data": {"viewer": {"id": "u1", "name": "x"}}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = LinearClient(api_key="test-key")
    client._request("{ viewer { id name } }")
    assert captured["url"] == LINEAR_API_URL
    assert captured["method"] == "POST"
    assert captured["headers"].get("Authorization") == "test-key"
    assert captured["headers"].get("Content-type") == "application/json"
    # Body should be a JSON-encoded GraphQL payload
    body = json.loads(captured["body"])
    assert "query" in body


# --- create_issue ---------------------------------------------------------

def test_create_issue_happy_path(monkeypatch) -> None:
    """create_issue should POST an IssueCreate mutation and return a typed
    LinearIssue with the parsed fields."""
    monkeypatch.setattr("urllib.request.urlopen", lambda req, **kw: _ok_response({
        "data": {
            "issueCreate": {
                "success": True,
                "issue": {
                    "id": "i-1",
                    "identifier": "GRO-4357",
                    "title": "Test issue",
                    "url": "https://linear.app/growthwebdev/issue/GRO-4357",
                    "state": {"id": "s1", "name": "Todo", "type": "unstarted"},
                    "labels": {"nodes": [{"name": "plugin:pwp"}]},
                    "parent": {"id": "i-0"},
                    "assignee": {"id": "u-1", "name": "Ned"},
                    "team": {"id": "t-1", "key": "GRO"},
                },
            },
        },
    }))
    client = LinearClient(api_key="k")
    issue = client.create_issue(CreateIssueInput(
        team_id="t-1",
        title="Test issue",
        description="Body",
        label_ids=["lab-1"],
    ))
    assert isinstance(issue, LinearIssue)
    assert issue.identifier == "GRO-4357"
    assert issue.title == "Test issue"
    assert issue.state == "Todo"
    assert issue.state_type == "unstarted"
    assert issue.parent_id == "i-0"
    assert issue.assignee_name == "Ned"
    assert issue.team_key == "GRO"
    assert "plugin:pwp" in issue.labels


def test_create_issue_reports_graphql_errors(monkeypatch) -> None:
    """A GraphQL errors[] response (HTTP 200) must surface as LinearError."""
    monkeypatch.setattr("urllib.request.urlopen", lambda req, **kw: _ok_response({
        "errors": [{"message": "Permission denied", "extensions": {"code": "FORBIDDEN"}}],
        "data": None,
    }))
    client = LinearClient(api_key="k")
    with pytest.raises(LinearError, match="Permission denied"):
        client.create_issue(CreateIssueInput(team_id="t-1", title="x"))


def test_create_issue_reports_http_4xx(monkeypatch) -> None:
    """A non-retryable 4xx HTTP error must surface as LinearError with status."""
    def fake_urlopen(req, timeout=None):
        raise _http_response(401, {"errors": [{"message": "Unauthorized"}]})
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = LinearClient(api_key="k", max_retries=0)
    with pytest.raises(LinearError) as excinfo:
        client.create_issue(CreateIssueInput(team_id="t-1", title="x"))
    assert excinfo.value.status == 401


def test_create_issue_retries_on_429(monkeypatch) -> None:
    """A 429 response should be retried (up to max_retries) and succeed if
    the next attempt returns 200."""
    call_count = {"n": 0}

    def fake_urlopen(req, timeout=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise _http_response(429, {"errors": [{"message": "rate limited"}]})
        return _ok_response({
            "data": {
                "issueCreate": {
                    "success": True,
                    "issue": {
                        "id": "i-1", "identifier": "GRO-1", "title": "x",
                        "url": "u", "state": {"id": "s", "name": "n", "type": "u"},
                        "labels": {"nodes": []}, "parent": None,
                        "assignee": None, "team": {"id": "t", "key": "GRO"},
                    },
                },
            },
        })

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    # Stub time.sleep so the test doesn't actually wait
    monkeypatch.setattr("time.sleep", lambda s: None)
    client = LinearClient(api_key="k", max_retries=2, retry_backoff=0.0)
    issue = client.create_issue(CreateIssueInput(team_id="t", title="x"))
    assert call_count["n"] == 2  # one 429, one success
    assert issue.identifier == "GRO-1"


def test_create_issue_exhausts_retries_on_persistent_429(monkeypatch) -> None:
    """If all retries return 429, the client must raise LinearError rather
    than silently returning a partial result."""
    call_count = {"n": 0}

    def fake_urlopen(req, timeout=None):
        call_count["n"] += 1
        raise _http_response(429, {"errors": [{"message": "rate limited"}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda s: None)
    client = LinearClient(api_key="k", max_retries=2, retry_backoff=0.0)
    with pytest.raises(LinearError) as excinfo:
        client.create_issue(CreateIssueInput(team_id="t", title="x"))
    assert call_count["n"] == 3  # 1 initial + 2 retries
    assert excinfo.value.status == 429


# --- add_comment ----------------------------------------------------------

def test_add_comment_happy_path(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _ok_response({"data": {"commentCreate": {"success": True, "comment": {"id": "c-1"}}}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = LinearClient(api_key="k")
    out = client.add_comment("i-1", "Hello world")
    assert out["comment"]["id"] == "c-1"
    assert captured["body"]["variables"]["input"]["issueId"] == "i-1"
    assert captured["body"]["variables"]["input"]["body"] == "Hello world"


def test_add_comment_propagates_failure(monkeypatch) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda req, **kw: _ok_response({
        "data": {"commentCreate": {"success": False, "comment": None}},
    }))
    client = LinearClient(api_key="k")
    with pytest.raises(LinearError):
        client.add_comment("i-1", "x")


# --- get_issue_status -----------------------------------------------------

def test_get_issue_status_happy_path(monkeypatch) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda req, **kw: _ok_response({
        "data": {
            "issue": {
                "id": "i-1", "identifier": "GRO-4357", "title": "x",
                "url": "u", "state": {"id": "s1", "name": "In Progress", "type": "started"},
                "labels": {"nodes": [{"name": "pipeline:dashboard-ui"}]},
                "parent": None, "assignee": {"id": "u1", "name": "Ned"},
                "team": {"id": "t", "key": "GRO"},
            },
        },
    }))
    client = LinearClient(api_key="k")
    issue = client.get_issue_status("i-1")
    assert issue.state == "In Progress"
    assert issue.state_type == "started"
    assert "pipeline:dashboard-ui" in issue.labels


def test_get_issue_status_not_found(monkeypatch) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda req, **kw: _ok_response({
        "data": {"issue": None},
    }))
    client = LinearClient(api_key="k")
    with pytest.raises(LinearError, match="not found"):
        client.get_issue_status("nonexistent")


# --- assign_agent ---------------------------------------------------------

def test_assign_agent_happy_path(monkeypatch) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda req, **kw: _ok_response({
        "data": {
            "issueUpdate": {
                "success": True,
                "issue": {
                    "id": "i-1", "identifier": "GRO-1", "title": "x",
                    "url": "u", "state": {"id": "s", "name": "n", "type": "u"},
                    "labels": {"nodes": []}, "parent": None,
                    "assignee": {"id": "u-99", "name": "New Assignee"},
                    "team": {"id": "t", "key": "GRO"},
                },
            },
        },
    }))
    client = LinearClient(api_key="k")
    issue = client.assign_agent("i-1", "u-99")
    assert issue.assignee_id == "u-99"
    assert issue.assignee_name == "New Assignee"


# --- lookup_user_id_by_email ---------------------------------------------

def test_lookup_user_id_by_email_found(monkeypatch) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda req, **kw: _ok_response({
        "data": {"users": {"nodes": [{"id": "u-42", "email": "ned@x.com"}]}},
    }))
    client = LinearClient(api_key="k")
    assert client.lookup_user_id_by_email("ned@x.com") == "u-42"


def test_lookup_user_id_by_email_not_found(monkeypatch) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda req, **kw: _ok_response({
        "data": {"users": {"nodes": []}},
    }))
    client = LinearClient(api_key="k")
    assert client.lookup_user_id_by_email("nobody@x.com") is None


# --- list_issues_by_label -------------------------------------------------

def test_list_issues_by_label_uses_graphql_variables(monkeypatch) -> None:
    """The label name MUST flow through GraphQL variables, not string
    interpolation — otherwise a label with quotes could inject GraphQL."""
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _ok_response({
            "data": {"issues": {"nodes": []}},
        })

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = LinearClient(api_key="k")
    # A label name containing characters that would break naive %-formatting
    weird = 'plugin:pwp" OR true -- '
    client.list_issues_by_label(team_id="t-1", label_name=weird)
    # The query body should NOT contain the literal weird string — only the
    # GraphQL variable `$labelName`. The actual value is in `variables`.
    assert "plugin:pwp" not in captured["body"]["query"] or "$labelName" in captured["body"]["query"]
    assert captured["body"]["variables"]["labelName"] == weird


def test_list_issues_by_label_returns_typed_issues(monkeypatch) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda req, **kw: _ok_response({
        "data": {"issues": {"nodes": [
            {
                "id": "i-1", "identifier": "GRO-1", "title": "Test",
                "url": "u", "state": {"id": "s", "name": "Todo", "type": "unstarted"},
                "labels": {"nodes": [{"name": "plugin:pwp"}]},
                "parent": None, "assignee": None,
                "team": {"id": "t", "key": "GRO"},
            },
        ]}},
    }))
    client = LinearClient(api_key="k")
    issues = client.list_issues_by_label(team_id="t", label_name="plugin:pwp")
    assert len(issues) == 1
    assert isinstance(issues[0], LinearIssue)
    assert issues[0].identifier == "GRO-1"


# --- find_issue_by_title --------------------------------------------------

def test_find_issue_by_title_case_insensitive(monkeypatch) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda req, **kw: _ok_response({
        "data": {"issues": {"nodes": [
            {
                "id": "i-1", "identifier": "GRO-1",
                "title": "[PE-KPI-FUNNEL] F1 — Build LinearClient wrapper",
                "url": "u", "state": {"id": "s", "name": "Todo", "type": "unstarted"},
                "labels": {"nodes": [{"name": "plugin:pwp"}]},
                "parent": {"id": "epic-1"}, "assignee": None,
                "team": {"id": "t", "key": "GRO"},
            },
        ]}},
    }))
    client = LinearClient(api_key="k")
    found = client.find_issue_by_title(
        team_id="t", title="[pe-kpi-funnel] f1 — build linearclient WRAPPER",
        parent_id="epic-1",
    )
    assert found is not None
    assert found.identifier == "GRO-1"
    assert found.parent_id == "epic-1"


def test_find_issue_by_title_returns_none_when_missing(monkeypatch) -> None:
    monkeypatch.setattr("urllib.request.urlopen", lambda req, **kw: _ok_response({
        "data": {"issues": {"nodes": []}},
    }))
    client = LinearClient(api_key="k")
    assert client.find_issue_by_title(team_id="t", title="nope") is None


# --- LinearIssue.to_dict roundtrip ----------------------------------------

def test_linear_issue_to_dict_roundtrip() -> None:
    issue = LinearIssue(
        id="i-1",
        identifier="GRO-1",
        title="t",
        url="u",
        state="Todo",
        state_id="s",
        state_type="unstarted",
        labels=["a", "b"],
        parent_id="p",
        assignee_id="u1",
        assignee_name="Ned",
        team_id="t",
        team_key="GRO",
    )
    d = issue.to_dict()
    assert json.dumps(d)  # JSON-serializable
    assert d["identifier"] == "GRO-1"
    assert d["labels"] == ["a", "b"]
