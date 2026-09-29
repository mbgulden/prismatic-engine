"""Tests for prismatic.run_account — the readable run account.

The fixture mirrors the real AgentRunRecord + ExecutionEvidence schema, i.e.
the exact dict shape produced by ``prismatic.gateway.server._run_record_to_dict``
(verified against main 2026-09-28).
"""

from prismatic.run_account import render_run_account, to_text


def _fixture_record():
    return {
        "run_id": "run-abc123",
        "issue_id": "GRO-1981",
        "agent_name": "builder",
        "status": "completed",
        "started_at": "2026-09-28T12:03:10+00:00",
        "completed_at": "2026-09-28T12:04:25+00:00",
        "output_path": None,
        "error_message": None,
        "evidence": {
            "task_id": "GRO-1981",
            "run_id": "run-abc123",
            "status": "partially_verified",
            "scope": "unit_tests",
            "summary": "Implemented the endpoint; one test still failing.",
            "commands": [
                {
                    "command": "pytest tests/test_endpoint.py -q",
                    "exit_code": 1,
                    "scope": "unit_tests",
                    "output_excerpt": "1 failed, 12 passed in 3.2s",
                },
                {
                    "command": "ruff check src/endpoint.py",
                    "exit_code": 0,
                    "scope": "lint",
                    "output_excerpt": "All checks passed!",
                },
                # adversarial: exit code never recorded
                {"command": "deploy --dry-run", "exit_code": None},
            ],
            "artifacts": ["dist/endpoint-0.2.0.tar.gz"],
            "files_changed": ["src/endpoint.py", "tests/test_endpoint.py"],
            "external_side_effects": [],
            "cleanup_status": "clean",
            "failure_category": "test_failure",
            "blocker": "test_endpoint.py::test_rate_limit fails intermittently",
            "created_at": "2026-09-28T12:04:25+00:00",
        },
        "verification_status": "partially_verified",
        "verification_scope": "unit_tests",
        "failure_category": "test_failure",
        "cleanup_status": "clean",
        "done_gate_result": "blocked",
        "done_gate_errors": ["1 verification command failed"],
    }


def test_every_line_traces_to_record():
    account = render_run_account(_fixture_record())
    commands = _fixture_record()["evidence"]["commands"]
    assert account["command_count"] == len(commands) == 3
    sources = [t["source"] for t in account["timeline"]]
    # every rendered line cites run_id + command index — no drops, no extras
    assert sources == ["run-abc123#cmd-0", "run-abc123#cmd-1", "run-abc123#cmd-2"]
    # commands render in evidence order
    assert account["timeline"][0]["command"] == "pytest tests/test_endpoint.py -q"


def test_outcomes_rendered_honestly():
    account = render_run_account(_fixture_record())
    outcomes = [t["outcome"] for t in account["timeline"]]
    assert outcomes[0] == "failed (exit 1)"
    assert outcomes[1] == "ok"
    assert outcomes[2] == "not recorded"  # exit_code None → not invented


def test_deterministic():
    rec = _fixture_record()
    a1 = render_run_account(rec)
    a2 = render_run_account(dict(rec))
    assert to_text(a1) == to_text(a2)


def test_duration_computed():
    account = render_run_account(_fixture_record())
    assert account["duration"] == "1m 15s"


def test_empty_evidence():
    rec = _fixture_record()
    rec["evidence"] = None
    account = render_run_account(rec)
    assert account["timeline"] == []
    assert account["command_count"] == 0
    text = to_text(account)
    assert "No evidence recorded for this run." in text


def test_no_fabrication():
    rec = {
        "run_id": "run-bare",
        "agent_name": "x",
        "status": "pending",
        # no timestamps, no evidence at all
    }
    account = render_run_account(rec)
    assert account["duration"] == "not recorded"
    assert account["verification_status"] == "not recorded"
    text = to_text(account)
    assert "not recorded" in text
    # no invented dollar figures, no invented durations
    assert "$" not in text


def test_text_shape():
    text = to_text(render_run_account(_fixture_record()))
    assert text.startswith("Run account: run-abc123")
    assert "Agent: builder   Task: GRO-1981   Status: completed" in text
    assert "Verification commands (3):" in text
    assert "[run-abc123#cmd-0]" in text
    assert "Files changed:" in text
    assert "src/endpoint.py" in text
    assert "Verification: partially_verified" in text
    assert "Blocker: test_endpoint.py::test_rate_limit fails intermittently" in text
    assert "Done-gate error: 1 verification command failed" in text


def test_unparseable_timestamps_degrade():
    rec = _fixture_record()
    rec["started_at"] = "not-a-time"
    rec["completed_at"] = None
    account = render_run_account(rec)
    assert account["duration"] == "not recorded"
    assert account["started_at"] == "not-a-time"  # rendered verbatim, not crashed on
