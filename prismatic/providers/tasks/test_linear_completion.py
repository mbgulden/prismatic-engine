from prismatic.providers.tasks.linear import LinearTaskProvider


class FakeLinearProvider(LinearTaskProvider):
    def __init__(self):
        self._api_key = "test-key"
        self._team_id = "GRO"
        self.calls = []

    def _graphql_data(self, query, variables=None):
        self.calls.append((query, variables or {}))
        if "CompletionContext" in query:
            return {
                "issue": {
                    "id": "issue-1",
                    "identifier": "GRO-3492",
                    "labels": {
                        "nodes": [
                            {"id": "label-agent-ned", "name": "agent:ned"},
                            {"id": "label-priority", "name": "dispatch:priority"},
                        ]
                    },
                    "team": {
                        "id": "team-1",
                        "labels": {
                            "nodes": [
                                {"id": "label-agent-ned", "name": "agent:ned"},
                                {"id": "label-priority", "name": "dispatch:priority"},
                                {"id": "label-peer", "name": "agent:peer-review"},
                            ]
                        },
                    },
                }
            }
        if "TeamStates" in query:
            return {
                "team": {
                    "states": {
                        "nodes": [
                            {"id": "state-backlog", "name": "Backlog"},
                            {"id": "state-review", "name": "In Review"},
                        ]
                    }
                }
            }
        if "CommentCreate" in query:
            return {"commentCreate": {"success": True}}
        if "CompleteIssue" in query:
            return {"issueUpdate": {"success": True}}
        raise AssertionError(query)


def test_complete_issue_posts_summary_state_and_merges_labels():
    provider = FakeLinearProvider()

    ok = provider.complete_issue(
        "issue-1",
        "Implemented completion sync and verified label preservation.",
        final_state="In Review",
        add_label_names=["agent:peer-review"],
        remove_label_names=["agent:ned"],
    )

    assert ok is True
    comment_call = next(c for c in provider.calls if "CommentCreate" in c[0])
    assert comment_call[1] == {
        "issueId": "issue-1",
        "body": (
            "## Completion summary — GRO-3492\n\n"
            "Implemented completion sync and verified label preservation.\n\n"
            "- Final state: `In Review`\n"
            "- Added labels: `agent:peer-review`\n"
            "- Removed labels: `agent:ned`"
        ),
    }

    update_call = next(c for c in provider.calls if "CompleteIssue" in c[0])
    assert update_call[1]["id"] == "issue-1"
    assert update_call[1]["stateId"] == "state-review"
    assert update_call[1]["labelIds"] == ["label-priority", "label-peer"]


def test_complete_issue_preserves_all_labels_when_no_label_changes_requested():
    provider = FakeLinearProvider()

    ok = provider.complete_issue("issue-1", "Done.")

    assert ok is True
    update_call = next(c for c in provider.calls if "CompleteIssue" in c[0])
    assert update_call[1]["labelIds"] == ["label-agent-ned", "label-priority"]
    comment_call = next(c for c in provider.calls if "CommentCreate" in c[0])
    assert "Labels preserved" in comment_call[1]["body"]


def test_complete_issue_refuses_to_clobber_when_added_label_is_unknown():
    provider = FakeLinearProvider()

    ok = provider.complete_issue("issue-1", "Done.", add_label_names=["agent:missing"])

    assert ok is False
    assert not any("CommentCreate" in query for query, _ in provider.calls)
    assert not any("CompleteIssue" in query for query, _ in provider.calls)
