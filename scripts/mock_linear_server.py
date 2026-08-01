#!/usr/bin/env python3
"""
Mock Linear API server for Prismatic integration sandbox testing.
Simulates issues, comments, label assignments, and transitions.
Runs on a configurable port (default 9001).

Part of the Prismatic Canary Test Harness — used to validate agent dispatch
and pipeline logic without hitting the real Linear API.

Usage:
    python3 scripts/mock_linear_server.py [--port PORT]

Once running, issue queries hit localhost:PORT/graphql with mocked responses.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── Mock Data ───────────────────────────────────────────

MOCK_LABELS = [
    {"id": "label-ned-id", "name": "agent:ned"},
    {"id": "label-fred-id", "name": "agent:fred"},
    {"id": "label-canary-id", "name": "prismatic-canary"},
    {"id": "label-done-id", "name": "agent:done"},
]

MOCK_STATES = [
    {"id": "state-todo-id", "name": "Todo", "type": "unstarted"},
    {"id": "state-ip-id", "name": "In Progress", "type": "started"},
    {"id": "state-done-id", "name": "Done", "type": "completed"},
]

MOCK_ISSUES: dict[str, dict] = {
    "issue-101": {
        "id": "issue-101",
        "identifier": "NED-101",
        "title": "[Ned] Canary: Sandbox integration test",
        "description": "Verify agent dispatch, label swap, and state transitions.",
        "state": {"id": "state-todo-id", "name": "Todo"},
        "labels": {"nodes": [{"id": "label-ned-id", "name": "agent:ned"}]},
    }
}


class MockLinearHandler(BaseHTTPRequestHandler):
    """Handles GraphQL POST requests with mocked responses."""

    def log_message(self, format, *args):
        """Suppress default logging — use our own format."""
        pass

    def do_POST(self):
        if self.path != "/graphql":
            self.send_error(404)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")

        try:
            payload = json.loads(body)
            query = payload.get("query", "")
        except json.JSONDecodeError:
            self._send_json({"errors": [{"message": "Invalid JSON"}]}, 400)
            return

        response = self._handle_query(query)
        self._send_json(response, 200)

    def _handle_query(self, query: str) -> dict:
        """Route the query to the appropriate mock handler."""
        q = query.strip().replace("\\n", " ")

        # Team issues query
        if "team(" in q and "issues(" in q and "nodes" in q:
            return {
                "data": {
                    "team": {
                        "issues": {
                            "nodes": list(MOCK_ISSUES.values()),
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            }

        # Single issue query
        if "issue(id:" in q or 'issue(id:' in q:
            import re
            match = re.search(r'issue\\(id:\\s*"([^"]+)"\\)', q)
            if match:
                issue_id = match.group(1)
                issue = MOCK_ISSUES.get(issue_id)
                if issue:
                    return {"data": {"issue": issue}}

        # issueUpdate mutation
        if "issueUpdate(" in q:
            return {"data": {"issueUpdate": {"success": True}}}

        # commentCreate mutation
        if "commentCreate(" in q:
            return {
                "data": {
                    "commentCreate": {
                        "success": True,
                        "comment": {"id": "comment-canary-1"},
                    }
                }
            }

        # Labels query
        if "issueLabels(" in q:
            return {"data": {"issueLabels": {"nodes": MOCK_LABELS}}}

        # States query
        if "states(" in q:
            return {"data": {"team": {"states": {"nodes": MOCK_STATES}}}}

        # Generic fallback for team queries — return issues
        if "team(" in q and ("issues" in q or "labels" in q or "states" in q):
            return {
                "data": {
                    "team": {
                        "issues": {
                            "nodes": list(MOCK_ISSUES.values()),
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            }

        # Labels fallback
        if "labels" in q.lower():
            return {"data": {"issueLabels": {"nodes": MOCK_LABELS}}}

        # Unknown — return empty
        return {"data": {}}

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(
        description="Mock Linear API server for Prismatic canary testing"
    )
    parser.add_argument(
        "--port", type=int, default=9001, help="Port to listen on (default: 9001)"
    )
    args = parser.parse_args()

    server = HTTPServer(("127.0.0.1", args.port), MockLinearHandler)
    print(f"🧪 Mock Linear API running on http://127.0.0.1:{args.port}/graphql")
    print(f"   Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Shutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
