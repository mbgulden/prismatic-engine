"""Offline eval runner: ``python -m prismatic.jev.eval.run``.

Loads recorded fixtures, runs the graders, prints a pass/fail report, and
exits non-zero on any failure. No network, no side effects, no model calls —
safe to run in CI.

Recalibration artifacts are computed and *reported only*; they are not
applied to the primitive.
"""

from __future__ import annotations

import json
import os
import sys

from . import graders, recalibrate

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name: str) -> list[dict]:
    with open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as fh:
        return json.load(fh)


def main() -> int:
    failures = 0

    schema = graders.grade_schema_conformance(_load("schema_cases.json"))
    print(f"[schema-conformance] {schema['passed']}/{schema['total']} passed")
    for f in schema["failures"]:
        print(f"  FAIL {f['case']}: expected {f['expected']}, got {f['got']}")
        failures += 1

    nodown = graders.grade_no_downgrade(_load("no_downgrade_cases.json"))
    print(f"[no-downgrade] {nodown['passed']}/{nodown['total']} passed")
    for f in nodown["failures"]:
        print(
            f"  FAIL {f['case']}: det={f['deterministic']} jev={f['jev_choice']} "
            f"expected {f['expected']}, got {f['got']}"
        )
        failures += 1

    calib = json.load(
        open(os.path.join(FIXTURES_DIR, "calibration_cases.json"), encoding="utf-8")
    )
    metrics = graders.calibration_metrics(
        calib["predictions"], calib["outcomes"], bins=calib.get("bins", 10)
    )
    print(
        f"[calibration] brier={metrics['brier']:.4f} ece={metrics['ece']:.4f} "
        f"n={metrics['n']} bins={metrics['bins']} (lower is better; "
        "brier 0.25 = always-0.5 baseline)"
    )

    a, b = recalibrate.platt_scaling(calib["predictions"], calib["outcomes"])
    t = recalibrate.temperature_scaling(calib["predictions"], calib["outcomes"])
    platt_metrics = graders.calibration_metrics(
        [recalibrate.apply_platt(p, a, b) for p in calib["predictions"]],
        calib["outcomes"],
    )
    temp_metrics = graders.calibration_metrics(
        [recalibrate.apply_temperature(p, t) for p in calib["predictions"]],
        calib["outcomes"],
    )
    print(
        f"[recalibration, report-only] platt a={a:.3f} b={b:.3f} "
        f"-> brier={platt_metrics['brier']:.4f} ece={platt_metrics['ece']:.4f}; "
        f"temperature T={t:.3f} -> brier={temp_metrics['brier']:.4f} "
        f"ece={temp_metrics['ece']:.4f} "
        "(NOT applied to the primitive)"
    )

    print("EVAL FAIL" if failures else "EVAL PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
