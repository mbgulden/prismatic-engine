"""Unit tests for LinearTaskProvider.update_issue_state.

The GraphQL transport (``_graphql_data``) is faked — no live Linear calls
are ever made.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from prismatic.providers.tasks.linear import LinearTaskProvider


class FakeTransportProvider(LinearTaskProvider):
    """LinearTaskProvider with a scripted in-memory GraphQL transport."""

    def __init__(self, handler=None):
        super().__init__()
        self._handler = handler
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def _graphql_data(self, query, variables=None):
        self.calls.append((query, variables))
        assert self._handler is not None, "no handler scripted"
        return self._handler(query, variables)


def _states_payload(
    identifier="GRO-5105", names=("Backlog", "Todo", "In Review", "Done")
):
    return {
        "issue": {
            "identifier": identifier,
            "team": {
                "id": "team-1",
                "states": {
                    "nodes": [
                        {"id": f"state-{n.lower().replace(' ', '-')}", "name": n}
                        for n in names
                    ]
                },
            },
        }
    }


def _mutation_payload(identifier="GRO-5105", state="Done"):
    return {
        "issueUpdate": {
            "success": True,
            "issue": {
                "identifier": identifier,
                "state": {"name": state},
                "url": f"https://linear.app/acme/issue/{identifier}",
            },
        }
    }


def _ok_provider(monkeypatch, identifier="GRO-5105", state_name="Done"):
    """Provider whose transport answers the states query + mutation."""

    def handler(query, variables):
        if "IssueTeamStates" in query:
            return _states_payload(identifier)
        if "IssueUpdateState" in query:
            assert variables["id"] == identifier
            assert variables["stateId"] == "state-done"
            return _mutation_payload(identifier, state_name)
        raise AssertionError(f"unexpected GraphQL operation: {query[:80]}")

    monkeypatch.setenv("LINEAR_API_KEY", "test-key")
    return FakeTransportProvider(handler)


def test_update_issue_state_resolves_name_to_id_and_transitions(monkeypatch):
    provider = _ok_provider(monkeypatch)
    resp = provider.update_issue_state("GRO-5105", state_name="Done")
    assert resp["ok"] is True
    assert resp["issue"] == "GRO-5105"
    assert resp["state"] == "Done"
    assert len(provider.calls) == 2  # states lookup, then mutation


def test_update_issue_state_matches_state_name_case_insensitively(monkeypatch):
    provider = _ok_provider(monkeypatch)
    resp = provider.update_issue_state("GRO-5105", state_name="done")
    assert resp["ok"] is True
    assert resp["state"] == "Done"


def test_update_issue_state_unknown_state_reports_available(monkeypatch):
    provider = _ok_provider(monkeypatch)
    resp = provider.update_issue_state("GRO-5105", state_name="Shipped")
    assert "error" in resp
    assert "Shipped" in resp["error"]
    assert "Done" in resp["error"]  # available states are listed
    # No mutation was attempted.
    assert len(provider.calls) == 1


def test_update_issue_state_issue_not_found(monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "test-key")
    provider = FakeTransportProvider(lambda q, v: {"issue": None})
    resp = provider.update_issue_state("GRO-9999")
    assert resp == {"error": "Issue not found: GRO-9999"}


def test_update_issue_state_unconfigured_is_a_loud_skip(monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    provider = LinearTaskProvider()
    resp = provider.update_issue_state("GRO-5105")
    assert resp == {"error": "LINEAR_API_KEY not set; transition skipped"}


def test_update_issue_state_transport_failure(monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", "test-key")
    provider = FakeTransportProvider(lambda q, v: None)
    resp = provider.update_issue_state("GRO-5105")
    assert resp == {"error": "Issue not found: GRO-5105"}


def test_update_issue_state_mutation_failure(monkeypatch):
    def handler(query, variables):
        if "IssueTeamStates" in query:
            return _states_payload()
        return {"issueUpdate": {"success": False, "issue": None}}

    monkeypatch.setenv("LINEAR_API_KEY", "test-key")
    provider = FakeTransportProvider(handler)
    resp = provider.update_issue_state("GRO-5105")
    assert resp == {"error": "issueUpdate failed for GRO-5105"}


def test_update_issue_state_method_exists_on_provider():
    assert callable(LinearTaskProvider.update_issue_state)


def test_dead_linear_helpers_import_is_gone():
    """Regression: the transition path must never import linear_helpers again.

    That module never existed in the repo or the gateway venv, so every
    call site that imported it failed with ModuleNotFoundError and the
    transition-to-Done path was silently dead.
    """
    repo = Path(__file__).resolve().parents[4]
    for rel in (
        "prismatic/review_factory/linear_hooks.py",
        "pe/deploy/linear_transition.py",
        "prismatic/deploy/linear_transition.py",
    ):
        src = (repo / rel).read_text(encoding="utf-8")
        assert "linear_helpers" not in src, (
            f"{rel} still references the dead linear_helpers module"
        )
