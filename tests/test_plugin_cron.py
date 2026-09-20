"""Tests for the prismatic-cron capability plugin (plugins/cron/).

Written against the generic plugin lifecycle contract:
  PrismaticPlugin.on_suspend() -> dict / on_resume(state: dict),
  PluginLoader.enable/disable/unload(name), plugin state persisted at
  $PRISMATIC_HOME/plugin-state/<name>/state.json, manifest config_schema
  validated at attach, auto_enable: false.

The tick is driven manually (no sleeping): tests call _tick_once() with an
explicit datetime.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_ROOT / "plugins" / "cron"

sys.path.insert(0, str(REPO_ROOT / "plugins"))

from cron.plugin import (  # noqa: E402
    PLUGIN_NAME,
    CronPlugin,
    plugin_state_dir,
    redact_secrets,
    run_idempotency_key,
    validate_job,
)
from prismatic.interface.plugin import PluginContext, PluginValidationError  # noqa: E402
from swarmcron.scheduler import CronScheduleEvaluator  # noqa: E402


def make_context(tmp_home: Path, jobs, **extra) -> PluginContext:
    os.environ["PRISMATIC_HOME"] = str(tmp_home)
    config = {"jobs": jobs}
    config.update(extra)
    return PluginContext(config=config, db_connection=None, state_dir=str(tmp_home))


def script_job(name="nightly-echo", schedule="* * * * *", enabled=True, **kw):
    job = {
        "name": name,
        "schedule": schedule,
        "handler": {"kind": "script", "ref": "echo hello-cron"},
        "enabled": enabled,
    }
    job.update(kw)
    return job


@pytest.fixture()
def plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path))
    p = CronPlugin()
    yield p
    # never leave a tick thread running
    p._stop_tick_thread()


# ── manifest ─────────────────────────────────────────────────────────────

def test_manifest_parses_with_required_fields():
    manifest = yaml.safe_load((PLUGIN_DIR / "plugin-manifest.yaml").read_text())
    assert manifest["name"] == "prismatic-cron"
    assert manifest["version"] == "0.1.0"
    assert manifest["entry_point"] == "cron.plugin:CronPlugin"
    assert manifest["core_version_constraint"] == ">=0.2.0, <2.0.0"
    assert "swarmcron>=0.3.0" in manifest["dependencies"]["pip"]
    assert set(manifest["tags"]) == {"infrastructure", "optional"}
    assert manifest["auto_enable"] is False
    assert manifest["config_schema"]["type"] == "object"


def test_config_schema_accepts_valid_jobs_config():
    import jsonschema

    manifest = yaml.safe_load((PLUGIN_DIR / "plugin-manifest.yaml").read_text())
    schema = manifest["config_schema"]
    config = {
        "jobs": [
            {
                "name": "nightly-report",
                "schedule": "0 2 * * *",
                "handler": {"kind": "script", "ref": "/usr/local/bin/report.sh"},
                "enabled": True,
                "deliver": {"channel": "email", "target": "ops@example.com"},
                "metadata": {"team": "ops"},
                "policy": {"max_runs_per_day": 1},
            },
            {
                "name": "agent-nudge",
                "schedule": "*/15 * * * *",
                "handler": {"kind": "prompt", "ref": "prompts/daily-standup.md"},
            },
            {
                "name": "ping-hook",
                "schedule": "0 * * * *",
                "handler": {"kind": "webhook", "ref": "https://example.com/hook"},
                "enabled": False,
            },
        ]
    }
    jsonschema.validate(config, schema)  # must not raise


def test_config_schema_rejects_bad_cron_expr():
    import jsonschema

    manifest = yaml.safe_load((PLUGIN_DIR / "plugin-manifest.yaml").read_text())
    schema = manifest["config_schema"]
    bad = {"jobs": [script_job(schedule="not-a-cron")]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


def test_on_init_rejects_bad_cron_expr_defensively(plugin, tmp_path):
    ctx = make_context(tmp_path, [script_job(schedule="*/10")])  # 1 field
    with pytest.raises(PluginValidationError):
        plugin.on_init(ctx)


# ── evaluator ────────────────────────────────────────────────────────────

def test_evaluator_computes_next_fire():
    ev = CronScheduleEvaluator("0 9 * * *")
    start = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    nxt = ev.get_next_run(from_dt=start)
    assert nxt is not None
    assert (nxt.hour, nxt.minute) == (9, 0)
    assert nxt > start


def test_evaluator_matches_now():
    now = datetime(2026, 9, 20, 9, 30, tzinfo=timezone.utc)  # a Sunday
    assert CronScheduleEvaluator("* * * * *").matches(now)
    assert not CronScheduleEvaluator("0 0 * * *").matches(now)


# ── fire path ────────────────────────────────────────────────────────────

def test_fire_writes_receipt_with_idempotency_key(plugin, tmp_path):
    ctx = make_context(tmp_path, [script_job()])
    plugin.on_init(ctx)
    plugin._stop_tick_thread()  # drive manually, no background ticks

    now = datetime(2026, 9, 20, 12, 34, tzinfo=timezone.utc)
    receipts = plugin._tick_once(now)
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["status"] == "ok"
    assert receipt["job"] == "nightly-echo"
    assert "hello-cron" in receipt["output_tail"]

    expected_key = run_idempotency_key(
        PLUGIN_NAME, "nightly-echo", "2026-09-20T12:34", "script", "echo hello-cron"
    )
    assert receipt["idempotency_key"] == expected_key
    assert len(expected_key) == 64

    runs_path = plugin_state_dir() / "runs.jsonl"
    lines = runs_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    stored = json.loads(lines[0])
    assert stored["idempotency_key"] == expected_key


def test_same_minute_never_double_fires(plugin, tmp_path):
    ctx = make_context(tmp_path, [script_job()])
    plugin.on_init(ctx)
    plugin._stop_tick_thread()
    now = datetime(2026, 9, 20, 12, 34, tzinfo=timezone.utc)
    assert len(plugin._tick_once(now)) == 1
    assert plugin._tick_once(now) == []  # same minute: no refire


def test_disabled_job_never_fires(plugin, tmp_path):
    ctx = make_context(tmp_path, [script_job(enabled=False)])
    plugin.on_init(ctx)
    plugin._stop_tick_thread()
    now = datetime(2026, 9, 20, 12, 34, tzinfo=timezone.utc)
    assert plugin._tick_once(now) == []
    assert not (plugin_state_dir() / "runs.jsonl").exists()


def test_prompt_handler_records_dispatched_receipt(plugin, tmp_path):
    ctx = make_context(
        tmp_path,
        [{"name": "nudge", "schedule": "* * * * *",
          "handler": {"kind": "prompt", "ref": "prompts/standup.md"}}],
    )
    plugin.on_init(ctx)
    plugin._stop_tick_thread()
    now = datetime(2026, 9, 20, 12, 34, tzinfo=timezone.utc)
    receipts = plugin._tick_once(now)
    assert len(receipts) == 1
    assert receipts[0]["status"] == "prompt-dispatched"
    assert receipts[0]["prompt_ref"] == "prompts/standup.md"


def test_max_runs_per_day_policy_skips(plugin, tmp_path):
    ctx = make_context(
        tmp_path, [script_job(policy={"max_runs_per_day": 1})]
    )
    plugin.on_init(ctx)
    plugin._stop_tick_thread()
    first = datetime(2026, 9, 20, 12, 34, tzinfo=timezone.utc)
    second = datetime(2026, 9, 20, 12, 35, tzinfo=timezone.utc)
    assert plugin._tick_once(first)[0]["status"] == "ok"
    skipped = plugin._tick_once(second)[0]
    assert skipped["status"] == "skipped"
    assert "max_runs_per_day" in skipped["reason"]


def test_script_output_redacts_secrets():
    out = redact_secrets("done token=abc123 and api_key: hunter2")
    assert "abc123" not in out
    assert "hunter2" not in out
    assert "[REDACTED]" in out


# ── suspend / resume round-trip ──────────────────────────────────────────

def test_suspend_resume_round_trips_jobs_and_history(plugin, tmp_path):
    ctx = make_context(tmp_path, [script_job(), script_job(name="second", schedule="0 0 * * *")])
    plugin.on_init(ctx)
    plugin._stop_tick_thread()
    now = datetime(2026, 9, 20, 12, 34, tzinfo=timezone.utc)
    plugin._tick_once(now)

    snapshot = plugin.on_suspend()
    assert plugin._tick_thread is None  # tick thread stopped
    assert {j["name"] for j in snapshot["jobs"]} == {"nightly-echo", "second"}
    assert len(snapshot["runs_tail"]) == 1
    # belt-and-braces file written at the contract path
    assert (plugin_state_dir() / "state.json").exists()

    resumed = CronPlugin()
    try:
        resumed.on_resume(snapshot)
        resumed._stop_tick_thread()
        assert set(resumed._jobs) == {"nightly-echo", "second"}
        assert len(resumed._runs) == 1
        assert resumed._runs[0]["job"] == "nightly-echo"
        # idempotency survives resume: same minute does not refire
        assert resumed._tick_once(now) == []
        # a later minute fires again (new idempotency key)
        later = datetime(2026, 9, 20, 12, 35, tzinfo=timezone.utc)
        assert len(resumed._tick_once(later)) == 1
    finally:
        resumed._stop_tick_thread()


def test_on_init_restores_history_from_runs_jsonl(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path))
    first = CronPlugin()
    try:
        first.on_init(make_context(tmp_path, [script_job()]))
        first._stop_tick_thread()
        first._tick_once(datetime(2026, 9, 20, 12, 34, tzinfo=timezone.utc))
        first.on_suspend()
    finally:
        first._stop_tick_thread()

    # fresh instance, no suspend snapshot passed through on_resume:
    # belt-and-braces restore reads runs.jsonl on init.
    second = CronPlugin()
    try:
        second.on_init(make_context(tmp_path, [script_job()]))
        second._stop_tick_thread()
        assert len(second._runs) == 1
        assert second._tick_once(datetime(2026, 9, 20, 12, 34, tzinfo=timezone.utc)) == []
    finally:
        second._stop_tick_thread()


# ── routes / contract / mapping ──────────────────────────────────────────

def test_jobs_status_payload(plugin, tmp_path):
    ctx = make_context(tmp_path, [script_job(), script_job(name="off", enabled=False)])
    plugin.on_init(ctx)
    plugin._stop_tick_thread()
    plugin._tick_once(datetime(2026, 9, 20, 12, 34, tzinfo=timezone.utc))

    payload = plugin.jobs_status()
    by_name = {j["name"]: j for j in payload}
    assert set(by_name) == {"nightly-echo", "off"}
    entry = by_name["nightly-echo"]
    assert entry["schedule"] == "* * * * *"
    assert entry["enabled"] is True
    assert entry["next_fire_at"] is not None
    assert entry["last_run"] == {"fired_at": entry["last_run"]["fired_at"], "status": "ok"}
    assert by_name["off"]["last_run"] is None


def test_register_api_routes_does_not_touch_core_server():
    routes = CronPlugin().register_api_routes()
    assert routes[0]["method"] == "GET"
    assert routes[0]["path"] == "/api/cron/jobs"


def test_register_tools_returns_empty():
    assert CronPlugin().register_tools() == []


def test_to_schedule_record_mapping(plugin, tmp_path):
    ctx = make_context(tmp_path, [script_job()])
    plugin.on_init(ctx)
    plugin._stop_tick_thread()
    rec = plugin.to_schedule_record(plugin._jobs["nightly-echo"])
    assert rec.owner == "prismatic"
    assert rec.schedule_type == "cron"
    assert rec.schedule_expr == "* * * * *"
    assert rec.enabled is True
    assert rec.id == "cron:nightly-echo"
    assert rec.metadata["handler_kind"] == "script"
    # ScheduleRecord round-trips through its own serialization
    assert type(rec).from_dict(rec.to_dict()).id == rec.id


def test_validate_job_rejects_unknown_handler_kind():
    with pytest.raises(PluginValidationError):
        validate_job({"name": "x", "schedule": "* * * * *",
                      "handler": {"kind": "sms", "ref": "y"}})


def test_validate_job_rejects_duplicate_names(plugin, tmp_path):
    ctx = make_context(tmp_path, [script_job(), script_job()])
    with pytest.raises(PluginValidationError):
        plugin.on_init(ctx)


def test_on_init_accepts_loader_nested_plugin_configs(plugin, tmp_path, monkeypatch):
    """The generic PluginLoader nests the validated plugin config at
    context.config["plugin_configs"]["prismatic-cron"]; on_init must read
    jobs from there, not only from a top-level config."""
    monkeypatch.setenv("PRISMATIC_HOME", str(tmp_path))
    jobs = [script_job(name="loader-job")]
    ctx = PluginContext(
        config={"plugin_configs": {"prismatic-cron": {"jobs": jobs}}},
        db_connection=None,
        state_dir=str(tmp_path),
    )
    plugin.on_init(ctx)
    assert [j["name"] for j in plugin.jobs_status()] == ["loader-job"]


def test_on_resume_with_empty_state_keeps_config_jobs(plugin, tmp_path):
    """First enable passes {} (no state file yet): the jobs on_init loaded
    from config must survive — on_resume must not wipe them."""
    ctx = make_context(tmp_path, [script_job(name="keep-me")])
    plugin.on_init(ctx)
    plugin._stop_tick_thread()
    try:
        plugin.on_resume({})
        assert [j["name"] for j in plugin.jobs_status()] == ["keep-me"]
    finally:
        plugin._stop_tick_thread()


def test_on_resume_does_not_resurrect_jobs_when_config_emptied(plugin, tmp_path):
    """An explicitly emptied config stays empty even when a suspend snapshot
    holds old definitions."""
    ctx = make_context(tmp_path, [script_job(name="old")])
    plugin.on_init(ctx)
    plugin._stop_tick_thread()
    snapshot = plugin.on_suspend()

    cleared = CronPlugin()
    try:
        cleared.on_init(make_context(tmp_path, []))  # explicit empty jobs
        cleared._stop_tick_thread()
        cleared.on_resume(snapshot.get("state", snapshot))
        assert cleared.jobs_status() == []
    finally:
        cleared._stop_tick_thread()
