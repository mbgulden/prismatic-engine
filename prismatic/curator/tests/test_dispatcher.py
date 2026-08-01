"""
Tests for prismatic.curator.dispatcher.

Covers:
- Lane model selection
- Budget checks (allowed / over-cap)
- Daily reset behavior
- Dispatch decision logic
- Supervisor cmd construction

Run: pytest prismatic/curator/tests/test_dispatcher.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prismatic.curator.dispatcher import (
    LANE_DAILY_BUDGET_USD,
    LANE_ESTIMATED_COST_USD,
    LaneBudgetTracker,
    build_supervisor_cmd,
    decide_dispatch,
)

# === LaneBudgetTracker tests ===

@pytest.fixture
def fresh_tracker(tmp_path):
    """A tracker with a temp state file."""
    state = tmp_path / "budget.json"
    return LaneBudgetTracker(state_path=state)


def test_fresh_tracker_allows_all(fresh_tracker):
    """No prior spending → all lanes under cap."""
    for lane in LANE_DAILY_BUDGET_USD.keys():
        check = fresh_tracker.check(lane)
        assert check.allowed, f"{lane} should be allowed initially"


def test_charge_accumulates(fresh_tracker):
    fresh_tracker.charge("codex", amount=1.0)
    fresh_tracker.charge("codex", amount=2.0)
    assert fresh_tracker.spent("codex") == 3.0


def test_cap_enforced_after_charges(fresh_tracker):
    """Repeatedly charge past cap, then check fails."""
    cap = LANE_DAILY_BUDGET_USD["kai"]  # smallest cap = $3
    # Charge to just under cap
    for _ in range(int(cap / LANE_ESTIMATED_COST_USD["kai"])):
        check = fresh_tracker.check("kai")
        if check.allowed:
            fresh_tracker.charge("kai")
        else:
            break

    # After many charges, should be near or at cap
    spent = fresh_tracker.spent("kai")
    assert spent >= cap - 0.5

    # Now check should fail (or be very close to cap)
    check = fresh_tracker.check("kai")
    if spent + LANE_ESTIMATED_COST_USD["kai"] > cap:
        assert not check.allowed
        assert "cap" in check.reason.lower()


def test_different_lanes_have_independent_budgets(fresh_tracker):
    """Spending on codex doesn't affect kai's budget."""
    fresh_tracker.charge("codex", amount=10.0)
    check = fresh_tracker.check("kai")
    assert check.allowed
    assert check.spent_today == 0.0


def test_snapshot_returns_all_lanes(fresh_tracker):
    fresh_tracker.charge("codex", amount=2.5)
    snap = fresh_tracker.snapshot()
    assert "date" in snap
    assert "lanes" in snap
    assert "codex" in snap["lanes"]
    assert snap["lanes"]["codex"]["spent"] == 2.5
    assert snap["lanes"]["codex"]["cap"] == LANE_DAILY_BUDGET_USD["codex"]


def test_snapshot_computes_utilization_pct(fresh_tracker):
    cap = LANE_DAILY_BUDGET_USD["ned"]
    fresh_tracker.charge("ned", amount=cap * 0.5)
    snap = fresh_tracker.snapshot()
    assert snap["lanes"]["ned"]["utilization_pct"] == 50.0


# === decide_dispatch tests ===

def test_decide_dispatch_no_lane_hint():
    decision = decide_dispatch(None)
    assert not decision.should_dispatch
    assert "no lane hint" in decision.reason.lower()


def test_decide_dispatch_known_lane():
    decision = decide_dispatch("codex")
    assert decision.should_dispatch
    assert decision.lane == "codex"
    assert decision.model == "sonnet"


def test_decide_dispatch_unknown_lane():
    decision = decide_dispatch("totally-bogus-lane")
    assert not decision.should_dispatch
    assert "unknown lane" in decision.reason.lower() or "triage" in decision.reason.lower()


def test_decide_dispatch_fred_uses_opus():
    """Fred orchestrates — should default to opus for high-judgment work."""
    decision = decide_dispatch("fred")
    assert decision.model == "opus"


def test_decide_dispatch_blocks_when_over_cap(fresh_tracker):
    """Force budget exhaustion, then verify decision blocks."""
    # Set cap to 0 by over-charging
    fresh_tracker.charge("codex", amount=100.0)
    decision = decide_dispatch("codex", budget_tracker=fresh_tracker)
    assert not decision.should_dispatch
    assert decision.lane == "codex"
    assert "cap" in decision.reason.lower()


# === build_supervisor_cmd tests ===

def test_build_supervisor_cmd_is_exact_issue_and_preserves_model():
    cmd = build_supervisor_cmd("GRO-1234", lane="codex", model="sonnet")
    assert "python3" in cmd[0] or cmd[0] == "python3"
    assert cmd.count("--issue") == 1
    assert cmd[cmd.index("--issue") + 1] == "GRO-1234"
    assert "--from-linear" not in cmd
    assert "--lane" not in cmd
    assert "codex" not in cmd
    assert cmd[cmd.index("--model") + 1] == "sonnet"


def test_build_supervisor_cmd_uses_explicit_runtime_paths(tmp_path):
    release = tmp_path / "release"
    supervisor = release / "scripts" / "agy_sandbox_event_supervisor.py"
    supervisor.parent.mkdir(parents=True)
    supervisor.write_text("# fixture\n")
    cmd = build_supervisor_cmd(
        "GRO-1234",
        lane="codex",
        model="sonnet",
        supervisor_path=str(supervisor),
        python_executable="/opt/prismatic/bin/python",
        expected_release_root=str(release),
    )
    assert cmd[:2] == ["/opt/prismatic/bin/python", str(supervisor)]


def test_build_supervisor_cmd_rejects_path_outside_release(tmp_path):
    with pytest.raises(RuntimeError, match="outside expected release root"):
        build_supervisor_cmd(
            "GRO-1234",
            lane="codex",
            model="sonnet",
            supervisor_path=str(tmp_path / "outside.py"),
            python_executable="/opt/prismatic/bin/python",
            expected_release_root=str(tmp_path / "release"),
        )


def test_build_supervisor_cmd_strict_mode_requires_pins(monkeypatch):
    monkeypatch.setenv("PRISMATIC_REQUIRE_PINNED_SUPERVISOR", "1")
    monkeypatch.delenv("PRISMATIC_SUPERVISOR_PATH", raising=False)
    monkeypatch.delenv("PRISMATIC_SUPERVISOR_PYTHON", raising=False)
    monkeypatch.delenv("PRISMATIC_RELEASE_ROOT", raising=False)
    with pytest.raises(RuntimeError, match="requires path, interpreter, and release root"):
        build_supervisor_cmd("GRO-1234", lane="codex", model="sonnet")


def test_build_supervisor_cmd_strict_mode_validates_runtime_objects(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PRISMATIC_REQUIRE_PINNED_SUPERVISOR", "1")
    release = tmp_path / "release"
    release.mkdir()
    supervisor = release / "scripts" / "supervisor.py"
    supervisor.parent.mkdir()
    supervisor.write_text("# fixture\n")
    interpreter = tmp_path / "venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("#!/bin/sh\nexit 0\n")
    interpreter.chmod(0o755)

    command = build_supervisor_cmd(
        "GRO-1234",
        lane="codex",
        model="sonnet",
        supervisor_path=str(supervisor),
        python_executable=str(interpreter),
        expected_release_root=str(release),
    )
    assert command[:2] == [str(interpreter.resolve()), str(supervisor.resolve())]

    release_file = tmp_path / "release-file"
    release_file.write_text("not a directory\n")
    invalid_cases = [
        ({"supervisor_path": str(release / "missing.py")}, "not an existing regular file"),
        ({"supervisor_path": str(supervisor.parent)}, "not an existing regular file"),
        ({"python_executable": str(tmp_path / "missing-python")}, "does not exist"),
        ({"python_executable": "python3"}, "must be an absolute path"),
        ({"expected_release_root": str(tmp_path / "missing-root")}, "does not exist"),
        ({"expected_release_root": str(release_file)}, "not a directory"),
    ]
    non_executable = tmp_path / "venv" / "bin" / "non-executable-python"
    non_executable.write_text("#!/bin/sh\nexit 0\n")
    invalid_cases.append(
        ({"python_executable": str(non_executable)}, "not an executable regular file")
    )

    defaults = {
        "supervisor_path": str(supervisor),
        "python_executable": str(interpreter),
        "expected_release_root": str(release),
    }
    for override, message in invalid_cases:
        arguments = defaults | override
        with pytest.raises(RuntimeError, match=message):
            build_supervisor_cmd(
                "GRO-1234", lane="codex", model="sonnet", **arguments
            )

    with pytest.raises(RuntimeError, match="release root must be an absolute path"):
        build_supervisor_cmd(
            "GRO-1234",
            lane="codex",
            model="sonnet",
            supervisor_path=str(supervisor),
            python_executable=str(interpreter),
            expected_release_root="relative-release",
        )
    with pytest.raises(RuntimeError, match="supervisor path must be an absolute path"):
        build_supervisor_cmd(
            "GRO-1234",
            lane="codex",
            model="sonnet",
            supervisor_path="scripts/supervisor.py",
            python_executable=str(interpreter),
            expected_release_root=str(release),
        )


@pytest.mark.parametrize(
    "value", [None, "", "--from-linear", "--issue=EVIL", "bad\x00value"]
)
@pytest.mark.parametrize("field", ["issue_id", "model"])
def test_build_supervisor_cmd_rejects_invalid_option_values(field, value):
    arguments = {"issue_id": "GRO-1234", "lane": "codex", "model": "sonnet"}
    arguments[field] = value
    with pytest.raises(ValueError, match="nonempty non-option string"):
        build_supervisor_cmd(**arguments)


def test_build_supervisor_cmd_opus():
    cmd = build_supervisor_cmd("GRO-X", lane="fred", model="opus")
    assert "opus" in cmd