from prismatic.budget_caps import (
    DEFAULT_BUDGET_CAPS,
    budget_caps_configured,
    evaluate_budget_caps,
    normalize_budget_caps,
    read_budget_caps,
    write_budget_caps,
)


def test_read_budget_caps_returns_defaults_when_missing(tmp_path):
    missing = tmp_path / "missing.json"
    assert read_budget_caps(missing) == DEFAULT_BUDGET_CAPS
    assert budget_caps_configured(missing) is False


def test_write_budget_caps_normalizes_and_persists(tmp_path):
    caps_path = tmp_path / "budget_caps.json"

    stored = write_budget_caps(
        {
            "daily_limit": "12.5",
            "per_model": {" gemini-3.5-flash-high ": "3", "": 99},
            "auto_pause": False,
        },
        caps_path,
    )

    assert stored == {
        "daily_limit": 12.5,
        "per_model": {"gemini-3.5-flash-high": 3.0},
        "auto_pause": False,
    }
    assert read_budget_caps(caps_path) == stored
    assert budget_caps_configured(caps_path) is True


def test_budget_guard_blocks_when_auto_pause_cap_is_reached():
    decision = evaluate_budget_caps(
        15.0,
        {"daily_limit": 15.0, "per_model": {}, "auto_pause": True},
    )

    assert decision.allowed is False
    assert "Daily budget cap reached" in decision.reason


def test_budget_guard_allows_when_auto_pause_disabled():
    decision = evaluate_budget_caps(
        100.0,
        {"daily_limit": 15.0, "per_model": {}, "auto_pause": False},
    )

    assert decision.allowed is True


def test_normalize_budget_caps_rejects_negative_limits():
    assert normalize_budget_caps({"daily_limit": -10, "per_model": {"x": -2}}) == {
        "daily_limit": 0.0,
        "per_model": {"x": 0.0},
        "auto_pause": True,
    }
