"""linear_client — minimal Linear GraphQL API client for PWP provision_site Phase 4.

Mirrors the shape of cloudflare_client.py / vercel_client.py so step
implementations can use a uniform idiom.

The Linear GraphQL API is documented at https://developers.linear.app/docs/graphql/working-with-the-graphql-api.
The base endpoint is https://api.linear.app/graphql. Authentication uses an
Authorization header with the raw API key value (no "Bearer" prefix — Linear
expects the token directly).

Rate limits: Linear's documented limit is 1500 requests per hour per user
and 300 per minute per resource. The client implements a small retry-on-429
loop with backoff to stay under the threshold during high-volume operations
(e.g. dashboard polling).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

LINEAR_API_URL = "https://api.linear.app/graphql"


class LinearError(RuntimeError):
    """Raised when the Linear API returns an error response.

    Attributes:
      status:   HTTP status code (0 if the request failed before HTTP).
      errors:   List of GraphQL error dicts (when available).
      message:  Human-readable summary.
    """
    def __init__(
        self,
        message: str,
        *,
        status: int = 0,
        errors: list[dict[str, Any]] | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.errors = errors or []


@dataclass(frozen=True)
class LinearIssue:
    """A typed view of a Linear issue.

    Carries the fields the PWP provision_site funnel_config flow needs.
    """
    id: str
    identifier: str          # e.g. "GRO-4356"
    title: str
    url: str
    state: str               # workflow state name ("Todo", "In Progress", ...)
    state_id: str
    state_type: str          # "backlog" | "unstarted" | "started" | "completed" | "canceled" | ...
    labels: list[str] = field(default_factory=list)
    parent_id: str | None = None
    assignee_id: str | None = None
    assignee_name: str | None = None
    team_id: str = ""
    team_key: str = ""

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> LinearIssue:
        state = data.get("state") or {}
        team = data.get("team") or {}
        assignee = data.get("assignee") or {}
        parent = data.get("parent") or {}
        labels_raw = data.get("labels") or {}
        # Linear returns labels either as a list of strings (when using a
        # custom query) or as a list of {nodes: [...]} (when paginated).
        if isinstance(labels_raw, dict) and "nodes" in labels_raw:
            label_names = [n.get("name", "") for n in labels_raw["nodes"]]
        elif isinstance(labels_raw, list):
            label_names = [
                n.get("name", "") if isinstance(n, dict) else str(n)
                for n in labels_raw
            ]
        else:
            label_names = []
        return cls(
            id=data["id"],
            identifier=data.get("identifier", ""),
            title=data.get("title", ""),
            url=data.get("url", ""),
            state=state.get("name", ""),
            state_id=state.get("id", ""),
            state_type=state.get("type", ""),
            labels=label_names,
            parent_id=parent.get("id"),
            assignee_id=assignee.get("id"),
            assignee_name=assignee.get("name"),
            team_id=team.get("id", ""),
            team_key=team.get("key", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "identifier": self.identifier,
            "title": self.title,
            "url": self.url,
            "state": self.state,
            "state_id": self.state_id,
            "state_type": self.state_type,
            "labels": list(self.labels),
            "parent_id": self.parent_id,
            "assignee_id": self.assignee_id,
            "assignee_name": self.assignee_name,
            "team_id": self.team_id,
            "team_key": self.team_key,
        }


@dataclass(frozen=True)
class CreateIssueInput:
    """Input for LinearClient.create_issue.

    Mirrors Linear's IssueCreateInput but simplified to the fields
    PWP actually uses. We expand here only when a feature needs it.
    """
    team_id: str
    title: str
    description: str = ""
    parent_id: str | None = None
    label_ids: list[str] = field(default_factory=list)
    assignee_id: str | None = None
    state_id: str | None = None
    priority: int | None = None  # 0 = No priority, 1 = Urgent, 2 = High, 3 = Medium, 4 = Low

    def to_graphql(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "teamId": self.team_id,
            "title": self.title,
        }
        if self.description:
            out["description"] = self.description
        if self.parent_id:
            out["parentId"] = self.parent_id
        if self.label_ids:
            out["labelIds"] = list(self.label_ids)
        if self.assignee_id:
            out["assigneeId"] = self.assignee_id
        if self.state_id:
            out["stateId"] = self.state_id
        if self.priority is not None:
            out["priority"] = self.priority
        return out


class LinearClient:
    """Minimal Linear GraphQL client.

    Usage:
        client = LinearClient.from_env()
        issue = client.create_issue(CreateIssueInput(
            team_id="...",
            title="...",
            description="...",
            label_ids=["..."],
        ))
    """

    def __init__(
        self,
        api_key: str,
        *,
        api_url: str = LINEAR_API_URL,
        max_retries: int = 2,
        retry_backoff: float = 1.0,
        token_source: str = "explicit",
    ):
        if not api_key:
            raise ValueError("LinearClient requires a non-empty api_key")
        self.api_key = api_key
        self.api_url = api_url
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.token_source = token_source

    # -- factory --------------------------------------------------------
    @classmethod
    def from_env(cls) -> LinearClient:
        """Construct from environment variables.

        Precedence: LINEAR_API_KEY > LINEAR_PERSONAL_TOKEN > LINEAR_TOKEN.
        All three are aliases for the same secret (Linear doesn't distinguish
        between API keys and personal tokens at the protocol level).
        """
        api_key = (
            os.environ.get("LINEAR_API_KEY")
            or os.environ.get("LINEAR_PERSONAL_TOKEN")
            or os.environ.get("LINEAR_TOKEN")
        )
        if not api_key:
            raise ValueError(
                "No Linear API token configured. Set LINEAR_API_KEY "
                "(preferred) or LINEAR_PERSONAL_TOKEN or LINEAR_TOKEN."
            )
        # Identify which env var provided the token for diagnostics.
        source = (
            "LINEAR_API_KEY" if os.environ.get("LINEAR_API_KEY")
            else "LINEAR_PERSONAL_TOKEN" if os.environ.get("LINEAR_PERSONAL_TOKEN")
            else "LINEAR_TOKEN"
        )
        return cls(api_key, token_source=source)

    # -- core HTTP ------------------------------------------------------
    def _request(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST a GraphQL query, retry on 429/5xx.

        Returns the parsed JSON `data` dict. Raises LinearError on failure.
        """
        body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")

        last_err: Exception | None = None
        # We always do at least one attempt; we retry up to max_retries
        # additional times on 429/5xx.
        total_attempts = max(1, self.max_retries + 1)
        for attempt in range(total_attempts):
            try:
                req = urllib.request.Request(
                    self.api_url,
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": self.api_key,
                        "User-Agent": "pwp-provision-site/0.1",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read().decode("utf-8")
                    payload = json.loads(raw)
                    if payload.get("errors"):
                        raise LinearError(
                            f"GraphQL errors: {payload['errors']}",
                            status=200,
                            errors=payload["errors"],
                        )
                    return payload.get("data") or {}
            except urllib.error.HTTPError as e:
                last_err = e
                # Read error body (best-effort)
                err_body = ""
                try:
                    err_body = e.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                try:
                    err_payload = json.loads(err_body) if err_body else {}
                except Exception:
                    err_payload = {}
                err_list = (
                    err_payload.get("errors", [])
                    if isinstance(err_payload, dict) else []
                )
                # Retry on 429 (rate-limited) or 5xx (server error).
                if e.code in (429, 500, 502, 503, 504) and attempt < total_attempts - 1:
                    sleep_s = self.retry_backoff * (2 ** attempt)
                    time.sleep(sleep_s)
                    continue
                raise LinearError(
                    f"Linear API HTTP {e.code}: {err_body[:300] or str(e)}",
                    status=e.code,
                    errors=err_list,
                ) from e
            except urllib.error.URLError as e:
                last_err = e
                if attempt < total_attempts - 1:
                    time.sleep(self.retry_backoff * (2 ** attempt))
                    continue
                raise LinearError(
                    f"Linear API connection error: {e.reason}",
                ) from e

        # Defensive: should never reach here because the for-loop either
        # returns (success) or raises (final attempt). If we do, surface
        # the last exception.
        raise LinearError(
            f"Linear API request failed after {total_attempts} attempts: "
            f"{last_err!r}"
        )

    # -- public API -----------------------------------------------------
    def create_issue(self, inp: CreateIssueInput) -> LinearIssue:
        """Create a new Linear issue.

        Returns the typed LinearIssue view.
        """
        mutation = """
mutation IssueCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue {
      id identifier title url
      state { id name type }
      labels { nodes { name } }
      parent { id }
      assignee { id name }
      team { id key }
    }
  }
}
"""
        data = self._request(mutation, {"input": inp.to_graphql()})
        result = data.get("issueCreate") or {}
        if not result.get("success"):
            raise LinearError(
                f"issueCreate returned success=False: {result}",
                status=200,
            )
        issue_raw = result.get("issue") or {}
        if not issue_raw.get("id"):
            raise LinearError(
                f"issueCreate returned no issue payload: {result}",
                status=200,
            )
        return LinearIssue.from_api(issue_raw)

    def add_comment(self, issue_id: str, body: str) -> dict[str, Any]:
        """Post a comment on an existing issue.

        Returns the raw `commentCreate` payload. Body is treated as plain
        text (Linear supports markdown but we don't enforce it here).
        """
        mutation = """
mutation CommentCreate($input: CommentCreateInput!) {
  commentCreate(input: $input) {
    success
    comment { id }
  }
}
"""
        data = self._request(mutation, {"input": {"issueId": issue_id, "body": body}})
        result = data.get("commentCreate") or {}
        if not result.get("success"):
            raise LinearError(
                f"commentCreate returned success=False: {result}",
                status=200,
            )
        return result

    def get_issue_status(self, issue_id: str) -> LinearIssue:
        """Fetch the current state of an issue (id, identifier, state, etc.).

        Used by the dashboard polling layer.
        """
        query = """
query IssueById($id: String!) {
  issue(id: $id) {
    id identifier title url
    state { id name type }
    labels { nodes { name } }
    parent { id }
    assignee { id name }
    team { id key }
  }
}
"""
        data = self._request(query, {"id": issue_id})
        issue_raw = data.get("issue")
        if issue_raw is None:
            raise LinearError(
                f"Issue not found: id={issue_id}", status=200,
            )
        return LinearIssue.from_api(issue_raw)

    def assign_agent(
        self,
        issue_id: str,
        assignee_id: str,
    ) -> LinearIssue:
        """Assign an issue to a user (the Linear user id, not the agent name).

        Use `lookup_user_id_by_email` if you only know the email.
        """
        mutation = """
mutation IssueUpdate($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) {
    success
    issue {
      id identifier title url
      state { id name type }
      labels { nodes { name } }
      parent { id }
      assignee { id name }
      team { id key }
    }
  }
}
"""
        data = self._request(
            mutation,
            {"id": issue_id, "input": {"assigneeId": assignee_id}},
        )
        result = data.get("issueUpdate") or {}
        if not result.get("success"):
            raise LinearError(
                f"issueUpdate returned success=False: {result}", status=200,
            )
        return LinearIssue.from_api(result.get("issue") or {})

    def lookup_user_id_by_email(self, email: str) -> str | None:
        """Find a Linear user id by email, or None if not found."""
        query = """
query UserByEmail($filter: UserFilter!) {
  users(filter: $filter, first: 1) {
    nodes { id email }
  }
}
"""
        data = self._request(
            query,
            {"filter": {"email": {"eq": email}}},
        )
        users = data.get("users", {}).get("nodes", [])
        if not users:
            return None
        return users[0]["id"]

    def list_issues_by_label(
        self,
        team_id: str,
        label_name: str,
        *,
        limit: int = 50,
    ) -> list[LinearIssue]:
        """List issues in `team_id` that have a label whose name contains
        `label_name` (case-insensitive substring match).

        Used by the funnel-config dispatcher to find the parent epic
        (e.g. labels with "PE-KPI-FUNNEL") and to dedupe submissions.
        """
        query = """
query ListIssues($teamId: ID!, $first: Int!, $labelName: String!) {
  issues(
    filter: {
      team: { id: { eq: $teamId } }
      labels: { name: { containsIgnoreCase: $labelName } }
    }
    first: $first
  ) {
    nodes {
      id identifier title url
      state { id name type }
      labels { nodes { name } }
      parent { id }
      assignee { id name }
      team { id key }
    }
  }
}
"""
        data = self._request(
            query,
            {"teamId": team_id, "first": int(limit), "labelName": label_name},
        )
        nodes = data.get("issues", {}).get("nodes", [])
        return [LinearIssue.from_api(n) for n in nodes]

    def find_issue_by_title(
        self,
        team_id: str,
        title: str,
        *,
        parent_id: str | None = None,
    ) -> LinearIssue | None:
        """Find an issue by exact (case-insensitive) title. Optionally
        scoped to children of a specific parent issue.

        Used by funnel_config to detect re-submissions: a second submission
        for the same site with the same title updates the existing task
        instead of creating a duplicate.
        """
        issues = self.list_issues_by_label(team_id=team_id, label_name="plugin:pwp")
        title_lower = title.lower()
        for issue in issues:
            if issue.title.lower() == title_lower:
                if parent_id is None or issue.parent_id == parent_id:
                    return issue
        return None
