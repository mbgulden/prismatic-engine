"""Phase 4 tests: the autonomy digest and its overnight-report plug point."""

from __future__ import annotations

import sys
import types

from prismatic.review_factory.digest import (
    MAX_EXCEPTIONS,
    build_autonomy_section,
    collect_autonomy_inputs,
    summarize_progress,
)


def _tier_status(**over):
    status = {
        "current_tier": 1,
        "consecutive_clean_by_class": {"docs": 14, "chore": 14, "dep_bump": 14},
        "clean_target_by_class": {"docs": 20, "chore": 20, "dep_bump": 20},
        "rollback_count_30d": 0,
        "pause_precision": 0.83,
        "pauses_trailing_30": 12,
        "pauses_agreed_trailing_30": 10,
    }
    status.update(over)
    return status


def test_summarize_progress_realistic_shape():
    lines = summarize_progress(_tier_status())
    assert "T1: 14/20 clean docs/chore/dep_bump" in lines
    assert "rollbacks (30d): 0" in lines
    assert "pause precision: 0.83 (12 pauses)" in lines
    assert not any("frozen" in line for line in lines)


def test_summarize_progress_none_means_ledger_unavailable():
    assert summarize_progress(None) == ["ledger unavailable"]


def test_summarize_progress_frozen_includes_freeze_line():
    lines = summarize_progress(
        _tier_status(promotion_freeze_until="2099-01-01T00:00:00Z")
    )
    assert "promotions frozen until 2099-01-01T00:00:00Z" in lines


def test_summarize_progress_never_raises_on_garbage():
    assert summarize_progress("not-a-dict") == ["ledger unavailable"]
    assert summarize_progress({}) == ["ledger unavailable"]
    lines = summarize_progress({"current_tier": "weird", "pause_precision": "high"})
    assert isinstance(lines, list) and lines


def test_summarize_progress_mixed_class_counts_get_per_class_lines():
    lines = summarize_progress(
        _tier_status(
            consecutive_clean_by_class={"docs": 14, "chore": 3},
            clean_target_by_class={"docs": 20, "chore": 30},
        )
    )
    assert "T1: 14/20 clean docs" in lines
    assert "T1: 3/30 clean chore" in lines


def test_build_autonomy_section_full_inputs():
    revocations = [
        {
            "ts": f"2026-09-{d:02d}T00:00:00Z",
            "from_tier": 2,
            "to_tier": 1,
            "reason": "rollback spike",
        }
        for d in range(1, 4)
    ]
    section = build_autonomy_section(
        tier_status=_tier_status(),
        revocations=revocations,
        brake_engaged=True,
        janitor={"removed": 5, "archived": 2, "skipped": 1, "source": "janitor"},
        auto_merges={"tier1": 7, "tier2": 2},
    )
    assert section["tier"] == 1
    assert isinstance(section["progress"], list) and section["progress"]
    assert section["auto_merges_by_tier"] == {"tier1": 7, "tier2": 2}
    assert section["jev_pauses"] == {"total": 12, "agreed": 10, "precision": 0.83}
    assert len(section["revocations"]) == 3
    assert section["revocations"][0]["ts"] == "2026-09-01T00:00:00Z"
    assert section["revocations_truncated_away"] == 0
    assert section["brake"] == {"engaged": True}
    assert section["janitor"]["removed"] == 5
    assert section["frozen"] is False


def test_build_autonomy_section_truncates_revocations():
    revocations = [
        {
            "ts": f"2026-09-{i:02d}T00:00:00Z",
            "from_tier": 2,
            "to_tier": 1,
            "reason": f"r{i}",
        }
        for i in range(1, 26)
    ]
    section = build_autonomy_section(
        tier_status=_tier_status(), revocations=revocations
    )
    assert len(section["revocations"]) == MAX_EXCEPTIONS == 10
    assert section["revocations_truncated_away"] == 15
    # Bounded shape: only the expected keys, no raw extras.
    assert set(section["revocations"][0]) == {"ts", "from_tier", "to_tier", "reason"}


def test_build_autonomy_section_all_none_never_raises():
    section = build_autonomy_section()
    assert section["tier"] is None
    assert section["progress"] == ["ledger unavailable"]
    assert section["auto_merges_by_tier"] == {}
    assert section["jev_pauses"] == {"total": None, "agreed": None, "precision": None}
    assert section["revocations"] == []
    assert section["revocations_truncated_away"] == 0
    assert section["brake"] == {"engaged": False}
    assert section["janitor"]["source"] == "janitor phase pending"
    assert section["frozen"] is False


def test_collect_autonomy_inputs_trust_absent(monkeypatch):
    monkeypatch.delitem(sys.modules, "prismatic.review_factory.trust", raising=False)
    result = collect_autonomy_inputs()
    assert result == {"unavailable": True, "reason": "trust_module_absent"}


def _stub_trust_module(tier_status, events, ledger_factory=None):
    module = types.ModuleType("prismatic.review_factory.trust")

    class TrustLedger:
        def __init__(self):
            if ledger_factory is not None:
                ledger_factory()

        def tier_status(self):
            return tier_status

        def events(self):
            return events

    module.TrustLedger = TrustLedger
    return module


def test_collect_autonomy_inputs_with_stubbed_trust(monkeypatch):
    tier_status = _tier_status()
    events = [
        {"event_type": "tier_promoted", "to_tier": 2},
        {
            "event_type": "tier_revoked",
            "ts": "t1",
            "from_tier": 2,
            "to_tier": 1,
            "reason": "x",
        },
        "not-a-dict",
        {"event_type": "tier_revoked", "ts": "t2", "from_tier": 1, "to_tier": 0},
    ]
    monkeypatch.setitem(
        sys.modules,
        "prismatic.review_factory.trust",
        _stub_trust_module(tier_status, events),
    )
    monkeypatch.setenv("PRISMATIC_AUTONOMY_ENABLED", "0")
    result = collect_autonomy_inputs()
    assert result["tier_status"] is tier_status
    assert len(result["revocations"]) == 2
    assert all(e["event_type"] == "tier_revoked" for e in result["revocations"])
    assert result["brake_engaged"] is True

    monkeypatch.setenv("PRISMATIC_AUTONOMY_ENABLED", "False")
    assert collect_autonomy_inputs()["brake_engaged"] is True
    monkeypatch.setenv("PRISMATIC_AUTONOMY_ENABLED", "yes")
    assert collect_autonomy_inputs()["brake_engaged"] is False
    monkeypatch.delenv("PRISMATIC_AUTONOMY_ENABLED")
    assert collect_autonomy_inputs()["brake_engaged"] is False


def test_collect_autonomy_inputs_explicit_ledger_skips_construction(monkeypatch):
    # An explicitly provided ledger is used directly; the absent trust module
    # only matters for the lazy import path.
    monkeypatch.setitem(
        sys.modules,
        "prismatic.review_factory.trust",
        _stub_trust_module(_tier_status(), []),
    )
    ledger = sys.modules["prismatic.review_factory.trust"].TrustLedger()
    result = collect_autonomy_inputs(ledger=ledger)
    assert result["tier_status"]["current_tier"] == 1
    assert result["revocations"] == []


def test_collect_autonomy_inputs_ledger_constructor_failure(monkeypatch):
    def boom():
        raise RuntimeError("no db")

    monkeypatch.setitem(
        sys.modules, "prismatic.review_factory.trust", _stub_trust_module({}, [], boom)
    )
    result = collect_autonomy_inputs()
    assert result["unavailable"] is True
    assert "ledger_unavailable" in result["reason"]


def test_overnight_report_defaults_autonomy_to_empty():
    from prismatic.reports.overnight import OvernightReport

    report = OvernightReport(
        generated_at="2026-09-24T00:00:00Z",
        factory_duration="12h 0m",
        tasks_processed=0,
        tasks_autonomous=0,
        tasks_need_human=0,
        cost_dollars=0.0,
        tokens_saved=0,
    )
    assert report.autonomy == {}
    assert report.to_dict()["autonomy"] == {}


def test_load_autonomy_uses_digest_and_builds_section(monkeypatch):
    import prismatic.reports.overnight as overnight_mod

    canned = {"tier": 1, "progress": ["T1: 1/20 clean docs"], "frozen": False}
    seen = {}

    def fake_collect():
        seen["collect_called"] = True
        return {
            "tier_status": {"current_tier": 1},
            "revocations": [],
            "brake_engaged": False,
        }

    def fake_build(**kwargs):
        seen["build_kwargs"] = kwargs
        return canned

    monkeypatch.setattr(overnight_mod, "collect_autonomy_inputs", fake_collect)
    monkeypatch.setattr(overnight_mod, "build_autonomy_section", fake_build)
    assert overnight_mod._load_autonomy() == canned
    assert seen["collect_called"] is True
    assert seen["build_kwargs"]["tier_status"] == {"current_tier": 1}
    assert seen["build_kwargs"]["revocations"] == []
    assert seen["build_kwargs"]["brake_engaged"] is False


def test_load_autonomy_when_collect_raises(monkeypatch):
    import prismatic.reports.overnight as overnight_mod

    def fake_collect():
        raise RuntimeError("ledger on fire")

    monkeypatch.setattr(overnight_mod, "collect_autonomy_inputs", fake_collect)
    result = overnight_mod._load_autonomy()
    assert result["status"] == "unavailable"
    assert "ledger on fire" in result["reason"]


def test_contract_waivers_default_empty():
    section = build_autonomy_section(tier_status=_tier_status())
    assert section["contract_waivers"] == []
    assert section["contract_waivers_truncated_away"] == 0


def test_contract_waivers_listed_bounded():
    waivers = [
        {"pr": f"#{100 + i}", "title": f"waiver {i}", "ts": "2026-09-25T00:00:00Z"}
        for i in range(3)
    ]
    section = build_autonomy_section(
        tier_status=_tier_status(), contract_waivers=waivers
    )
    assert len(section["contract_waivers"]) == 3
    assert section["contract_waivers"][0]["pr"] == "#100"
    assert section["contract_waivers_truncated_away"] == 0


def test_contract_waivers_truncated_with_count():
    waivers = [{"pr": f"#{i}", "title": "w"} for i in range(MAX_EXCEPTIONS + 4)]
    section = build_autonomy_section(
        tier_status=_tier_status(), contract_waivers=waivers
    )
    assert len(section["contract_waivers"]) == MAX_EXCEPTIONS
    assert section["contract_waivers_truncated_away"] == 4


def test_contract_waivers_garbage_never_raises():
    section = build_autonomy_section(
        tier_status=_tier_status(), contract_waivers="not-a-list"
    )
    assert section["contract_waivers"] == []
    section = build_autonomy_section(
        tier_status=_tier_status(),
        contract_waivers=[{"pr": "#1"}, "junk", None],
    )
    assert len(section["contract_waivers"]) == 1
