"""Tests for first-class abstain: floors, proxies, and caller-side handling."""

from prismatic.jev import advice_choice, apply_jev_advice
from prismatic.jev.backends import BackendResult
from prismatic.jev.client import DecisionClient
from prismatic.jev.config import JevConfig
from prismatic.jev.questions import (
    Choice,
    ChoiceAnswer,
    Noul,
    NoulAnswer,
    Score,
    ScoreAnswer,
    apply_abstain_floor,
)


class FakeBackend:
    backend_name = "fake"
    model = "fake-model"

    def __init__(self, answers):
        self._answers = answers
        self.calls = 0

    def decide(self, state, questions, *, call=None):
        self.calls += 1
        return BackendResult(backend="fake", answers=self._answers, latency_ms=1.0)


def _client(answers):
    return DecisionClient(backend=FakeBackend(answers), config=JevConfig())


# -- unit: apply_abstain_floor --------------------------------------------


def test_no_floor_never_abstains():
    q = Noul("u", "p")
    a = NoulAnswer(probability=0.51, confidence=0.51)
    assert apply_abstain_floor(q, a).abstained is False


def test_confidence_below_floor_abstains_with_reason():
    q = Noul("u", "p", abstain_below=0.6)
    a = NoulAnswer(probability=0.9, confidence=0.4)
    out = apply_abstain_floor(q, a)
    assert out.abstained is True
    assert "0.400" in out.abstain_reason
    assert out.probability == 0.9  # raw value kept for provenance


def test_confidence_above_floor_passes():
    q = Noul("u", "p", abstain_below=0.6)
    a = NoulAnswer(probability=0.9, confidence=0.8)
    assert apply_abstain_floor(q, a).abstained is False


def test_choice_proxy_is_top_probability():
    q = Choice("v", "p", options=["A", "B"], abstain_below=0.7)
    a = ChoiceAnswer(choice="A", probabilities={"A": 0.6, "B": 0.4})
    out = apply_abstain_floor(q, a)
    assert out.abstained is True  # 0.6 < 0.7
    assert "uncertainty proxy" in out.abstain_reason


def test_choice_proxy_passes_when_decisive():
    q = Choice("v", "p", options=["A", "B"], abstain_below=0.5)
    a = ChoiceAnswer(choice="A", probabilities={"A": 0.6, "B": 0.4})
    assert apply_abstain_floor(q, a).abstained is False


def test_noul_proxy_is_distance_from_half():
    q = Noul("u", "p", abstain_below=0.5)
    assert apply_abstain_floor(q, NoulAnswer(probability=0.55)).abstained is True
    assert apply_abstain_floor(q, NoulAnswer(probability=0.9)).abstained is False


def test_score_without_confidence_never_abstains():
    q = Score("r", "p", abstain_below=0.9)
    out = apply_abstain_floor(q, ScoreAnswer(score=0.5))
    assert out.abstained is False  # no proxy for scores


def test_score_with_low_confidence_abstains():
    q = Score("r", "p", abstain_below=0.9)
    out = apply_abstain_floor(q, ScoreAnswer(score=0.5, confidence=0.2))
    assert out.abstained is True


def test_already_abstained_stays():
    q = Noul("u", "p", abstain_below=0.99)
    a = NoulAnswer(probability=0.9, abstained=True, abstain_reason="manual")
    out = apply_abstain_floor(q, a)
    assert out.abstain_reason == "manual"


# -- advice_choice ----------------------------------------------------------


def test_advice_choice_returns_choice():
    a = ChoiceAnswer(choice="REPAIR", probabilities={"CLEAN": 0.4, "REPAIR": 0.6})
    assert advice_choice(a) == "REPAIR"


def test_advice_choice_none_when_abstained():
    a = ChoiceAnswer(
        choice="REPAIR",
        probabilities={"CLEAN": 0.4, "REPAIR": 0.6},
        abstained=True,
        abstain_reason="low confidence",
    )
    assert advice_choice(a) is None


def test_advice_choice_none_for_non_choice():
    assert advice_choice(NoulAnswer(probability=0.8)) is None
    assert advice_choice(None) is None


# -- client level ------------------------------------------------------------


def test_client_marks_abstain_and_keeps_raw_values():
    q = Choice("v", "Triage.", options=["CLEAN", "REPAIR"], abstain_below=0.8)
    ans = ChoiceAnswer(choice="REPAIR", probabilities={"CLEAN": 0.4, "REPAIR": 0.6})
    result = _client({"v": ans}).decide({"e": 1}, [q])
    assert result.abstained is True
    assert result.answers["v"].abstained is True
    assert result.answers["v"].choice == "REPAIR"


def test_abstain_does_not_escalate_deterministic_verdict():
    # Caller-side pattern: an abstain is not advice; the deterministic
    # verdict stands and the caller decides what the abstain means.
    q = Choice("v", "Triage.", options=["CLEAN", "REPAIR"], abstain_below=0.8)
    ans = ChoiceAnswer(choice="REPAIR", probabilities={"CLEAN": 0.4, "REPAIR": 0.6})
    result = _client({"v": ans}).decide({"e": 1}, [q])
    final = apply_jev_advice("CLEAN", advice_choice(result.answers["v"]))
    assert final == "CLEAN"
    assert result.abstained is True  # the abstain is visible, not hidden


def test_non_abstained_advice_still_flows():
    q = Choice("v", "Triage.", options=["CLEAN", "REPAIR"])
    ans = ChoiceAnswer(choice="REPAIR", probabilities={"CLEAN": 0.2, "REPAIR": 0.8})
    result = _client({"v": ans}).decide({"e": 1}, [q])
    assert result.abstained is False
    assert apply_jev_advice("CLEAN", advice_choice(result.answers["v"])) == "ESCALATE"


def test_abstain_survives_in_audit_dict():
    q = Noul("u", "p", abstain_below=0.9)
    result = _client({"u": NoulAnswer(probability=0.7)}).decide({"e": 1}, [q])
    audit = result.to_audit_dict()
    assert audit["abstained"] is True
    assert audit["answers"]["u"]["abstained"] is True
