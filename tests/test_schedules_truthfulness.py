"""Truthfulness tests for the Schedule Observatory providers.

Regression coverage for the fabricated-timestamp fix: no schedule may ever
report a last_run that was not observed from a real source. Mock/fallback
providers must report last_run=None ("Never" in the UI); the systemd
provider must read live systemctl state.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from prismatic import schedules as sched_mod
from prismatic.schedules import (
    _parse_systemd_usec,
    _systemd_timer_state,
    get_agy_schedules,
    get_jules_schedules,
    get_systemd_timer_schedules,
)


def test_parse_systemd_usec_valid() -> None:
    iso = _parse_systemd_usec("Mon 2026-09-28 22:35:23 UTC")
    assert iso is not None
    dt = datetime.fromisoformat(iso)
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second) == (
        2026, 9, 28, 22, 35, 23,
    )
    assert dt.tzinfo is not None


@pytest.mark.parametrize("value", ["n/a", "N/A", "", "   ", "not a timestamp"])
def test_parse_systemd_usec_honest_on_garbage(value: str) -> None:
    assert _parse_systemd_usec(value) is None


def _fake_run_systemctl(mapping: dict):
    """Build a _run_systemctl stand-in dispatching on argv."""

    def _fake(args):
        for prefix, stdout in mapping.items():
            if list(args)[: len(prefix)] == list(prefix):
                return stdout
        return None

    return _fake


SHOW_ACTIVE = (
    "LastTriggerUSec=Mon 2026-09-28 22:35:23 UTC\nActiveState=active\n"
)
LIST_TIMERS = (
    "Mon 2026-09-28 22:40:00 UTC  4min 37s left "
    "Mon 2026-09-28 22:35:23 UTC 4min ago "
    "prismatic-watchdog.timer prismatic-watchdog.service\n"
)
UNIT_CAT = (
    "# /etc/systemd/system/prismatic-watchdog.timer\n"
    "[Unit]\nDescription=x\n\n"
    "[Timer]\nOnBootSec=120\nOnUnitActiveSec=30\nAccuracySec=1\n"
)


def test_systemd_timer_parses_live_state(monkeypatch) -> None:
    monkeypatch.setattr(
        sched_mod,
        "_run_systemctl",
        _fake_run_systemctl(
            {
                ("show",): SHOW_ACTIVE,
                ("list-timers",): LIST_TIMERS,
                ("cat",): UNIT_CAT,
            }
        ),
    )
    (record,) = get_systemd_timer_schedules()
    assert record.id == "prismatic:systemd:prismatic-watchdog"
    assert record.enabled is True
    assert record.last_run is not None
    assert record.last_run.fired_at.startswith("2026-09-28T22:35:23")
    assert record.last_run.status == "success"
    assert record.next_run_at is not None
    assert record.next_run_at.startswith("2026-09-28T22:40:00")
    # Honest schedule expression from the unit file, not a hardcoded guess
    assert "OnUnitActiveSec=30" in record.schedule_expr
    assert "OnBootSec=120" in record.schedule_expr
    assert "0/5" not in record.schedule_expr


def test_systemd_timer_never_triggered_is_honest(monkeypatch) -> None:
    monkeypatch.setattr(
        sched_mod,
        "_run_systemctl",
        _fake_run_systemctl(
            {
                ("show",): "LastTriggerUSec=n/a\nActiveState=active\n",
                ("list-timers",): LIST_TIMERS,
                ("cat",): UNIT_CAT,
            }
        ),
    )
    (record,) = get_systemd_timer_schedules()
    assert record.last_run is None
    assert record.next_run_at is not None


def test_systemd_timer_missing_unit_is_honest(monkeypatch) -> None:
    monkeypatch.setattr(
        sched_mod, "_run_systemctl", lambda args: None
    )
    state = _systemd_timer_state("no-such-unit.timer")
    assert state == {
        "last_trigger": None,
        "next_elapse": None,
        "active": None,
        "schedule_expr": None,
    }
    (record,) = get_systemd_timer_schedules()
    assert record.last_run is None
    assert record.next_run_at is None
    assert record.enabled is False


def test_systemd_timer_inactive_unit_not_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        sched_mod,
        "_run_systemctl",
        _fake_run_systemctl(
            {
                ("show",): (
                    "LastTriggerUSec=Mon 2026-09-28 22:35:23 UTC\n"
                    "ActiveState=inactive\n"
                ),
                ("list-timers",): "",
                ("cat",): UNIT_CAT,
            }
        ),
    )
    (record,) = get_systemd_timer_schedules()
    assert record.enabled is False
    assert record.last_run is not None  # real historical data is fine
    assert record.next_run_at is None  # no NEXT row when timer is off


def test_agy_fallback_never_fabricates_last_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGY_SCHEDULES_DIR", str(tmp_path / "does-not-exist"))
    records = get_agy_schedules()
    assert records, "expected fallback mock records"
    for record in records:
        assert record.metadata.get("adapter") == "fallback-mock"
        assert record.last_run is None, (
            f"fallback mock {record.id} must not fabricate a last_run"
        )


def test_jules_fallback_never_fabricates_last_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JULES_SCHEDULES_FILE", str(tmp_path / "nope.json"))
    monkeypatch.delenv("JULES_API_KEY", raising=False)
    records = get_jules_schedules()
    assert records, "expected fallback mock records"
    for record in records:
        assert record.metadata.get("adapter") == "fallback-mock"
        assert record.last_run is None, (
            f"fallback mock {record.id} must not fabricate a last_run"
        )


def test_no_provider_reports_future_last_run(tmp_path, monkeypatch) -> None:
    """Belt-and-suspenders: no provider may claim a run in the future."""
    monkeypatch.setenv("AGY_SCHEDULES_DIR", str(tmp_path / "does-not-exist"))
    monkeypatch.setenv("JULES_SCHEDULES_FILE", str(tmp_path / "nope.json"))
    monkeypatch.delenv("JULES_API_KEY", raising=False)
    now = datetime.now(timezone.utc)
    for provider in (get_agy_schedules, get_jules_schedules):
        for record in provider():
            if record.last_run is None:
                continue
            fired = datetime.fromisoformat(record.last_run.fired_at)
            assert fired <= now, f"{record.id} claims a future run"
