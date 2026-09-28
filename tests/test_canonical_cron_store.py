"""Section C: one canonical cron store, shared by recorder/gateway/dashboard.

Regression: the store used to default to ``repo_root()/prismatic_state``, so
every code copy (venv site-packages, each release dir) had its own store.
The gateway read a freshly-seeded empty one and the dashboard crons tab
permanently showed zero run history.
"""
import json
from pathlib import Path

import pytest

from prismatic.native_crons import (
    NativeCronStore,
    canonical_cron_store_path,
    default_cron_store_path,
    list_native_crons,
)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    for var in ("PRISMATIC_NATIVE_CRON_STORE", "PRISMATIC_STATE_DIR"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def _write_store(path: Path, crons: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "crons": crons}), encoding="utf-8")


def _legacy_entry(cron_id, ts, status="success", exit_code=0):
    return {
        "id": cron_id,
        "last_run_at": ts,
        "last_status": status,
        "last_exit_code": exit_code,
        "last_stdout": "ok",
        "last_stderr": "",
        "last_duration_s": 1.5,
    }


def test_default_store_is_canonical_not_repo_relative(fake_home):
    path = default_cron_store_path()
    assert path == fake_home / ".prismatic" / "db" / "native_crons.json"
    assert path == canonical_cron_store_path()
    assert "site-packages" not in str(path)


def test_store_path_is_code_location_independent(fake_home, tmp_path, monkeypatch):
    """The gateway (venv site-packages) and the recorder (release dir) resolve
    the same store no matter the working directory."""
    monkeypatch.chdir(tmp_path)
    first = NativeCronStore().path
    monkeypatch.chdir(fake_home)
    assert NativeCronStore().path == first == canonical_cron_store_path()


def test_env_overrides_still_honored(fake_home, tmp_path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_NATIVE_CRON_STORE", str(tmp_path / "custom.json"))
    assert default_cron_store_path() == tmp_path / "custom.json"
    monkeypatch.delenv("PRISMATIC_NATIVE_CRON_STORE")
    monkeypatch.setenv("PRISMATIC_STATE_DIR", str(tmp_path / "state"))
    assert default_cron_store_path() == tmp_path / "state" / "native_crons.json"


def test_migration_imports_newest_history_per_cron(fake_home):
    legacy_a = fake_home / ".prismatic/venv_stable/lib/python3.12/site-packages/prismatic_state/native_crons.json"
    legacy_b = fake_home / ".prismatic/versions/prismatic-engine-abc123/prismatic_state/native_crons.json"
    _write_store(legacy_a, [
        _legacy_entry("engine.doctor", "2026-09-20T10:00:00+00:00"),
        _legacy_entry("engine.lane-visibility-probe", "2026-09-21T10:00:00+00:00"),
    ])
    _write_store(legacy_b, [
        _legacy_entry("engine.doctor", "2026-09-27T10:00:00+00:00", status="failed", exit_code=1),
    ])

    store = NativeCronStore()
    store.ensure_seeded()  # seeds canonical store, imports legacy history
    by_id = {c.id: c for c in store.load()}

    doctor = by_id["engine.doctor"]
    assert doctor.last_run_at == "2026-09-27T10:00:00+00:00"  # newest wins
    assert doctor.last_status == "failed"
    assert doctor.last_exit_code == 1
    probe = by_id["engine.lane-visibility-probe"]
    assert probe.last_run_at == "2026-09-21T10:00:00+00:00"

    # list_native_crons (the gateway/dashboard path) shows the same history.
    listed = {c["id"]: c for c in list_native_crons()}
    assert listed["engine.doctor"]["last_run_at"] == "2026-09-27T10:00:00+00:00"


def test_migration_runs_on_existing_historyless_store(fake_home):
    """The canonical store may already exist (seeded) with zero history —
    migration must still backfill it."""
    store = NativeCronStore()
    store.ensure_seeded()
    assert not any(c.last_run_at for c in store.load())

    legacy = fake_home / ".prismatic/venv_old/lib/python3.12/site-packages/prismatic_state/native_crons.json"
    _write_store(legacy, [_legacy_entry("engine.doctor", "2026-09-25T10:00:00+00:00")])

    reloaded = NativeCronStore().load()
    doctor = next(c for c in reloaded if c.id == "engine.doctor")
    assert doctor.last_run_at == "2026-09-25T10:00:00+00:00"


def test_migration_skipped_when_canonical_already_has_history(fake_home):
    store = NativeCronStore()
    store.ensure_seeded()
    from prismatic.native_crons import record_cron_run
    assert record_cron_run("engine.doctor", "true", store=store) == 0

    legacy = fake_home / ".prismatic/venv_stable/lib/python3.12/site-packages/prismatic_state/native_crons.json"
    _write_store(legacy, [_legacy_entry("engine.doctor", "2020-01-01T00:00:00+00:00", status="failed", exit_code=9)])

    doctor = next(c for c in NativeCronStore().load() if c.id == "engine.doctor")
    assert doctor.last_exit_code == 0  # legacy stale history not imported
    assert doctor.last_status == "success"


def test_migration_tolerates_missing_and_corrupt_legacy(fake_home):
    bad = fake_home / ".prismatic/venv_bad/lib/python3.12/site-packages/prismatic_state/native_crons.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("not json {{{", encoding="utf-8")

    store = NativeCronStore()
    store.ensure_seeded()  # must not raise
    assert len(store.load()) > 0


def test_legacy_store_never_used_as_default(fake_home):
    """Even when a rich legacy store exists, the default never points at it."""
    legacy = fake_home / ".prismatic/venv_stable/lib/python3.12/site-packages/prismatic_state/native_crons.json"
    _write_store(legacy, [_legacy_entry("engine.doctor", "2026-09-27T10:00:00+00:00")])
    assert default_cron_store_path() != legacy
    assert NativeCronStore().path == canonical_cron_store_path()
