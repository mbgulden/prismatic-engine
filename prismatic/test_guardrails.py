from __future__ import annotations

from pathlib import Path

from prismatic.guardrails import (
    GuardrailStatus,
    ReplayQueue,
    SmokeCheck,
    SmokeSuite,
    detect_silent_stalls,
    rollout_gate,
)


def test_replay_queue_backfills_in_original_order(tmp_path: Path) -> None:
    queue = ReplayQueue(tmp_path / "replay.jsonl")
    queue.append("1", "ingest", {"issue": "GRO-1"})
    queue.append("2", "dispatch", {"agent": "ned"})
    seen: list[tuple[str, str]] = []

    result = queue.replay(
        {
            "ingest": lambda record: seen.append(
                (record.event_id, record.payload["issue"])
            ),
            "dispatch": lambda record: seen.append(
                (record.event_id, record.payload["agent"])
            ),
        }
    )

    assert result.status is GuardrailStatus.PASS
    assert result.succeeded == ["1", "2"]
    assert seen == [("1", "GRO-1"), ("2", "ned")]


def test_replay_queue_records_forced_failure(tmp_path: Path) -> None:
    queue = ReplayQueue(tmp_path / "replay.jsonl")
    queue.append("bad", "artifact", {"path": "missing"})

    result = queue.replay(
        {
            "artifact": lambda record: (_ for _ in ()).throw(
                RuntimeError("artifact offline")
            )
        }
    )

    assert result.status is GuardrailStatus.FAIL
    assert result.failed == {"bad": "artifact offline"}
    [record] = queue.load()
    assert record.attempts == 1
    assert record.last_error == "artifact offline"


def test_smoke_suite_proves_live_path_and_fails_fast() -> None:
    suite = SmokeSuite(
        [
            SmokeCheck("ingest", lambda: (True, "webhook accepted")),
            SmokeCheck("dispatch", lambda: (False, "no worker claimed task")),
            SmokeCheck("artifact", lambda: True),
            SmokeCheck("state_sync", lambda: (True, "linear updated")),
        ]
    )

    result = suite.run()

    assert result.status is GuardrailStatus.FAIL
    assert result.checks["dispatch"] == {
        "passed": False,
        "detail": "no worker claimed task",
    }
    assert result.checks["artifact"]["passed"] is True


def test_silent_stall_alert_has_concrete_path() -> None:
    alert = detect_silent_stalls(
        {"ingest": 100.0, "dispatch": 10.0},
        now=400.0,
        max_age_seconds=120.0,
        alert_path="Linear comment + ops feed",
    )

    assert alert.status is GuardrailStatus.FAIL
    assert alert.stalled_keys == ["dispatch", "ingest"]
    assert "Linear comment + ops feed" in alert.message


def test_rollout_gate_requires_green_smoke_replay_stalls_approval_and_rollback(
    tmp_path: Path,
) -> None:
    queue = ReplayQueue(tmp_path / "replay.jsonl")
    queue.append("ok", "ingest", {})
    replay = queue.replay({"ingest": lambda record: None})
    smoke = SmokeSuite([SmokeCheck("ingest", lambda: True)]).run()
    stalls = detect_silent_stalls({"ingest": 10.0}, now=20.0, max_age_seconds=120.0)

    blocked = rollout_gate(
        smoke=smoke,
        replay=replay,
        stalls=stalls,
        manual_approval=False,
        rollback_target=None,
    )
    assert blocked.go is False
    assert "manual rollout approval missing" in blocked.reasons
    assert "rollback target missing" in blocked.reasons

    allowed = rollout_gate(
        smoke=smoke,
        replay=replay,
        stalls=stalls,
        manual_approval=True,
        rollback_target="previous-release",
    )
    assert allowed.go is True
    assert allowed.status is GuardrailStatus.PASS
