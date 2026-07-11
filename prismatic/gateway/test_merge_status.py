from prismatic.gateway.server import (
    _linear_state_is_terminal,
    _prune_terminal_linear_pending,
)


def test_linear_state_terminal_detection_by_name_and_type():
    assert _linear_state_is_terminal({"name": "Done", "type": "completed"})
    assert _linear_state_is_terminal({"name": "Duplicate", "type": "canceled"})
    assert _linear_state_is_terminal({"name": "Canceled", "type": "canceled"})
    assert not _linear_state_is_terminal({"name": "In Review", "type": "started"})
    assert not _linear_state_is_terminal(None)


def test_prune_terminal_pending_removes_terminal_unless_force_kept():
    pending = {
        "GRO-1": {"tier": 2},
        "GRO-2": {"tier": 3},
        "GRO-3": {
            "tier": 4,
            "force_keep_terminal": True,
            "terminal_keep_rationale": "manual merge still required",
        },
        "GRO-4": {"tier": 1},
    }
    linear_states = {
        "GRO-1": {"name": "Done", "type": "completed"},
        "GRO-2": {"name": "Todo", "type": "unstarted"},
        "GRO-3": {"name": "Duplicate", "type": "canceled"},
        "GRO-4": {"name": "Canceled", "type": "canceled"},
    }

    active, pruned, retained = _prune_terminal_linear_pending(pending, linear_states)

    assert set(active) == {"GRO-2", "GRO-3"}
    assert {row["ticket"] for row in pruned} == {"GRO-1", "GRO-4"}
    assert retained == [
        {
            "ticket": "GRO-3",
            "linear_state": {"name": "Duplicate", "type": "canceled"},
            "rationale": "manual merge still required",
        }
    ]
