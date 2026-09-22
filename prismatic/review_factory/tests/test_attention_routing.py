"""Tests for attention routing (Jev #29) — shadow mode, advisory only.

Every test asserts a safety property the router claims:
- deterministic scoring first; Jev refines upward only (never lowers);
- deterministic high short-circuits Jev;
- gates default off and fail closed;
- advisory only: no blocking API usage, reports marked shadow;
- config is versioned and disciplined;
- exactly one audit signal per score_pr().
"""

import json
from pathlib import Path

import pytest

from prismatic.jev import DecisionClient
from prismatic.jev.backends import BackendResult
from prismatic.jev.errors import DecisionError
from prismatic.jev.questions import NoulAnswer, ScoreAnswer
from prismatic.review_factory import attention_routing
from prismatic.review_factory.attention_routing import (
    RISK_BAND_HIGH,
    RISK_BAND_LOW,
    RISK_BAND_MEDIUM,
    AttentionRouter,
    ChangedFile,
    PRInput,
)

SPEC = (
    Path(__file__).resolve().parent.parent / "spec" / "attention_risk_weights_v1.yaml"
)

MASTER_ENV = "SWARMJEV_ENABLED"
SITE_ENV = "SWARMJEV_CALLSITE_ATTENTION_ROUTING_ENABLED"


# ── fixtures & doubles ────────────────────────────────────────────────


@pytest.fixture
def clean_jev_env(monkeypatch):
    for var in (MASTER_ENV, SITE_ENV):
        monkeypatch.delenv(var, raising=False)


def gate_on(monkeypatch):
    monkeypatch.setenv(MASTER_ENV, "1")
    monkeypatch.setenv(SITE_ENV, "1")


class StubBackend:
    """Test double for the Jev backend: scripted risk score, call counting."""

    backend_name = "stub"

    def __init__(self, *, score=None, error=None):
        self._score = score
        self._error = error
        self.calls = 0

    def decide(self, state, questions):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return BackendResult(
            backend=self.backend_name,
            answers={
                "review_risk": ScoreAnswer(score=self._score, confidence=0.9),
                "confident": NoulAnswer(probability=0.9, confidence=0.9),
            },
            latency_ms=2.0,
        )


def make_router(tmp_path, monkeypatch, *, backend=None, weights_path=None):
    client = DecisionClient(backend=backend) if backend is not None else None
    return AttentionRouter(
        weights_path=str(weights_path or SPEC),
        audit_path=str(tmp_path / "audit.jsonl"),
        client=client,
    )


def pr_input(files, **kwargs):
    return PRInput(
        files=tuple(ChangedFile(path=p, additions=a, deletions=d) for p, a, d in files),
        **kwargs,
    )


def low_risk_pr():
    return pr_input([("docs/getting-started.md", 20, 2)], author_login="alice")


def high_risk_pr():
    return pr_input(
        [
            (".github/workflows/deploy.yml", 500, 10),
            ("prismatic/review_factory/spec/policy_file_v1.yaml", 5, 0),
        ],
        author_login="mallory",
        prior_failed_runs=3,
    )


def audit_rows(tmp_path):
    path = tmp_path / "audit.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# ── deterministic scoring ─────────────────────────────────────────────


def test_risky_path_raises_band(tmp_path, monkeypatch, clean_jev_env):
    router = make_router(tmp_path, monkeypatch)
    risky = router.score_pr(
        pr_input([(".github/workflows/test.yml", 10, 2)]),
        pr_number=1,
        head_sha="abc",
    )
    assert risky.deterministic_band in (RISK_BAND_MEDIUM, RISK_BAND_HIGH)
    assert risky.deterministic_score >= 30.0

    control = router.score_pr(low_risk_pr(), pr_number=2, head_sha="def")
    assert control.deterministic_band == RISK_BAND_LOW


def test_deploy_receiver_path_flagged(tmp_path, monkeypatch, clean_jev_env):
    router = make_router(tmp_path, monkeypatch)
    report = router.score_pr(
        pr_input([("prismatic/deploy/receiver.py", 40, 5)]), pr_number=3, head_sha="ghi"
    )
    assert report.deterministic_band in (RISK_BAND_MEDIUM, RISK_BAND_HIGH)
    hit_paths = [
        path
        for _, path in router.extract_features(
            pr_input([("prismatic/deploy/receiver.py", 40, 5)])
        ).risky_path_hits
    ]
    assert "prismatic/deploy/receiver.py" in hit_paths


def test_secret_path_pattern_flagged(tmp_path, monkeypatch, clean_jev_env):
    router = make_router(tmp_path, monkeypatch)
    report = router.score_pr(
        pr_input([("config/backup_token_old.txt", 5, 0)]), pr_number=4, head_sha="jkl"
    )
    assert report.deterministic_band in (RISK_BAND_MEDIUM, RISK_BAND_HIGH)


def test_policy_file_touch_raises_score(tmp_path, monkeypatch, clean_jev_env):
    router = make_router(tmp_path, monkeypatch)
    base = pr_input([("prismatic/reviewer.py", 50, 10)])
    with_policy = pr_input(
        [
            ("prismatic/reviewer.py", 50, 10),
            ("prismatic/review_factory/spec/policy_file_v1.yaml", 3, 1),
        ]
    )
    score_base, _ = router.deterministic_score(router.extract_features(base))
    score_policy, _ = router.deterministic_score(router.extract_features(with_policy))
    # Policy-file touch adds a flat 20 on top of the second file's own points.
    assert score_policy - score_base == pytest.approx(20.0 + 1.5 + 3 * 0.02 + 1 * 0.02)


# ── Jev refinement: upward only ───────────────────────────────────────


def test_jev_never_lowers_deterministic_high(tmp_path, monkeypatch, clean_jev_env):
    gate_on(monkeypatch)
    backend = StubBackend(score=0.05)  # 5/100: Jev says "safe"
    router = make_router(tmp_path, monkeypatch, backend=backend)

    report = router.score_pr(high_risk_pr(), pr_number=10, head_sha="mno")
    assert report.deterministic_band == RISK_BAND_HIGH
    # Short-circuit: Jev is never consulted for a deterministic high,
    # so it cannot lower the band — and the report says so.
    assert backend.calls == 0
    assert report.jev_status == "not_consulted"
    assert report.combined_band == RISK_BAND_HIGH
    assert "never lowers" in report.render_markdown()


def test_jev_can_raise_low(tmp_path, monkeypatch, clean_jev_env):
    gate_on(monkeypatch)
    backend = StubBackend(score=0.85)  # 85/100: Jev says "risky"
    router = make_router(tmp_path, monkeypatch, backend=backend)

    report = router.score_pr(low_risk_pr(), pr_number=11, head_sha="pqr")
    assert report.deterministic_band == RISK_BAND_LOW
    assert report.jev_status == "advised"
    assert report.jev_band == RISK_BAND_HIGH
    assert report.combined_band == RISK_BAND_HIGH


def test_jev_low_score_cannot_lower_medium(tmp_path, monkeypatch, clean_jev_env):
    gate_on(monkeypatch)
    backend = StubBackend(score=0.10)
    router = make_router(tmp_path, monkeypatch, backend=backend)

    report = router.score_pr(
        pr_input([(".github/workflows/test.yml", 10, 2)]),
        pr_number=12,
        head_sha="stu",
    )
    assert report.deterministic_band == RISK_BAND_MEDIUM
    assert report.combined_band == RISK_BAND_MEDIUM


def test_deterministic_high_short_circuits_jev(tmp_path, monkeypatch, clean_jev_env):
    gate_on(monkeypatch)
    backend = StubBackend(error=AssertionError("backend must never be called"))
    router = make_router(tmp_path, monkeypatch, backend=backend)

    report = router.score_pr(high_risk_pr(), pr_number=13, head_sha="vwx")
    assert backend.calls == 0
    assert report.jev_status == "not_consulted"
    assert report.jev_advice is None
    assert report.combined_band == RISK_BAND_HIGH


def test_jev_error_keeps_deterministic_band(tmp_path, monkeypatch, clean_jev_env):
    gate_on(monkeypatch)
    backend = StubBackend(error=DecisionError("backend exploded"))
    router = make_router(tmp_path, monkeypatch, backend=backend)

    report = router.score_pr(low_risk_pr(), pr_number=14, head_sha="yza")
    assert report.jev_status == "errored"
    assert report.combined_band == report.deterministic_band == RISK_BAND_LOW
    rows = audit_rows(tmp_path)
    assert rows[0]["jev_status"] == "errored"


# ── gates ─────────────────────────────────────────────────────────────


def test_gate_default_off(tmp_path, clean_jev_env):
    from prismatic.jev import CallSiteGate

    assert CallSiteGate("attention-routing").allow() is False
    router = AttentionRouter(
        weights_path=str(SPEC), audit_path=str(tmp_path / "audit.jsonl")
    )
    report = router.score_pr(
        pr_input([(".github/workflows/test.yml", 10, 2)]),
        pr_number=20,
        head_sha="bcd",
    )
    assert report.jev_status == "gate_closed"
    assert report.jev_advice is None
    assert report.combined_band == report.deterministic_band
    assert "gate is closed" in report.render_markdown()


# ── advisory only ─────────────────────────────────────────────────────


def test_advisory_only_marking(tmp_path, monkeypatch, clean_jev_env):
    router = make_router(tmp_path, monkeypatch)
    report = router.score_pr(high_risk_pr(), pr_number=30, head_sha="efg")
    markdown = report.render_markdown()
    assert "shadow — advisory only, never blocking" in markdown
    assert report.advisory_only is True

    # Mechanical proof: no merge/block/policy-enforcement usage in the module.
    source = Path(attention_routing.__file__).read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert "merge" not in stripped
            assert "policy" not in stripped or "prismatic.jev" in stripped
    assert "def block" not in source
    assert "def deny" not in source
    assert "def approve" not in source
    assert "def hold" not in source
    assert "createComment" not in source
    assert "required_status" not in source


def test_top_three_factors_present(tmp_path, monkeypatch, clean_jev_env):
    router = make_router(tmp_path, monkeypatch)
    report = router.score_pr(high_risk_pr(), pr_number=31, head_sha="hij")
    assert len(report.top_factors) == 3
    points = [f.points for f in report.top_factors]
    assert points == sorted(points, reverse=True)
    markdown = report.render_markdown()
    for rank in ("1.", "2.", "3."):
        assert rank in markdown


# ── config discipline ─────────────────────────────────────────────────


def test_malformed_weights_fail_closed(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("features: [unclosed\n", encoding="utf-8")
    router = AttentionRouter(
        weights_path=str(bad), audit_path=str(tmp_path / "audit.jsonl")
    )
    assert router.config_invalid is True
    # Baseline scoring only; never fails the PR, never raises.
    report = router.score_pr(low_risk_pr(), pr_number=40, head_sha="klm")
    assert report.combined_band == RISK_BAND_LOW
    assert report.deterministic_score == 0.0


def test_missing_weights_file_empty_weights(tmp_path, monkeypatch, clean_jev_env):
    router = AttentionRouter(
        weights_path=str(tmp_path / "nope.yaml"),
        audit_path=str(tmp_path / "audit.jsonl"),
    )
    assert router.config_invalid is False
    report = router.score_pr(low_risk_pr(), pr_number=41, head_sha="nop")
    assert report.deterministic_score == 0.0
    assert report.deterministic_band == RISK_BAND_LOW

    # The Jev path is still gated (not broken by the missing file).
    gate_on(monkeypatch)
    backend = StubBackend(score=0.9)
    router2 = make_router(
        tmp_path, monkeypatch, backend=backend, weights_path=tmp_path / "nope.yaml"
    )
    report2 = router2.score_pr(low_risk_pr(), pr_number=42, head_sha="qrs")
    assert report2.jev_status == "advised"
    assert report2.combined_band == RISK_BAND_HIGH


def test_exactly_one_audit_signal_per_pr(tmp_path, monkeypatch, clean_jev_env):
    router = make_router(tmp_path, monkeypatch)
    router.score_pr(low_risk_pr(), pr_number=50, head_sha="tuv")
    router.score_pr(high_risk_pr(), pr_number=51, head_sha="wxy")
    rows = audit_rows(tmp_path)
    assert len(rows) == 2
    for row in rows:
        assert row["component"] == "attention-routing"
        assert row["weights_version"] == "attention-risk-weights-v1"
        assert row["advisory_only"] is True
    assert rows[0]["pr_number"] == 50
    assert rows[1]["pr_number"] == 51
    assert rows[1]["combined_band"] == RISK_BAND_HIGH
