"""Tests for scripts/runner_fleet_watchdog.py.

Hermetic: fake GitHub client, fake systemctl, fake clock. No network, no
subprocess, no real time. The adversarial cases (grace, pre-restart race,
flap) fail against a naive restart-on-busy implementation.
"""

import importlib.util
import json
import os
import sys

import pytest

SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "runner_fleet_watchdog.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("runner_fleet_watchdog", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses needs this at exec time
    spec.loader.exec_module(module)
    return module


wd = load_module()


class FakeClock:
    def __init__(self, start=1_000_000.0):
        self.t = start

    def now(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class FakeGitHub:
    """Scripted GitHub client. runners/live are attributes tests mutate."""

    def __init__(self):
        self.runners = []
        self.live = set()
        self.fail_on = None  # "runners" | "live" | None
        self.calls = []

    def list_runners(self):
        self.calls.append("list_runners")
        if self.fail_on == "runners":
            raise RuntimeError("boom: runners")
        return list(self.runners)

    def live_job_runners(self):
        self.calls.append("live_job_runners")
        if self.fail_on == "live":
            raise RuntimeError("boom: live")
        return set(self.live)


class FakeSystemctl:
    def __init__(self, ok=True, on_restart=None):
        self.ok = ok
        self.on_restart = on_restart
        self.restarts = []

    def restart(self, unit):
        self.restarts.append(unit)
        if self.on_restart:
            self.on_restart(unit)
        return self.ok


def make_runner(name, busy=False, online=True):
    return wd.RunnerState(name=name, busy=busy, online=online)


@pytest.fixture
def env(tmp_path):
    clock = FakeClock()
    github = FakeGitHub()
    systemctl = FakeSystemctl()
    state = wd.WatchdogState(str(tmp_path / "state.json"), now=clock.now)
    alerter = wd.Alerter(str(tmp_path / "alerts.jsonl"))
    return {
        "clock": clock,
        "github": github,
        "systemctl": systemctl,
        "state": state,
        "alerter": alerter,
        "tmp": tmp_path,
    }


def run(env, **kw):
    params = dict(
        grace_seconds=300,
        unit_template="actions.runner.{repo_slug}.{runner_name}",
        repo="mbgulden/prismatic-engine",
        dry_run=False,
        post_restart_timeout=0.05,
        post_restart_poll_interval=0.001,
    )
    params.update(kw)
    return wd.run_cycle(
        env["github"], env["state"], env["alerter"], env["systemctl"], **params
    )


# --- classification ---------------------------------------------------------


def test_classify_legit_busy_with_live_job():
    r = make_runner("webtop-hermes", busy=True)
    assert wd.classify(r, {"webtop-hermes"}) == wd.LEGIT


def test_classify_legit_not_busy_with_live_job():
    # Transient: job attributed but busy flag not yet set. Never touch.
    r = make_runner("webtop-hermes", busy=False)
    assert wd.classify(r, {"webtop-hermes"}) == wd.LEGIT


def test_classify_phantom_candidate():
    r = make_runner("webtop-hermes", busy=True)
    assert wd.classify(r, set()) == wd.PHANTOM_CANDIDATE


def test_classify_idle():
    r = make_runner("webtop-hermes", busy=False)
    assert wd.classify(r, set()) == wd.IDLE


def test_classify_offline():
    r = make_runner("webtop-hermes", busy=True, online=False)
    assert wd.classify(r, set()) == wd.OFFLINE


# --- grace period (adversarial 1: no restart before grace elapses) -----------


def test_grace_not_confirmed_before_elapsed(env):
    env["github"].runners = [make_runner("webtop-hermes", busy=True)]
    result = run(env)
    assert result.recovered == []
    assert env["systemctl"].restarts == []
    # Still a candidate after 60s of a 300s grace: diagnosed, not acted on.
    env["clock"].advance(60)
    result = run(env)
    assert result.recovered == []
    assert env["systemctl"].restarts == []


def test_grace_confirmed_after_elapsed(env):
    env["github"].runners = [make_runner("webtop-hermes", busy=True)]
    # The restart clears the phantom: runner reports idle afterwards.
    env["systemctl"].on_restart = lambda unit: setattr(
        env["github"], "runners", [make_runner("webtop-hermes", busy=False)]
    )
    run(env)
    env["clock"].advance(301)
    result = run(env)
    assert result.recovered == ["webtop-hermes"]
    assert env["systemctl"].restarts == [
        "actions.runner.mbgulden-prismatic-engine.webtop-hermes"
    ]
    assert any(a["type"] == "runner_recovered" for a in env["alerter"].emitted)


# --- flap during grace clears candidacy (adversarial 3) ---------------------


def test_flap_clears_candidacy(env):
    env["github"].runners = [make_runner("webtop-hermes", busy=True)]
    run(env)  # candidate at t=0
    env["clock"].advance(120)
    # A real job starts: runner is legit now.
    env["github"].live = {"webtop-hermes"}
    run(env)
    env["clock"].advance(200)
    # Job finished, runner idle: no stale candidacy, no restart, no alert.
    env["github"].live = set()
    env["github"].runners = [make_runner("webtop-hermes", busy=False)]
    result = run(env)
    assert result.recovered == []
    assert env["systemctl"].restarts == []
    assert env["alerter"].emitted == []


# --- pre-restart race: live job appears after diagnosis (adversarial 2) ------


def test_recover_aborts_when_live_job_appears(env):
    env["github"].runners = [make_runner("webtop-hermes", busy=True)]
    run(env)
    env["clock"].advance(301)
    # Between the diagnosis fetch and the pre-restart fetch, a job lands.
    calls = {"n": 0}
    orig = FakeGitHub.live_job_runners

    def live_with_race(self):
        calls["n"] += 1
        if calls["n"] > 1:
            self.live = {"webtop-hermes"}
        return orig(self)

    env["github"].live_job_runners = live_with_race.__get__(env["github"], FakeGitHub)
    result = run(env)
    assert result.recovered == []
    assert env["systemctl"].restarts == [], (
        "hard invariant violated: restart issued with a live job"
    )
    types = [a["type"] for a in env["alerter"].emitted]
    assert "recovery_aborted_live_job" in types


def test_pre_restart_check_failure_aborts(env):
    env["github"].runners = [make_runner("webtop-hermes", busy=True)]
    run(env)
    env["clock"].advance(301)
    # Diagnosis fetch succeeds; the pre-restart fetch fails.
    calls = {"n": 0}
    orig = FakeGitHub.live_job_runners

    def flaky(self):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("boom: live")
        return orig(self)

    env["github"].live_job_runners = flaky.__get__(env["github"], FakeGitHub)
    result = run(env)
    assert result.recovered == []
    assert env["systemctl"].restarts == []
    types = [a["type"] for a in env["alerter"].emitted]
    assert "recovery_aborted_check_failed" in types


# --- dry-run never restarts --------------------------------------------------


def test_dry_run_never_restarts_and_notifies_once(env):
    env["github"].runners = [make_runner("webtop-hermes", busy=True)]
    run(env, dry_run=True)
    env["clock"].advance(301)
    result = run(env, dry_run=True)
    assert result.recovered == []
    assert env["systemctl"].restarts == []
    dry = [
        a
        for a in env["alerter"].emitted
        if a["type"] == "recovery_dry_run_would_restart"
    ]
    assert len(dry) == 1
    assert (
        dry[0]["detail"]["unit"]
        == "actions.runner.mbgulden-prismatic-engine.webtop-hermes"
    )
    # Repeating the cycle does not re-alert.
    result = run(env, dry_run=True)
    dry = [
        a
        for a in env["alerter"].emitted
        if a["type"] == "recovery_dry_run_would_restart"
    ]
    assert len(dry) == 1


# --- offline: alert, never restart -------------------------------------------


def test_offline_alerts_no_restart(env):
    env["github"].runners = [make_runner("webtop-hermes", busy=True, online=False)]
    result = run(env)
    assert result.recovered == []
    assert env["systemctl"].restarts == []
    types = [a["type"] for a in env["alerter"].emitted]
    assert "runner_offline" in types


# --- restart failure paths ----------------------------------------------------


def test_restart_failure_alerts(env):
    env["systemctl"] = FakeSystemctl(ok=False)
    env["github"].runners = [make_runner("webtop-hermes", busy=True)]
    run(env)
    env["clock"].advance(301)
    result = run(env)
    assert result.recovered == []
    types = [a["type"] for a in env["alerter"].emitted]
    assert "runner_restart_failed" in types


def test_recovery_unverified_when_runner_stays_down(env):
    env["github"].runners = [make_runner("webtop-hermes", busy=True)]
    run(env)
    env["clock"].advance(301)
    # Runner stays busy with no live job even after the restart.
    result = run(env)
    assert result.recovered == []
    assert env["systemctl"].restarts == [
        "actions.runner.mbgulden-prismatic-engine.webtop-hermes"
    ]
    types = [a["type"] for a in env["alerter"].emitted]
    assert "runner_recovery_unverified" in types


# --- safe degradation --------------------------------------------------------


def test_jobs_fetch_failure_degrades_safe(env):
    env["github"].runners = [make_runner("webtop-hermes", busy=True)]
    env["github"].fail_on = "live"
    result = run(env)
    assert result.error != ""
    assert result.recovered == []
    assert env["systemctl"].restarts == []
    types = [a["type"] for a in env["alerter"].emitted]
    assert "check_failed" in types


def test_corrupt_state_file_restarts_grace(env):
    path = str(env["tmp"] / "state.json")
    with open(path, "w") as f:
        f.write("{not json")
    env["github"].runners = [make_runner("webtop-hermes", busy=True)]
    state = wd.WatchdogState(path, now=env["clock"].now)
    result = wd.run_cycle(
        env["github"],
        state,
        env["alerter"],
        env["systemctl"],
        grace_seconds=300,
        unit_template="u.{runner_name}",
        repo="o/r",
        dry_run=False,
        post_restart_timeout=0.05,
        post_restart_poll_interval=0.001,
    )
    # Corrupt state -> grace restarts from zero -> no restart yet.
    assert result.recovered == []
    assert env["systemctl"].restarts == []


# --- state persistence & alert schema -----------------------------------------


def test_state_persists_across_cycles(env):
    path = str(env["tmp"] / "state.json")
    env["github"].runners = [make_runner("webtop-hermes", busy=True)]
    run(env)
    # New state object, same file, clock advanced past grace: confirmed.
    env["clock"].advance(301)
    state2 = wd.WatchdogState(path, now=env["clock"].now)
    assert state2.is_confirmed("webtop-hermes", 300)


def test_alert_jsonl_schema(env):
    env["github"].runners = [make_runner("webtop-hermes", busy=True, online=False)]
    run(env)
    log = env["tmp"] / "alerts.jsonl"
    lines = log.read_text().strip().split("\n")
    assert len(lines) == 1
    alert = json.loads(lines[0])
    assert set(alert) == {"ts", "type", "runner", "detail"}
    assert alert["type"] == "runner_offline"
    assert alert["runner"] == "webtop-hermes"


def test_unit_template_rendering():
    assert (
        wd.unit_for_runner(
            "actions.runner.{repo_slug}.{runner_name}",
            "mbgulden/prismatic-engine",
            "webtop-hermes-2",
        )
        == "actions.runner.mbgulden-prismatic-engine.webtop-hermes-2"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
