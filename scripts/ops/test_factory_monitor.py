"""Tests for factory_monitor.py — R-3 repair.

Fail-first coverage for:
- failed-unit triage only counts `.service` units: a failed transient
  scope (e.g. ``session-....scope`` from a dead user login session) must
  not page as CRITICAL on its own — on the live box this exact line was
  one of the "3 failed systemd units" behind the exit-2;
- the exit-code contract: CRITICAL -> 2, WARN/OK -> 0 (systemd labels
  exit 2 "INVALIDARGUMENT"; that is its generic name for the code, not
  an argument-parsing error in this script).
"""
from __future__ import annotations

import factory_monitor as fm


SCOPE_LINE = "● session-c5874.scope               loaded failed failed Session c5874 of User ubuntu"
SVC_LINE = "● prismatic-review-factory.service  loaded failed failed Prismatic Review/Merge Factory bounded one-shot drain"
OWN_LINE = "● factory-monitor.service           loaded failed failed Prismatic Factory Monitor — heartbeat health check"


def _ok_checks(**overrides):
    checks = {
        "services": {},
        "failed_units": {"count": 0, "units": []},
        "zombies": {"count": 0},
        "endpoints": {},
        "bus": {"exists": True, "pending": 0, "last_event_age_sec": 0},
        "curator": {"ok": True, "escalations_recent": 0},
        "log_errors": {},
    }
    checks.update(overrides)
    return checks


def test_failed_unit_name_parsing():
    assert fm._failed_unit_name(SCOPE_LINE) == "session-c5874.scope"
    assert fm._failed_unit_name(SVC_LINE) == "prismatic-review-factory.service"
    assert fm._failed_unit_name("factory-monitor.service loaded failed failed x") == "factory-monitor.service"
    assert fm._failed_unit_name("") == ""
    assert fm._failed_unit_name("   ") == ""


def test_check_failed_units_ignores_scopes(monkeypatch):
    monkeypatch.setattr(
        fm, "shell",
        lambda cmd, timeout=10: (0, f"{SCOPE_LINE}\n{SVC_LINE}\n{OWN_LINE}", ""),
    )
    result = fm.check_failed_units()
    # scope excluded; both .service units kept (self-exclusion of
    # factory-monitor happens in assess(), not here)
    assert result["count"] == 2
    assert result["units"] == [SVC_LINE, OWN_LINE]


def test_scope_only_failure_pipeline_exits_zero(monkeypatch):
    """End-to-end of the exit-2 path: a lone failed scope must not CRITICAL.

    Red on the pre-fix script (scope counted -> CRITICAL -> exit 2);
    green after the fix (scope ignored -> OK -> exit 0).
    """
    monkeypatch.setattr(fm, "shell", lambda cmd, timeout=10: (0, SCOPE_LINE, ""))
    checks = _ok_checks(failed_units=fm.check_failed_units())
    severity, alerts = fm.assess(checks)
    assert severity == "OK"
    assert alerts == []
    assert fm.severity_exit_code(severity) == 0


def test_failed_service_is_still_critical(monkeypatch):
    """The fix must not neuter the check: a real failed service pages."""
    monkeypatch.setattr(fm, "shell", lambda cmd, timeout=10: (0, SVC_LINE, ""))
    checks = _ok_checks(failed_units=fm.check_failed_units())
    severity, alerts = fm.assess(checks)
    assert severity == "CRITICAL"
    assert any("prismatic-review-factory" in a for a in alerts)
    assert fm.severity_exit_code(severity) == 2


def test_own_service_excluded_from_critical():
    """The monitor's own failed entry is excluded at assess() time."""
    checks = _ok_checks(failed_units={"count": 1, "units": [OWN_LINE]})
    severity, alerts = fm.assess(checks)
    assert severity == "OK"
    assert alerts == []


def test_exit_code_contract():
    assert fm.severity_exit_code("CRITICAL") == 2
    assert fm.severity_exit_code("WARN") == 0
    assert fm.severity_exit_code("OK") == 0


def test_inactive_service_is_critical():
    """A service that is not active (e.g. crash-looping in auto-restart)
    must stay CRITICAL — the strict check is load-bearing."""
    checks = _ok_checks(services={"prismatic-consumer": {"active": False, "recent_restart": False}})
    severity, alerts = fm.assess(checks)
    assert severity == "CRITICAL"
    assert any("prismatic-consumer" in a and "not active" in a for a in alerts)
