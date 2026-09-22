"""Tests for the learn-loop band-change application path (apply_band_change).

Safety properties pinned here:
- apply re-validates exactly like propose: unknown key, non-numeric value,
  out-of-caps, loosen-without-evidence, and disabled-loop all refuse with
  nothing written;
- over-budget loosenings apply only for Michael (by="mbgulden");
- the metrics-feed event is recorded BEFORE anything is written: a feed
  failure raises and no spec file or log row appears;
- the live bands file is never overwritten -- the new versioned spec is
  filed alongside it;
- every apply emits an apply_band_change audit row.
"""

import json
from pathlib import Path

import yaml

from prismatic.review_factory import metrics_feed
from prismatic.review_factory.learn_loop import (
    LearnLoop,
    _next_spec_path,
    main as learn_main,
)

HERE = Path(__file__).resolve()
SPEC_LEARN = HERE.parent.parent / "spec" / "learn_loop_policy_v1.yaml"
SPEC_BANDS = HERE.parent.parent / "spec" / "auto_merge_bands_v1.yaml"

NOW = 1_800_000_000.0
DAY = 86400.0


def _write_policy(tmp_path, **overrides):
    data = yaml.safe_load(SPEC_LEARN.read_text(encoding="utf-8"))
    data.update(overrides)
    path = tmp_path / "learn_loop_policy_test.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _write_bands(tmp_path, **overrides):
    data = yaml.safe_load(SPEC_BANDS.read_text(encoding="utf-8"))
    data.update(overrides)
    path = tmp_path / "auto_merge_bands_test.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _loop(tmp_path, enabled=True, now=NOW):
    loop = LearnLoop(
        _write_policy(tmp_path, enabled=enabled),
        _write_bands(tmp_path),
        decision_log=tmp_path / "decisions.jsonl",
        outcome_log=tmp_path / "outcomes.jsonl",
        band_change_log=tmp_path / "band-changes.jsonl",
        audit_log=tmp_path / "learn-audit.jsonl",
        now_fn=lambda: now,
    )
    return loop


def _write_rows(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _decision(job_id, ts, decision="allowed"):
    return {
        "ts": ts,
        "ts_iso": "2026-09-22T00:00:00+00:00",
        "component": "merge_authority",
        "policy_version": "auto-v1",
        "job_id": job_id,
        "repository": "mbgulden/prismatic-engine",
        "head_sha": "abc123",
        "tier": 0,
        "decision": decision,
        "reason": "test",
        "gates": [],
        "jev_score": None,
        "merge_sha": "def456",
    }


def _clean_scenario(tmp_path, n=20, now=NOW):
    """Enabled loop with n allowed merges, all explicitly clean."""
    loop = _loop(tmp_path, enabled=True, now=now)
    jobs = [f"job-{i}" for i in range(n)]
    _write_rows(
        loop.decision_log,
        [_decision(j, now - i * DAY) for i, j in enumerate(jobs)],
    )
    _write_rows(
        loop.outcome_log,
        [{"job_id": j, "ts": now - 10, "outcome": "clean"} for j in jobs],
    )
    return loop


def _read_jsonl(path):
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _new_spec_file(tmp_path):
    cands = [
        p
        for p in tmp_path.iterdir()
        if p.name.startswith("auto_merge_bands_test")
        and p.name != "auto_merge_bands_test.yaml"
    ]
    assert len(cands) == 1, f"expected one new spec file, got {cands}"
    return cands[0]


# ── happy path ───────────────────────────────────────────────────────


def test_apply_tighten_writes_everything(tmp_path):
    loop = _loop(tmp_path)
    events = tmp_path / "events.jsonl"
    out = loop.apply_band_change(
        "human_above", 0.65, reason="tighten test", event_log=events
    )
    assert out["status"] == "ok"
    assert out["direction"] == "tighten"
    assert out["old_value"] == 0.60
    assert out["new_value"] == 0.65

    # New versioned spec filed alongside the live one; live file untouched.
    spec = _new_spec_file(tmp_path)
    assert "auto-bands-v2" in spec.read_text(encoding="utf-8")
    assert "human_above: 0.65" in spec.read_text(encoding="utf-8")
    live = yaml.safe_load((tmp_path / "auto_merge_bands_test.yaml").read_text())
    assert live["human_above"] == 0.60

    # Band-change log row in the format _recent_loosening reads.
    log_rows = _read_jsonl(loop.band_change_log)
    assert len(log_rows) == 1
    assert log_rows[0]["band_key"] == "human_above"
    assert log_rows[0]["direction"] == "tighten"
    assert log_rows[0]["old_value"] == 0.60
    assert log_rows[0]["new_value"] == 0.65

    # Metrics-feed event recorded and valid.
    events_rows = _read_jsonl(events)
    assert len(events_rows) == 1
    row = events_rows[0]
    assert row["event"] == "band_change"
    metrics_feed._validate_band_change(
        {k: row[k] for k in ("band", "old_value", "new_value", "reason")}
    )
    assert row["band"] == "human_above"
    assert row["reason"] == "tighten test"

    # Audit row emitted.
    audit = _read_jsonl(loop.audit_log)
    assert any(
        r.get("action") == "apply_band_change" and r.get("status") == "ok"
        for r in audit
    )


def test_apply_is_refused_while_disabled(tmp_path):
    loop = _loop(tmp_path, enabled=False)
    out = loop.apply_band_change("human_above", 0.65, event_log=tmp_path / "e.jsonl")
    assert out["status"] == "refused"
    assert list(tmp_path.glob("auto_merge_bands_test*")) == [
        tmp_path / "auto_merge_bands_test.yaml"
    ]
    assert not (tmp_path / "events.jsonl").exists()


# ── refusals write nothing ───────────────────────────────────────────


def _assert_nothing_written(tmp_path, loop):
    assert list(tmp_path.glob("auto_merge_bands_test*")) == [
        tmp_path / "auto_merge_bands_test.yaml"
    ]
    assert not loop.band_change_log.exists()
    assert not (tmp_path / "events.jsonl").exists()


def test_apply_refuses_unknown_key(tmp_path):
    loop = _loop(tmp_path)
    out = loop.apply_band_change("nope", 0.5, event_log=tmp_path / "events.jsonl")
    assert out["status"] == "refused"
    _assert_nothing_written(tmp_path, loop)


def test_apply_refuses_out_of_caps(tmp_path):
    loop = _loop(tmp_path)
    out = loop.apply_band_change(
        "human_above", 5.0, event_log=tmp_path / "events.jsonl"
    )
    assert out["status"] == "refused"
    assert "outside caps" in out["reason"]
    _assert_nothing_written(tmp_path, loop)


def test_apply_refuses_non_numeric(tmp_path):
    loop = _loop(tmp_path)
    out = loop.apply_band_change(
        "human_above",
        "high",
        event_log=tmp_path / "events.jsonl",  # type: ignore[arg-type]
    )
    assert out["status"] == "refused"
    _assert_nothing_written(tmp_path, loop)


def test_apply_loosen_without_evidence_refused(tmp_path):
    loop = _loop(tmp_path)  # no decisions/outcomes at all
    out = loop.apply_band_change(
        "human_above", 0.55, event_log=tmp_path / "events.jsonl"
    )
    assert out["status"] == "refused"
    _assert_nothing_written(tmp_path, loop)


def test_apply_existing_version_refuses(tmp_path):
    loop = _loop(tmp_path)
    events = tmp_path / "events.jsonl"
    first = loop.apply_band_change("human_above", 0.65, event_log=events)
    assert first["status"] == "ok"
    second = loop.apply_band_change("human_above", 0.70, event_log=events)
    assert second["status"] == "refused"
    assert "already exists" in second["reason"]
    # Only the first change's event was recorded.
    assert len(_read_jsonl(events)) == 1


# ── the Michael gate ─────────────────────────────────────────────────


def test_over_budget_loosen_requires_michael(tmp_path):
    loop = _clean_scenario(tmp_path, n=20)
    # Pre-fill the weekly loosen budget: 0.04 of the 0.05 budget used.
    _write_rows(
        loop.band_change_log,
        [
            {
                "ts": NOW - 100,
                "band_key": "human_above",
                "direction": "loosen",
                "old_value": 0.64,
                "new_value": 0.60,
            }
        ],
    )
    events = tmp_path / "events.jsonl"
    refused = loop.apply_band_change(
        "human_above", 0.55, by="intruder", event_log=events
    )
    assert refused["status"] == "refused"
    assert "Michael" in refused["reason"]
    assert not events.exists()

    ok = loop.apply_band_change("human_above", 0.55, by="mbgulden", event_log=events)
    assert ok["status"] == "ok"
    assert ok["direction"] == "loosen"
    assert len(_read_jsonl(events)) == 1


# ── fail-closed ordering ─────────────────────────────────────────────


def test_feed_failure_writes_nothing(tmp_path):
    loop = _loop(tmp_path)
    # event_log parent is a FILE, so the feed cannot create/write the log.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir", encoding="utf-8")
    try:
        loop.apply_band_change("human_above", 0.65, event_log=blocker / "events.jsonl")
    except Exception as exc:
        assert "cannot append" in str(exc)
    else:  # pragma: no cover - the write must fail
        raise AssertionError("expected the feed write to fail")
    # Fail-closed: the spec file and the band-change log were NOT written.
    assert list(tmp_path.glob("auto_merge_bands_test*")) == [
        tmp_path / "auto_merge_bands_test.yaml"
    ]
    assert not loop.band_change_log.exists()


# ── helpers ──────────────────────────────────────────────────────────


def test_next_spec_path_versioned_name(tmp_path):
    base = tmp_path / "auto_merge_bands_v1.yaml"
    assert _next_spec_path(base, "auto-bands-v2").name == "auto_merge_bands_v2.yaml"


def test_next_spec_path_unversioned_name(tmp_path):
    base = tmp_path / "bands.yaml"
    assert _next_spec_path(base, "auto-bands-v2").name == "bands-auto-bands-v2.yaml"


# ── CLI ──────────────────────────────────────────────────────────────


def _cli_args(tmp_path, *extra):
    loop_policy = _write_policy(tmp_path, enabled=True)
    bands = _write_bands(tmp_path)
    return [
        "--policy",
        str(loop_policy),
        "--bands",
        str(bands),
        "--decision-log",
        str(tmp_path / "decisions.jsonl"),
        "--outcome-log",
        str(tmp_path / "outcomes.jsonl"),
        "--band-change-log",
        str(tmp_path / "band-changes.jsonl"),
        "--audit-log",
        str(tmp_path / "learn-audit.jsonl"),
        "apply",
        "--event-log",
        str(tmp_path / "events.jsonl"),
        *extra,
    ]


def test_cli_apply_end_to_end(tmp_path, capsys):
    rc = learn_main(
        _cli_args(
            tmp_path,
            "--band-key",
            "human_above",
            "--value",
            "0.65",
            "--reason",
            "cli tighten",
        )
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["direction"] == "tighten"
    assert _new_spec_file(tmp_path).exists()
    assert len(_read_jsonl(tmp_path / "events.jsonl")) == 1


def test_cli_apply_refused(tmp_path, capsys):
    rc = learn_main(_cli_args(tmp_path, "--band-key", "human_above", "--value", "99"))
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "refused"
