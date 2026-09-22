"""Recalibration helpers for the offline eval harness ONLY.

Platt scaling (logistic on logits) and temperature scaling are computed and
*reported* here. They never alter the primitive's output: any recalibration
is applied by the caller, after review, in their own code. The primitive
stays exactly calibrated-or-not as its questions ask it to be.
"""

from __future__ import annotations

import math

from .graders import clamp_prob, logit, sigmoid


def _log_loss(probs: list[float], outcomes: list[int]) -> float:
    return -sum(
        y * math.log(clamp_prob(p)) + (1 - y) * math.log(clamp_prob(1.0 - p))
        for p, y in zip(probs, outcomes)
    ) / len(probs)


def platt_scaling(
    probs: list[float], outcomes: list[int], iters: int = 300, lr: float = 0.5
) -> tuple[float, float]:
    """Fit logistic-regression parameters ``(a, b)`` on logit space.

    Calibrated probability: ``sigmoid(a * logit(p) + b)``.
    Simple batch gradient descent on log-loss; deterministic, stdlib-only.
    """
    if len(probs) != len(outcomes) or not probs:
        raise ValueError("probs and outcomes must be non-empty and equal length")
    zs = [logit(p) for p in probs]
    a, b = 1.0, 0.0
    for _ in range(iters):
        ga = gb = 0.0
        n = len(zs)
        for z, y in zip(zs, outcomes):
            q = sigmoid(a * z + b)
            err = q - y
            ga += err * z / n
            gb += err / n
        a -= lr * ga
        b -= lr * gb
    return a, b


def apply_platt(p: float, a: float, b: float) -> float:
    """Apply fitted Platt parameters to a raw probability."""
    return sigmoid(a * logit(p) + b)


def temperature_scaling(probs: list[float], outcomes: list[int]) -> float:
    """Find the temperature ``T > 0`` minimizing log-loss (coarse-to-fine).

    Calibrated probability for the binary case:
    ``p^(1/T) / (p^(1/T) + (1-p)^(1/T))``. T < 1 sharpens, T > 1 softens.
    """
    if len(probs) != len(outcomes) or not probs:
        raise ValueError("probs and outcomes must be non-empty and equal length")

    def loss(t: float) -> float:
        return _log_loss([apply_temperature(p, t) for p in probs], outcomes)

    lo, hi = 0.05, 10.0
    for _ in range(40):
        m1 = lo + (hi - lo) / 3.0
        m2 = hi - (hi - lo) / 3.0
        if loss(m1) < loss(m2):
            hi = m2
        else:
            lo = m1
    return (lo + hi) / 2.0


def apply_temperature(p: float, t: float) -> float:
    """Apply a temperature to a raw probability (binary formulation)."""
    p = clamp_prob(p)
    if t <= 0:
        raise ValueError("temperature must be > 0")
    pw = p ** (1.0 / t)
    qw = (1.0 - p) ** (1.0 / t)
    return pw / (pw + qw)
