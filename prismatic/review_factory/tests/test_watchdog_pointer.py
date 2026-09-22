"""Tests for the phase tick's watchdog-policy evidence pointer.

Safety properties asserted:
- the tick resolves the policy actually running on the host (deployed v2 via
  the audit symlink), not absent evidence;
- an explicit --evidence-pointers entry always wins over the injected default;
- missing/unparseable policy resolves to absent evidence, so the armed check
  stays fail-closed (false), never an assumed verdict;
- the tick still files no request when the other exit gates are unmet.
"""

import json
from pathlib import Path

import pytest
import yaml

from prismatic.review_factory import phase_advancement as pa
from prismatic.review_factory.phase_advancement import (
    check_phase0_exit,
    load_evidence,
    main,
    resolve_watchdog_policy_path,
)

MODULE_SPEC = Path(pa.__file__).resolve().parent / "spec"
V2_POLICY = MODULE_SPEC / "watchdog_policy_v2.yaml"
V1_POLICY = MODULE_SPEC / "watchdog_policy_v1.yaml"

MONITOR_ONLY = "enabled: true\nmode: monitor-only\n"


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _write_policy(spec_dir: Path, **overrides) -> None:
    spec_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "version": "phase-v1",
        "phase": 0,
        "advancements_enabled": False,
        "approver": "mbgulden",
        "chunks": {"shadow_observer": "enabled_observe_only"},
    }
    data.update(overrides)
    (spec_dir / "phase_policy_v1.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )


@pytest.fixture()
def clean_env(monkeypatch):
    monkeypatch.delenv(pa.WATCHDOG_POLICY_ENV_VAR, raising=False)
    return monkeypatch


# ── resolve_watchdog_policy_path ─────────────────────────────────────


def test_explicit_flag_wins(tmp_path, clean_env):
    policy = _write(tmp_path / "custom.yaml", MONITOR_ONLY)
    assert resolve_watchdog_policy_path(str(policy)) == policy


def test_env_var_beats_audit_default(tmp_path, clean_env, monkeypatch):
    env_policy = _write(tmp_path / "env.yaml", MONITOR_ONLY)
    monkeypatch.setenv(pa.WATCHDOG_POLICY_ENV_VAR, str(env_policy))
    monkeypatch.setattr(pa, "AUDIT_WATCHDOG_POLICY", tmp_path / "missing.yaml")
    assert resolve_watchdog_policy_path() == env_policy


def test_audit_symlink_used_when_valid(tmp_path, clean_env, monkeypatch):
    audit = _write(tmp_path / "audit.yaml", MONITOR_ONLY)
    monkeypatch.setattr(pa, "AUDIT_WATCHDOG_POLICY", audit)
    assert resolve_watchdog_policy_path() == audit


def test_falls_back_to_shipped_v1(tmp_path, clean_env, monkeypatch):
    monkeypatch.setattr(pa, "AUDIT_WATCHDOG_POLICY", tmp_path / "missing.yaml")
    resolved = resolve_watchdog_policy_path()
    assert resolved == V1_POLICY


def test_unparseable_candidates_are_skipped(tmp_path, clean_env, monkeypatch):
    bad = _write(tmp_path / "bad.yaml", "not: [valid\n  yaml: : :")
    monkeypatch.setattr(pa, "AUDIT_WATCHDOG_POLICY", bad)
    # shipped v1 is still a valid fallback
    assert resolve_watchdog_policy_path() == V1_POLICY


def test_returns_none_when_nothing_resolves(tmp_path, clean_env, monkeypatch):
    monkeypatch.setattr(pa, "AUDIT_WATCHDOG_POLICY", tmp_path / "missing.yaml")
    monkeypatch.setattr(pa, "SPEC_DIR", tmp_path / "empty-spec")
    assert resolve_watchdog_policy_path() is None
    assert resolve_watchdog_policy_path(str(tmp_path / "nope.yaml")) is None


# ── the check itself is untouched ────────────────────────────────────


def test_armed_true_with_real_v2_monitor_only_policy():
    assert V2_POLICY.exists(), "repo must ship watchdog_policy_v2.yaml"
    evidence = load_evidence({"watchdog_policy": str(V2_POLICY)})
    result = check_phase0_exit(evidence)
    assert result.checks["watchdog_armed_monitor_only"] is True
    assert "watchdog=True/monitor-only" in result.detail


def test_armed_false_with_shipped_v1_hard_disabled():
    evidence = load_evidence({"watchdog_policy": str(V1_POLICY)})
    result = check_phase0_exit(evidence)
    assert result.checks["watchdog_armed_monitor_only"] is False


def test_armed_false_with_absent_evidence():
    result = check_phase0_exit({})
    assert result.checks["watchdog_armed_monitor_only"] is False
    assert "watchdog=None/None" in result.detail


# ── main() wiring ───────────────────────────────────────────────────


def _run_main(tmp_path, monkeypatch, extra_argv=(), audit_policy=None):
    spec_dir = tmp_path / "spec"
    _write_policy(spec_dir)
    monkeypatch.setattr(
        pa, "AUDIT_WATCHDOG_POLICY", audit_policy or tmp_path / "missing.yaml"
    )
    argv = [
        "--spec-dir",
        str(spec_dir),
        "--log",
        str(tmp_path / "adv-log.jsonl"),
        "--audit-sink",
        str(tmp_path / "adv-audit.jsonl"),
        *extra_argv,
    ]
    return main(argv)


def test_main_injects_audit_policy_and_stays_inert(
    tmp_path, clean_env, monkeypatch, capsys
):
    audit = _write(tmp_path / "audit.yaml", MONITOR_ONLY)
    rc = _run_main(tmp_path, monkeypatch, audit_policy=audit)
    assert rc == 0
    state = json.loads(capsys.readouterr().out)
    assert state["status"] == "no-request"  # other gates unmet: inert
    assert state["checks"]["watchdog_armed_monitor_only"] is True
    assert "watchdog=True/monitor-only" in state["detail"]
    # nothing filed: the log is never even created when inert
    log_path = tmp_path / "adv-log.jsonl"
    assert not log_path.exists() or "request" not in log_path.read_text(
        encoding="utf-8"
    )


def test_main_without_audit_symlink_stays_fail_closed(
    tmp_path, clean_env, monkeypatch, capsys
):
    # audit symlink absent -> falls back to shipped v1 (hard-disabled)
    rc = _run_main(tmp_path, monkeypatch)
    assert rc == 0
    state = json.loads(capsys.readouterr().out)
    assert state["status"] == "no-request"
    assert state["checks"]["watchdog_armed_monitor_only"] is False


def test_main_flag_overrides(tmp_path, clean_env, monkeypatch, capsys):
    flag_policy = _write(tmp_path / "flag.yaml", MONITOR_ONLY)
    audit = _write(tmp_path / "audit.yaml", "enabled: false\nmode: hard-disabled\n")
    rc = _run_main(
        tmp_path,
        monkeypatch,
        extra_argv=["--watchdog-policy", str(flag_policy)],
        audit_policy=audit,
    )
    assert rc == 0
    state = json.loads(capsys.readouterr().out)
    assert state["checks"]["watchdog_armed_monitor_only"] is True


def test_explicit_evidence_pointers_win_over_injection(
    tmp_path, clean_env, monkeypatch, capsys
):
    # a bogus explicit watchdog_policy pointer must fail closed (exit 1),
    # proving the injected default did not silently override it
    pointers_file = tmp_path / "pointers.json"
    pointers_file.write_text(
        json.dumps({"watchdog_policy": str(tmp_path / "nope.yaml")}),
        encoding="utf-8",
    )
    rc = _run_main(
        tmp_path,
        monkeypatch,
        extra_argv=["--evidence-pointers", str(pointers_file)],
    )
    assert rc == 1
    assert "cannot load evidence" in capsys.readouterr().out
