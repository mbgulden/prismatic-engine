"""Tests for the orphan wiring (approved Sep 22, 2026).

Three modules were merged but never called; this file pins their wiring:

- phase_advancement: the main() heartbeat CLI returns no-request when
  criteria are unmet, files a request on synthetic passing evidence, and
  execute() still refuses without approval + evidence (requesting !=
  executing).
- learn_loop: self_review() returns "disabled" while the loop is disabled;
  record_outcome() refuses while disabled; the outcome-join logic joins
  synthetic decision/outcome logs by job_id.
- novelty: the merge-stage monitor-only screen logs trips without halting
  the pipeline and stays silent on clean candidates; the detector's own
  quarantine registry is never touched by the screen.

All enable flags stay off in production: these tests use synthetic,
explicitly-armed fixtures and never flip the shipped configs.
"""

import json
from pathlib import Path

import yaml

from prismatic.review_factory import learn_loop as learn_loop_mod
from prismatic.review_factory import phase_advancement as pa_mod
from prismatic.review_factory.learn_loop import LearnLoop
from prismatic.review_factory.merge_stage import MergeStage, MergeStageConfig
from prismatic.review_factory.novelty import (
    MODE_MONITOR_ONLY,
    STATE_DISABLED,
    STATE_FAMILIAR,
    STATE_NOVEL_MONITOR,
    NoveltyDetector,
)

SPEC_DIR = Path(__file__).resolve().parent.parent / "spec"
SHIPPED_LEARN_POLICY = SPEC_DIR / "learn_loop_policy_v1.yaml"
SHIPPED_BANDS = SPEC_DIR / "auto_merge_bands_v1.yaml"
SHIPPED_NOVELTY_POLICY = SPEC_DIR / "novelty_policy_v1.yaml"


# ── helpers ──────────────────────────────────────────────────────────


def _write_phase_policy(spec_dir: Path, phase=0, enabled=False) -> None:
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "phase_policy_v1.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "phase-v1",
                "phase": phase,
                "advancements_enabled": enabled,
                "approver": "mbgulden",
                "chunks": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _shadow_signal(i: int) -> dict:
    return {
        "agent": "shadow-observer",
        "event_type": "shadow_decision",
        "id": f"sig-{i}",
        "message": "shadow call",
        "metadata": {
            "pr_number": i,
            "call": "merge",
            "policy_version": "shadow-v1",
        },
        "severity": "info",
        "source": "test",
        "status": "ok",
        "timestamp": "2026-09-22T00:00:00+00:00",
    }


def _write_evidence_bundle(tmp_path: Path) -> Path:
    """Pointers file for synthetic PASSING Phase 0 -> 1 evidence."""
    records = [{"system_call": "merge", "actual_outcome": "merged"} for _ in range(30)]
    records_path = tmp_path / "shadow_records.jsonl"
    records_path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    signals_path = tmp_path / "shadow_signals.jsonl"
    signals_path.write_text(
        "\n".join(json.dumps(_shadow_signal(i)) for i in range(30)),
        encoding="utf-8",
    )
    watchdog_path = tmp_path / "watchdog.yaml"
    watchdog_path.write_text(
        yaml.safe_dump({"enabled": True, "mode": "monitor-only"}),
        encoding="utf-8",
    )
    pointers = {
        "shadow_records": str(records_path),
        "shadow_signals": str(signals_path),
        "bad_merge_calls": 0,
        "watchdog_policy": str(watchdog_path),
    }
    pointers_path = tmp_path / "evidence_pointers.json"
    pointers_path.write_text(json.dumps(pointers), encoding="utf-8")
    return pointers_path


def _cli_args(tmp_path: Path, **overrides) -> list:
    spec_dir = tmp_path / "spec"
    _write_phase_policy(spec_dir)
    args = [
        "--spec-dir",
        str(spec_dir),
        "--log",
        str(tmp_path / "adv-log.jsonl"),
        "--audit-sink",
        str(tmp_path / "adv-audit.jsonl"),
    ]
    for key, value in overrides.items():
        args.extend([f"--{key.replace('_', '-')}", str(value)])
    return args


def _learn_loop(tmp_path: Path, enabled: bool = False) -> LearnLoop:
    policy = yaml.safe_load(SHIPPED_LEARN_POLICY.read_text(encoding="utf-8"))
    policy["enabled"] = enabled
    policy_path = tmp_path / "learn_policy_test.yaml"
    policy_path.write_text(yaml.safe_dump(policy), encoding="utf-8")
    bands = yaml.safe_load(SHIPPED_BANDS.read_text(encoding="utf-8"))
    bands_path = tmp_path / "learn_bands_test.yaml"
    bands_path.write_text(yaml.safe_dump(bands), encoding="utf-8")
    return LearnLoop(
        policy_path=policy_path,
        bands_path=bands_path,
        decision_log=tmp_path / "decisions.jsonl",
        outcome_log=tmp_path / "outcomes.jsonl",
        band_change_log=tmp_path / "band-changes.jsonl",
        audit_log=tmp_path / "learn-audit.jsonl",
        now_fn=lambda: 1_758_000_000.0,
    )


def _decision_row(job_id: str, ts: float = 1_758_000_000.0) -> dict:
    return {
        "job_id": job_id,
        "ts": ts,
        "ts_iso": "2026-09-22T00:00:00+00:00",
        "component": "merge_authority",
        "policy_version": "auto-v1",
        "decision": "allowed",
        "tier": 0,
    }


def _monitor_detector(tmp_path: Path) -> NoveltyDetector:
    policy = yaml.safe_load(SHIPPED_NOVELTY_POLICY.read_text(encoding="utf-8"))
    policy["enabled"] = True
    policy["mode"] = MODE_MONITOR_ONLY
    policy_path = tmp_path / "novelty_policy_monitor.yaml"
    policy_path.write_text(yaml.safe_dump(policy), encoding="utf-8")
    return NoveltyDetector(policy_path, audit_log=tmp_path / "novelty-audit.jsonl")


class _StubJob:
    """Minimal merge-candidate stand-in for the novelty screen."""

    def __init__(self, job_id: str = "job-1", **novelty_attrs):
        self.review_job_id = job_id
        self.risk_tier = 0
        for key, value in novelty_attrs.items():
            setattr(self, key, value)


class _StubDB:
    def __init__(self):
        self.audit_rows: list[dict] = []

    def insert_audit_entry(self, actor, action, review_job_id, details):
        self.audit_rows.append(
            {
                "actor": actor,
                "action": action,
                "review_job_id": review_job_id,
                "details": details,
            }
        )

    def find_audit_entry(self, job_id, action):
        return next(
            (
                r
                for r in self.audit_rows
                if r["review_job_id"] == job_id and r["action"] == action
            ),
            None,
        )

    def update_review_job_state(self, job_id, state):
        return True

    def consume_authorization(self, auth_id):
        return True


class _StubQueue:
    def __init__(self):
        self.db = _StubDB()

    def authorize_merge(self, job_id, actor=None):
        return "auth-1"


class _StubMergeResult:
    def __init__(self, success=True):
        self.success = success
        self.merge_sha = "dry-run-sha"
        self.error = ""


class _StubExecutor:
    def __init__(self, *args, **kwargs):
        pass

    def execute(self, job_id):
        return _StubMergeResult(success=True)


# ── phase_advancement CLI ────────────────────────────────────────────


def test_cli_no_request_when_criteria_unmet(tmp_path, capsys):
    """The heartbeat with no evidence files a nothing: criteria unmet."""
    rc = pa_mod.main(_cli_args(tmp_path))
    assert rc == 0
    state = json.loads(capsys.readouterr().out)
    assert state["status"] == "no-request"
    assert state["criteria_met"] is False
    assert state["phase"] == 0
    assert state["target"] == 1
    # No request row was filed.
    assert not (tmp_path / "adv-log.jsonl").exists()


def test_cli_files_request_on_synthetic_passing_evidence(tmp_path, capsys):
    """Synthetic passing Phase 0 -> 1 evidence files a request (not an
    advancement)."""
    pointers = _write_evidence_bundle(tmp_path)
    rc = pa_mod.main(
        _cli_args(tmp_path, evidence_pointers=str(pointers), requester="test-tick")
    )
    assert rc == 0
    state = json.loads(capsys.readouterr().out)
    assert state["status"] == "request-filed"
    assert state["criteria_met"] is True
    assert state["request_id"]
    assert state["requester"] == "test-tick"
    rows = [
        json.loads(line)
        for line in (tmp_path / "adv-log.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    request_rows = [r for r in rows if r.get("type") == "request"]
    assert len(request_rows) == 1
    assert request_rows[0]["payload"]["request_id"] == state["request_id"]


def test_execute_still_refuses_without_approval_and_evidence(tmp_path, capsys):
    """Filing a request does not advance anything: execute() stays
    fail-closed without the master switch + Michael's approval record."""
    pointers = _write_evidence_bundle(tmp_path)
    pa_mod.main(_cli_args(tmp_path, evidence_pointers=str(pointers)))
    state = json.loads(capsys.readouterr().out)
    request_id = state["request_id"]

    adv = pa_mod.PhaseAdvancement(
        spec_dir=tmp_path / "spec",
        log_path=tmp_path / "adv-log.jsonl",
        audit_sink=tmp_path / "adv-audit.jsonl",
    )
    # Master switch is off (shipped default): refuses first.
    result = adv.execute(request_id, {}, {})
    assert result.executed is False
    assert any("advancements_enabled" in r for r in result.reasons)
    assert adv.current_phase() == 0

    # Even with the switch on, an approval-less attempt refuses.
    _write_phase_policy(tmp_path / "spec", enabled=True)
    result = adv.execute(request_id, {"schema": "nope"}, {"bad_merge_calls": 5})
    assert result.executed is False
    assert any("approval invalid" in r for r in result.reasons)
    assert adv.current_phase() == 0


# ── learn_loop ───────────────────────────────────────────────────────


def test_self_review_main_returns_disabled(tmp_path, capsys):
    """The CLI runs self_review(); with the shipped (disabled) policy the
    report is inert."""
    rc = learn_loop_mod.main(
        [
            "--policy",
            str(SHIPPED_LEARN_POLICY),
            "--bands",
            str(SHIPPED_BANDS),
            "--decision-log",
            str(tmp_path / "decisions.jsonl"),
            "--outcome-log",
            str(tmp_path / "outcomes.jsonl"),
            "--band-change-log",
            str(tmp_path / "band-changes.jsonl"),
            "--audit-log",
            str(tmp_path / "learn-audit.jsonl"),
        ]
    )
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == STATE_DISABLED
    assert report["auto_merges"] == 0


def test_record_outcome_refuses_when_disabled(tmp_path):
    loop = _learn_loop(tmp_path, enabled=False)
    result = loop.record_outcome("job-1", "clean")
    assert result["status"] == "refused"
    assert result["reason"] == "learn_loop_disabled"
    # Nothing was written to the outcome log.
    assert not (tmp_path / "outcomes.jsonl").exists()


def test_outcome_join_on_synthetic_logs(tmp_path):
    """record_outcome joins to the decision log by job_id on synthetic
    logs (enabled fixture): two jobs, two outcomes, joined correctly."""
    loop = _learn_loop(tmp_path, enabled=True)
    decisions = tmp_path / "decisions.jsonl"
    decisions.write_text(
        "\n".join(
            json.dumps(_decision_row(j, ts=1_758_000_000.0 + i))
            for i, j in enumerate(("job-a", "job-b"))
        ),
        encoding="utf-8",
    )
    assert loop.record_outcome("job-a", "clean")["status"] == "ok"
    assert loop.record_outcome("job-b", "rolled_back")["status"] == "ok"

    outcome_rows = [
        json.loads(line)
        for line in (tmp_path / "outcomes.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    decision_rows = {
        r["job_id"]: r
        for r in (
            json.loads(line)
            for line in decisions.read_text(encoding="utf-8").splitlines()
        )
    }
    assert len(outcome_rows) == 2
    by_job = {r["job_id"]: r["outcome"] for r in outcome_rows}
    assert by_job == {"job-a": "clean", "job-b": "rolled_back"}
    # The join holds: every outcome's job_id has a decision row.
    assert all(job_id in decision_rows for job_id in by_job)

    # Duplicate outcomes and unknown jobs refuse (append-only, joined).
    dup = loop.record_outcome("job-a", "clean")
    assert dup["status"] == "refused"
    assert "outcome_already_recorded" in dup["reason"]
    unknown = loop.record_outcome("job-zzz", "clean")
    assert unknown["status"] == "refused"
    assert "unknown_job" in unknown["reason"]


# ── novelty monitor-only screen ──────────────────────────────────────


def test_monitor_only_trip_logs_without_halting(tmp_path):
    """A tripping candidate in monitor-only mode: the trip is logged to
    both audits, the pipeline is NOT halted, nothing is quarantined."""
    detector = _monitor_detector(tmp_path)
    queue = _StubQueue()
    stage = MergeStage(
        queue,
        MergeStageConfig(enabled=True, dry_run=True, live_tiers=frozenset({0})),
        novelty_detector=detector,
    )
    job = _StubJob(
        "job-trip",
        novelty_error_classes=("brand-new-error",),
        novelty_known_error_classes=(),
    )
    stage._novelty_screen(job, "job-trip")

    entry = queue.db.find_audit_entry("job-trip", "merge_novelty_screen")
    assert entry is not None
    assert entry["details"]["novelty_state"] == STATE_NOVEL_MONITOR
    assert entry["details"]["mode"] == MODE_MONITOR_ONLY
    assert "unknown_error_class" in entry["details"]["tripped_inputs"]
    assert entry["details"]["quarantined"] is False
    assert entry["details"]["pipeline_halted"] is False

    # The detector's own quarantine registry is untouched by the screen.
    assert detector.quarantined_ids == ()


def test_clean_candidate_no_trip(tmp_path):
    """A familiar candidate produces no trip — the screen stays silent
    except for the audit row."""
    detector = _monitor_detector(tmp_path)
    queue = _StubQueue()
    stage = MergeStage(queue, MergeStageConfig(), novelty_detector=detector)
    job = _StubJob("job-clean")  # no novelty attrs: everything unavailable
    stage._novelty_screen(job, "job-clean")

    entry = queue.db.find_audit_entry("job-clean", "merge_novelty_screen")
    assert entry is not None
    assert entry["details"]["novelty_state"] == STATE_FAMILIAR
    assert entry["details"]["tripped_inputs"] == []
    assert detector.quarantined_ids == ()


def test_screen_off_when_detector_none(tmp_path):
    stage = MergeStage(_StubQueue(), MergeStageConfig(), novelty_detector=None)
    stage._novelty_screen(_StubJob("job-x"), "job-x")
    assert stage.queue.db.find_audit_entry("job-x", "merge_novelty_screen") is None


def test_process_continues_past_trip(tmp_path, monkeypatch):
    """End to end through process(): a tripping candidate still reaches the
    dry-run authorization — the pipeline is not halted."""
    monkeypatch.setattr(
        "prismatic.review_factory.merge_executor.MergeExecutor", _StubExecutor
    )
    detector = _monitor_detector(tmp_path)
    queue = _StubQueue()
    stage = MergeStage(
        queue,
        MergeStageConfig(enabled=True, dry_run=True, live_tiers=frozenset({0})),
        novelty_detector=detector,
    )
    job = _StubJob(
        "job-pipe",
        novelty_error_classes=("brand-new-error",),
        novelty_known_error_classes=(),
    )
    result = stage.process(job)
    assert result.action == "dry_run_ok"
    assert result.authorization_id == "auth-1"
    screen = queue.db.find_audit_entry("job-pipe", "merge_novelty_screen")
    assert screen is not None
    assert screen["details"]["novelty_state"] == STATE_NOVEL_MONITOR


def test_screen_inert_while_shipped_policy_disabled(tmp_path):
    """With the shipped (disabled) policy the screen evaluates to
    'disabled' — inert, no input read, no trip."""
    detector = NoveltyDetector(
        SHIPPED_NOVELTY_POLICY, audit_log=tmp_path / "novelty-audit.jsonl"
    )
    queue = _StubQueue()
    stage = MergeStage(queue, MergeStageConfig(), novelty_detector=detector)
    job = _StubJob(
        "job-inert",
        novelty_error_classes=("brand-new-error",),
        novelty_known_error_classes=(),
    )
    stage._novelty_screen(job, "job-inert")
    entry = queue.db.find_audit_entry("job-inert", "merge_novelty_screen")
    assert entry is not None
    assert entry["details"]["novelty_state"] == STATE_DISABLED
    assert entry["details"]["tripped_inputs"] == []
    assert detector.quarantined_ids == ()
