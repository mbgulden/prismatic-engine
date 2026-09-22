"""Tests for the offline eval harness: graders, fixtures, recalibration."""

import json
import os

import pytest

from prismatic.jev.eval import graders, recalibrate
from prismatic.jev.eval.run import FIXTURES_DIR, main


def _load(name):
    with open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as fh:
        return json.load(fh)


def test_fixture_files_exist():
    for name in (
        "schema_cases.json",
        "no_downgrade_cases.json",
        "calibration_cases.json",
    ):
        assert os.path.exists(os.path.join(FIXTURES_DIR, name))


def test_schema_conformance_fixture_passes():
    result = graders.grade_schema_conformance(_load("schema_cases.json"))
    assert result["failed"] == 0, result["failures"]
    assert result["passed"] == result["total"] > 0


def test_no_downgrade_fixture_passes():
    result = graders.grade_no_downgrade(_load("no_downgrade_cases.json"))
    assert result["failed"] == 0, result["failures"]
    assert result["passed"] == result["total"] > 0


def test_calibration_metrics_perfect_predictions():
    m = graders.calibration_metrics([1.0, 0.0, 1.0, 0.0], [1, 0, 1, 0])
    assert m["brier"] == pytest.approx(0.0)
    assert m["ece"] == pytest.approx(0.0)


def test_calibration_metrics_baseline():
    m = graders.calibration_metrics([0.5, 0.5, 0.5, 0.5], [1, 0, 1, 0])
    assert m["brier"] == pytest.approx(0.25)
    assert m["ece"] == pytest.approx(0.0)


def test_calibration_metrics_rejects_bad_input():
    with pytest.raises(ValueError):
        graders.calibration_metrics([0.5], [1, 0])
    with pytest.raises(ValueError):
        graders.calibration_metrics([], [])


def test_platt_scaling_runs_and_stays_in_range():
    probs = [0.9, 0.8, 0.2, 0.1, 0.7, 0.3]
    outcomes = [1, 1, 0, 0, 0, 1]
    a, b = recalibrate.platt_scaling(probs, outcomes)
    assert a != 0.0 or b != 0.0  # it actually fit something
    for p in probs:
        q = recalibrate.apply_platt(p, a, b)
        assert 0.0 < q < 1.0


def test_temperature_identity_at_one():
    assert recalibrate.apply_temperature(0.7, 1.0) == pytest.approx(0.7)
    assert recalibrate.apply_temperature(0.2, 1.0) == pytest.approx(0.2)


def test_temperature_sharpens_and_softens():
    assert recalibrate.apply_temperature(0.7, 0.5) > 0.7
    assert recalibrate.apply_temperature(0.7, 2.0) < 0.7


def test_temperature_scaling_finds_reasonable_t():
    probs = [0.99, 0.01, 0.99, 0.01]
    outcomes = [1, 0, 0, 1]  # overconfident and wrong half the time
    t = recalibrate.temperature_scaling(probs, outcomes)
    assert t > 1.0  # must soften


def test_temperature_rejects_nonpositive():
    with pytest.raises(ValueError):
        recalibrate.apply_temperature(0.5, 0.0)


def test_eval_runner_passes(capsys):
    assert main() == 0
    out = capsys.readouterr().out
    assert "EVAL PASS" in out
    assert "[schema-conformance]" in out
    assert "[no-downgrade]" in out
    assert "[calibration]" in out
    assert "NOT applied to the primitive" in out


def test_graders_never_touch_network(monkeypatch):
    import socket

    def _boom(*args, **kwargs):
        raise AssertionError("network touched during offline eval")

    monkeypatch.setattr(socket, "create_connection", _boom)
    assert main() == 0
