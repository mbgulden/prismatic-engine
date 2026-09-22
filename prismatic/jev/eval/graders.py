"""Offline eval graders: pure functions, deterministic, no network.

These graders run against recorded fixtures (``fixtures/*.json``) or live
in-memory cases. They never touch the network and never alter the primitive's
output — recalibration artifacts are reported beside the numbers, applied
only by the caller. ECE/Brier belong here, in the offline harness.
"""

from __future__ import annotations

import math
from typing import Any

from ..errors import SchemaViolationError
from ..gates import apply_jev_advice
from ..questions import Choice, Noul, Question, Score


def make_question(spec: dict[str, Any]) -> Question:
    """Build a question from a fixture spec."""
    kind = spec.get("kind")
    name = spec.get("name", "q")
    prompt = spec.get("prompt", "")
    if kind == "noul":
        return Noul(name, prompt, abstain_below=spec.get("abstain_below"))
    if kind == "choice":
        return Choice(name, prompt, options=tuple(spec.get("options", [])))
    if kind == "score":
        return Score(name, prompt, min=spec.get("min", 0.0), max=spec.get("max", 1.0))
    raise ValueError(f"unknown question kind in fixture: {kind!r}")


def grade_schema_conformance(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Parse-fixture cases: expect_ok true → parse succeeds, false → raises."""
    passed = 0
    failures: list[dict[str, Any]] = []
    for case in cases:
        question = make_question(case["question"])
        expect_ok = bool(case["expect_ok"])
        try:
            question.parse_answer(case["payload"])
            ok = True
        except SchemaViolationError:
            ok = False
        if ok == expect_ok:
            passed += 1
        else:
            failures.append(
                {
                    "case": case.get("name", "?"),
                    "expected": "parse-ok" if expect_ok else "schema-violation",
                    "got": "parse-ok" if ok else "schema-violation",
                }
            )
    return {
        "total": len(cases),
        "passed": passed,
        "failed": len(cases) - passed,
        "failures": failures,
    }


def grade_no_downgrade(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Adversarial combine cases: deterministic REPAIR/REJECT must never drop."""
    passed = 0
    failures: list[dict[str, Any]] = []
    for case in cases:
        got = apply_jev_advice(case["deterministic"], case.get("jev_choice"))
        if got == case["expected"]:
            passed += 1
        else:
            failures.append(
                {
                    "case": case.get("name", "?"),
                    "deterministic": case["deterministic"],
                    "jev_choice": case.get("jev_choice"),
                    "expected": case["expected"],
                    "got": got,
                }
            )
    return {
        "total": len(cases),
        "passed": passed,
        "failed": len(cases) - passed,
        "failures": failures,
    }


def calibration_metrics(
    predictions: list[float], outcomes: list[int], bins: int = 10
) -> dict[str, float]:
    """Brier score and expected calibration error (ECE) for probabilities.

    Lower is better for both. Brier of 0.25 is the always-p=0.5 baseline.
    """
    if len(predictions) != len(outcomes) or not predictions:
        raise ValueError("predictions and outcomes must be non-empty and equal length")
    n = len(predictions)
    brier = sum((p - y) ** 2 for p, y in zip(predictions, outcomes)) / n
    bucket_edges = [i / bins for i in range(bins + 1)]
    ece = 0.0
    for i in range(bins):
        lo, hi = bucket_edges[i], bucket_edges[i + 1]
        bucket = [
            (p, y)
            for p, y in zip(predictions, outcomes)
            if (lo <= p < hi) or (i == bins - 1 and p == hi)
        ]
        if not bucket:
            continue
        acc = sum(y for _, y in bucket) / len(bucket)
        conf = sum(p for p, _ in bucket) / len(bucket)
        ece += abs(acc - conf) * (len(bucket) / n)
    return {"brier": brier, "ece": ece, "n": n, "bins": bins}


def clamp_prob(p: float) -> float:
    return min(1.0 - 1e-6, max(1e-6, p))


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def logit(p: float) -> float:
    p = clamp_prob(p)
    return math.log(p / (1.0 - p))
