"""
Prismatic Engine — Linear Task Provider
========================================

GraphQL-based implementation of TaskProvider for Linear.app.
Uses only ``urllib.request`` (stdlib) — no external HTTP libraries.

ENVIRONMENT VARIABLES
---------------------
``LINEAR_API_KEY``
    Required.  Personal API key from Linear.app Settings → API.
    Passed as-is in the ``Authorization`` header (no "Bearer" prefix).

``LINEAR_TEAM_ID``
    Optional.  Default team key (e.g. ``"GRO"``) used when no ``team_id``
    is passed to the constructor.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.error
from typing import Any

from .base import TaskProvider, Issue

# ── GraphQL endpoint ────────────────────────────────────────────
LINEAR_API_URL = "https://api.linear.app/graphql"

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class LinearTaskProvider(TaskProvider):
    """Task provider backed by Linear.app's GraphQL API.

    Usage::

        provider = LinearTaskProvider(team_id="GRO")
        issues = provider.get_issues_with_label("pipeline:hermes")
        for issue in issues:
            print(f"{issue.identifier}: {issue.title}")
    """

    def __init__(self, team_id: str | None = None):
        self._api_key = os.environ.get("LINEAR_API_KEY", "")
        if not self._api_key:
            print("[LinearTaskProvider] WARNING: LINEAR_API_KEY not set")

        self._team_id = team_id or os.environ.get("LINEAR_TEAM_ID", "")

    # ── public API ─────────────────────────────────────────────

    def get_issues_with_label(self, label: str) -> list[Issue]:
        """Query all open issues that have a label with the given name."""
        query = """
        query IssuesWithLabel($label: String!) {
          issues(
            filter: {
              labels: { name: { eq: $label } }
              state: { type: { neq: "completed" } }
            }
          ) {
            nodes {
              id
              identifier
              title
              description
              state { name }
              labels { nodes { name } }
              team { name }
            }
          }
        }
        """
        variables = {"label": label}
        data = self._graphql_data(query, variables)
        if data is None:
            return []

        nodes = (
            data
            .get("issues", {})
            .get("nodes", [])
        )
        return [self._node_to_issue(n) for n in nodes]

    def add_comment(self, issue_id: str, body: str) -> bool:
        """Post a comment on a Linear issue."""
        query = """
        mutation CommentCreate($issueId: String!, $body: String!) {
          commentCreate(input: { issueId: $issueId, body: $body }) {
            success
          }
        }
        """
        variables = {"issueId": issue_id, "body": body}
        data = self._graphql_data(query, variables)
        if data is None:
            return False
        return (
            data
            .get("commentCreate", {})
            .get("success", False)
        )

    def set_labels(self, issue_id: str, label_ids: list[str]) -> bool:
        """Replace labels on an issue."""
        query = """
        mutation IssueUpdate($id: String!, $labelIds: [String!]!) {
          issueUpdate(id: $id, input: { labelIds: $labelIds }) {
            success
          }
        }
        """
        variables = {"id": issue_id, "labelIds": label_ids}
        data = self._graphql_data(query, variables)
        if data is None:
            return False
        return (
            data
            .get("issueUpdate", {})
            .get("success", False)
        )

    def get_issue(self, issue_id: str) -> Issue | None:
        """Fetch a single issue by its internal ID."""
        query = """
        query Issue($id: String!) {
          issue(id: $id) {
            id
            identifier
            title
            description
            state { name }
            labels { nodes { name } }
            team { name }
          }
        }
        """
        variables = {"id": issue_id}
        data = self._graphql_data(query, variables)
        if data is None:
            return None

        node = data.get("issue")
        if node is None:
            return None
        return self._node_to_issue(node)

    def create_issue(
        self,
        team_id: str,
        title: str,
        description: str = "",
        label_names: list[str] | None = None,
    ) -> Issue | None:
        """Create an issue on a team.

        ``team_id`` may be the team key (e.g. "GRO", as in ``LINEAR_TEAM_ID``)
        or the team UUID; it is resolved to the UUID before the mutation.
        Label names are resolved to IDs (created when missing) so callers
        can pass human-readable names.
        """
        team_uuid = self._resolve_team_id(team_id)
        if not team_uuid:
            return None
        label_ids: list[str] = []
        for name in label_names or []:
            label_id = self.get_label_id(name, team_id=team_uuid)
            if label_id:
                label_ids.append(label_id)
        query = """
        mutation IssueCreate(
          $teamId: String!, $title: String!, $description: String,
          $labelIds: [String!]
        ) {
          issueCreate(input: {
            teamId: $teamId, title: $title,
            description: $description, labelIds: $labelIds
          }) {
            success
            issue {
              id identifier title description
              state { name }
              labels { nodes { name } }
              team { name }
            }
          }
        }
        """
        variables = {
            "teamId": team_uuid,
            "title": title,
            "description": description,
            "labelIds": label_ids or None,
        }
        data = self._graphql_data(query, variables)
        if data is None:
            return None
        payload = data.get("issueCreate", {})
        if not payload.get("success"):
            return None
        node = payload.get("issue")
        if node is None:
            return None
        return self._node_to_issue(node)

    def get_label_id(
        self, label_name: str, team_id: str | None = None
    ) -> str | None:
        """Resolve a label name to its ID, creating it when missing."""
        # NOTE: Linear has no direct "label by name" lookup; list team
        # labels and match client-side.
        scope = self._resolve_team_id(team_id)
        if not scope:
            return None
        list_query = """
        query TeamLabels($teamId: String!) {
          team(id: $teamId) {
            labels { nodes { id name } }
          }
        }
        """
        data = self._graphql_data(list_query, {"teamId": scope})
        nodes = (data or {}).get("team", {}).get("labels", {}).get("nodes", [])
        for node in nodes:
            if node.get("name") == label_name:
                return node.get("id")
        create = """
        mutation LabelCreate($name: String!, $teamId: String!) {
          issueLabelCreate(input: { name: $name, teamId: $teamId }) {
            success
            issueLabel { id }
          }
        }
        """
        created = self._graphql_data(
            create, {"name": label_name, "teamId": scope}
        )
        payload = (created or {}).get("issueLabelCreate", {})
        if payload.get("success"):
            return (payload.get("issueLabel") or {}).get("id")
        return None

    def _resolve_team_id(self, team_id: str | None = None) -> str | None:
        """Resolve a team key (e.g. "GRO") or UUID to the team UUID."""
        raw = (team_id or self._team_id or "").strip()
        if not raw:
            return None
        if _UUID_RE.fullmatch(raw):
            return raw
        query = """
        query TeamByKey($key: String!) {
          teams(filter: { key: { eq: $key } }) {
            nodes { id key }
          }
        }
        """
        data = self._graphql_data(query, {"key": raw})
        nodes = (data or {}).get("teams", {}).get("nodes", [])
        for node in nodes:
            if node.get("key") == raw and node.get("id"):
                return node["id"]
        return None

    # ── GraphQL transport ──────────────────────────────────────

    def _graphql_data(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Execute a GraphQL query and return the ``data`` dict only.

        This is a safe wrapper around ``_graphql()`` that extracts
        the ``data`` key, returning ``None`` if the response contains
        errors, has no data, or the network request fails.

        Linear (and most GraphQL APIs) return::

            { "data": { ... }, "errors": [...] }

        even on partial success.  This helper treats any presence of
        ``errors`` as a failure to keep the calling code simple.
        """
        result = self._graphql(query, variables)
        if result is None:
            return None

        # Treat any errors as failure — don't trust partial data
        if "errors" in result:
            return None

        data = result.get("data")
        if not isinstance(data, dict):
            return None

        return data

    def _graphql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Execute a GraphQL query against the Linear API.

        Returns the parsed JSON response dict, or None on any error.
        Prints diagnostic information on failure.
        """
        if not self._api_key:
            print("[LinearTaskProvider] No API key — skipping request")
            return None

        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            LINEAR_API_URL,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": self._api_key,   # No "Bearer" prefix
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                result: dict[str, Any] = json.loads(body)

                # Check for GraphQL-level errors
                if "errors" in result:
                    for err in result["errors"]:
                        msg = err.get("message", "Unknown error")
                        print(f"[LinearTaskProvider] GraphQL error: {msg}")

                return result

        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")[:500]
            print(
                f"[LinearTaskProvider] HTTP {exc.code}: {body}"
            )
            return None

        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            print(f"[LinearTaskProvider] Network error: {exc}")
            return None

        except json.JSONDecodeError as exc:
            print(f"[LinearTaskProvider] JSON decode error: {exc}")
            return None

    # ── helpers ────────────────────────────────────────────────

    @staticmethod
    def _node_to_issue(node: dict[str, Any]) -> Issue:
        """Convert a Linear GraphQL node to a generic Issue."""
        labels = [
            lbl["name"]
            for lbl in node.get("labels", {}).get("nodes", [])
            if isinstance(lbl, dict)
        ]
        state = node.get("state", {})
        if isinstance(state, dict):
            state = state.get("name", "backlog")

        team = node.get("team", {})
        if isinstance(team, dict):
            team = team.get("name", "")

        return Issue(
            id=node.get("id", ""),
            identifier=node.get("identifier", ""),
            title=node.get("title", ""),
            description=node.get("description", "") or "",
            state=state,
            labels=labels,
            project=team,
        )
