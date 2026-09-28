"""tests/test_cron_health_seeds.py — WI-5: engine-health seed set for new users.

- Fresh stores seed BOTH the SEO group and the engine-health group.
- Existing stores merge new seeds while keeping runtime fields
  (state, last_run_at/last_status/..., timestamps) on crons the user owns.
- `prismatic crons emit` includes the report-only janitor-gc core cron.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from prismatic.core_crons import emit as emit_core_crons
from prismatic.native_crons import (
    ENGINE_HEALTH_CRONS,
    SEO_NATIVE_CRONS,
    NativeCron,
    NativeCronStore,
    repo_root,
    validate_cron_schedule,
)

ENGINE_HEALTH_IDS = [c.id for c in ENGINE_HEALTH_CRONS]
SEO_IDS = [c.id for c in SEO_NATIVE_CRONS]


def _fresh_store(tmp_path: Path) -> NativeCronStore:
    store_file = tmp_path / "native_crons.json"
    assert not store_file.exists()
    return NativeCronStore(path=store_file)


def test_fresh_store_seeds_both_groups(tmp_path: Path) -> None:
    store = _fresh_store(tmp_path)
    crons = store.load()
    by_id = {c.id: c for c in crons}
    assert len(crons) == len(SEO_IDS) + len(ENGINE_HEALTH_IDS)
    for cron_id in SEO_IDS:
        assert cron_id in by_id
        assert by_id[cron_id].group == "seo"
    for cron_id in ENGINE_HEALTH_IDS:
        assert cron_id in by_id
        assert by_id[cron_id].group == "engine-health"
    assert {c.group for c in crons} == {"seo", "engine-health"}


def test_existing_store_keeps_runtime_fields_on_merge(tmp_path: Path) -> None:
    store_file = tmp_path / "native_crons.json"
    owned = NativeCron(
        id="seo.aot-weekly-rankings",
        name="SEO — Managed site weekly rankings change report",
        schedule="0 4 * * 1",
        command=["python3", "scripts/seo/aot_kpi_tracker.py"],
        group="seo",
    )
    owned.state = "paused"
    owned.paused_at = "2026-09-01T00:00:00+00:00"
    owned.last_run_at = "2026-09-26T04:00:00+00:00"
    owned.last_status = "success"
    owned.last_exit_code = 0
    owned.last_stdout = "ran fine"
    owned.updated_at = "2026-09-26T05:00:00+00:00"
    custom = NativeCron(
        id="custom.user-cron",
        name="User cron",
        schedule="0 9 * * *",
        command=["echo", "hi"],
        group="custom",
    )
    store_file.write_text(
        json.dumps(
            {"version": 1, "crons": [owned.to_dict(), custom.to_dict()]},
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    store = NativeCronStore(path=store_file)
    crons = store.load()
    by_id = {c.id: c for c in crons}
    # New engine-health seeds merged in…
    for cron_id in ENGINE_HEALTH_IDS:
        assert cron_id in by_id
    # …existing SEO cron keeps its runtime fields…
    kept = by_id["seo.aot-weekly-rankings"]
    assert kept.state == "paused"
    assert kept.paused_at == "2026-09-01T00:00:00+00:00"
    assert kept.last_run_at == "2026-09-26T04:00:00+00:00"
    assert kept.last_status == "success"
    assert kept.last_exit_code == 0
    assert kept.last_stdout == "ran fine"
    assert kept.updated_at == "2026-09-26T05:00:00+00:00"
    # …and user-defined crons are never dropped.
    assert by_id["custom.user-cron"].state == "active"


def test_ensure_seeded_is_idempotent(tmp_path: Path) -> None:
    store = _fresh_store(tmp_path)
    store.load()
    first = store.path.read_text(encoding="utf-8")
    store.load()
    second = store.path.read_text(encoding="utf-8")
    assert first == second


def test_seed_schedules_and_ids_are_valid() -> None:
    for cron in SEO_NATIVE_CRONS + ENGINE_HEALTH_CRONS:
        validate_cron_schedule(cron.schedule)
        assert (cron.name or "").strip()
        assert isinstance(cron.command, list) and cron.command
        assert all(isinstance(part, str) and part for part in cron.command)
    ids = [c.id for c in SEO_NATIVE_CRONS + ENGINE_HEALTH_CRONS]
    assert len(ids) == len(set(ids)), "seed cron ids must be unique"


def test_health_seed_entry_points_exist() -> None:
    """Every engine-health seed must reference a real, existing entry point."""
    from prismatic import cli as prismatic_cli  # noqa: PLC0415

    root = repo_root()
    for cron in ENGINE_HEALTH_CRONS:
        argv = cron.command
        if argv[0] == "python3" and len(argv) > 1 and argv[1].endswith(".py"):
            script = root / argv[1]
            assert script.is_file(), f"{cron.id} references missing script {argv[1]}"
        elif argv[0] == "prismatic" and argv[1:] == ["doctor"]:
            parser = prismatic_cli._build_parser()
            subparsers = next(
                a
                for a in parser._actions
                if isinstance(a, argparse._SubParsersAction)
            )
            assert "doctor" in subparsers.choices, "prismatic doctor must exist"
        else:
            raise AssertionError(f"{cron.id} has an unrecognized command shape: {argv}")


def test_emit_includes_report_only_janitor_gc() -> None:
    payload = json.loads(emit_core_crons(fmt="json"))
    by_id = {item["id"]: item for item in payload}
    gc = by_id["prismatic.worktree-janitor-gc.weekly"]
    assert gc["command"].startswith("prismatic worktrees janitor-gc")
    assert "--mode report-only" in gc["command"]
    # Weekly schedule (5-field cron, day-of-week pinned).
    validate_cron_schedule(gc["schedule"])
    assert gc["schedule"].split()[-1] != "*"
    lines = emit_core_crons(fmt="crontab").splitlines()
    assert any("prismatic.worktree-janitor-gc.weekly" in line for line in lines)
    assert any("prismatic worktrees janitor-gc" in line for line in lines)
    # The original hourly janitor is still there.
    assert any("prismatic.worktree-janitor.hourly" in line for line in lines)


def test_health_seeds_export_as_scheduled_lines(tmp_path: Path, monkeypatch) -> None:
    """All four engine-health seeds are active + scheduled, so the crontab export carries them."""
    from prismatic import native_crons as nc  # noqa: PLC0415

    monkeypatch.setattr(
        nc, "default_cron_store_path", lambda: tmp_path / "native_crons.json"
    )
    lines = nc.export_system_crontab_lines()
    for cron in ENGINE_HEALTH_CRONS:
        if cron.schedule == "manual":
            continue
        assert any(cron.id in line for line in lines), (
            f"engine-health seed {cron.id} missing from crontab export"
        )
