from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from prismatic import dispatcher
from prismatic.local_tasks import LocalTaskQueue

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "handoff-contracts"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_handoff_dispatch_preflight_allows_valid_packet() -> None:
    issue = {"handoff_packet": load_fixture("pass.json")}

    result = dispatcher.handoff_dispatch_preflight(issue, "agy", "GRO-549")

    assert result is not None
    assert result.ok is True
    assert result.status == "allowed"
    assert result.reason == "handoff_contract_valid"


def test_handoff_dispatch_preflight_blocks_missing_result_before_dispatch() -> None:
    issue = {"handoff_packet": load_fixture("missing-result.json")}

    result = dispatcher.handoff_dispatch_preflight(issue, "fred", "GRO-549")

    assert result is not None
    assert result.ok is False
    assert result.status == "blocked"
    assert result.reason == "handoff_contract_invalid"
    assert any("missing required result artifacts" in error for error in result.errors)


def test_handoff_dispatch_preflight_blocks_out_of_lane_before_dispatch() -> None:
    issue = {"handoff_packet": load_fixture("out-of-lane.json")}

    result = dispatcher.handoff_dispatch_preflight(issue, "agy", "GRO-549")

    assert result is not None
    assert result.ok is False
    assert result.status == "blocked"
    assert any("changed path outside allowed_paths" in error for error in result.errors)


def test_handoff_dispatch_preflight_blocks_production_claim_without_proof() -> None:
    issue = {"handoff_packet": load_fixture("production-proof-missing.json")}

    result = dispatcher.handoff_dispatch_preflight(issue, "fred", "GRO-549")

    assert result is not None
    assert result.ok is False
    assert result.status == "blocked"
    assert any("missing production_proof artifacts" in error for error in result.errors)


def test_handoff_dispatch_preflight_routes_empty_target_to_manual_review() -> None:
    issue = {"handoff_packet": load_fixture("ambiguous-target-agent.json")}

    result = dispatcher.handoff_dispatch_preflight(issue, "agy", "GRO-549")

    assert result is not None
    assert result.ok is False
    assert result.status == "needs_manual_review"
    assert result.reason == "ambiguous_target_agent"


def test_dispatch_local_tasks_fails_closed_for_invalid_handoff_packet(
    tmp_path, monkeypatch
) -> None:
    queue = LocalTaskQueue(tmp_path / "event_router.db")
    task = queue.create(
        title="Bad handoff",
        agent="fred",
        workspace=tmp_path,
        metadata={"handoff_packet": load_fixture("missing-result.json")},
    )
    launched: list[str] = []

    def fake_launcher(issue_id: str, **kwargs: Any) -> bool:
        launched.append(issue_id)
        return True

    monkeypatch.setattr(dispatcher, "AGENT_LAUNCHERS", {"fred": fake_launcher})

    dispatched = dispatcher.dispatch_local_tasks(dedup=None, local_task_queue=queue)

    stored = queue.get(task.id)
    assert dispatched == 0
    assert launched == []
    assert stored.status == "blocked"
    assert stored.metadata["handoff_preflight_reason"] == "handoff_contract_invalid"
    assert any(
        "missing required result artifacts" in error
        for error in stored.metadata["handoff_preflight_errors"]
    )


def test_dispatch_local_tasks_routes_ambiguous_target_to_manual_review(
    tmp_path, monkeypatch
) -> None:
    queue = LocalTaskQueue(tmp_path / "event_router.db")
    task = queue.create(
        title="Ambiguous handoff",
        agent="agy",
        workspace=tmp_path,
        metadata={"handoff_packet": load_fixture("ambiguous-target-agent.json")},
    )
    launched: list[str] = []

    def fake_launcher(issue_id: str, **kwargs: Any) -> bool:
        launched.append(issue_id)
        return True

    monkeypatch.setattr(dispatcher, "AGENT_LAUNCHERS", {"agy": fake_launcher})

    dispatched = dispatcher.dispatch_local_tasks(dedup=None, local_task_queue=queue)

    stored = queue.get(task.id)
    assert dispatched == 0
    assert launched == []
    assert stored.status == "needs_manual_review"
    assert stored.metadata["handoff_preflight_reason"] == "ambiguous_target_agent"
