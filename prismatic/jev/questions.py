"""Typed questions (Choice / Score / Noul) and strict answer validation.

The primitive owns the question schema and validates every backend answer
against it at parse time. Wire shapes follow the public OpenRouter decisions
API examples (Sep 2026), extended with a top-level ``schema_version`` and
per-type ``wire_version`` (see :mod:`prismatic.jev.schema`).

Abstain is first-class: a question may set ``abstain_below`` (a confidence
floor). When the answer's confidence — or a documented per-type uncertainty
proxy when the backend returned no confidence — falls below the floor, the
answer is marked ``abstained`` with a reason, but the raw probabilities are
kept for provenance. The primitive never escalates on abstain; the caller
decides (human review or a stronger model).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from .errors import DecisionError, SchemaViolationError
from .prompts import PromptRef, PromptRegistry
from .schema import wire_version_for


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_probability(value: Any, where: str) -> float:
    if not _is_number(value) or not 0.0 <= value <= 1.0:
        raise SchemaViolationError(
            f"{where}: expected probability in [0, 1], got {value!r}"
        )
    return float(value)


def _check_confidence(value: Any, where: str) -> float | None:
    if value is None:
        return None
    return _check_probability(value, f"{where}.confidence")


@dataclass(frozen=True)
class ChoiceAnswer:
    """Validated answer to a Choice question."""

    choice: str
    probabilities: dict[str, float]
    confidence: float | None = None
    abstained: bool = False
    abstain_reason: str | None = None

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "type": "choice",
            "choice": self.choice,
            "probabilities": dict(self.probabilities),
            "confidence": self.confidence,
            "abstained": self.abstained,
            "abstain_reason": self.abstain_reason,
        }


@dataclass(frozen=True)
class NoulAnswer:
    """Validated answer to a Noul (yes/no probability) question."""

    probability: float
    confidence: float | None = None
    abstained: bool = False
    abstain_reason: str | None = None

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "type": "noul",
            "probability": self.probability,
            "confidence": self.confidence,
            "abstained": self.abstained,
            "abstain_reason": self.abstain_reason,
        }


@dataclass(frozen=True)
class ScoreAnswer:
    """Validated answer to a Score (position on a scale) question."""

    score: float
    confidence: float | None = None
    abstained: bool = False
    abstain_reason: str | None = None

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "type": "score",
            "score": self.score,
            "confidence": self.confidence,
            "abstained": self.abstained,
            "abstain_reason": self.abstain_reason,
        }


Answer = ChoiceAnswer | NoulAnswer | ScoreAnswer


def _uncertainty_proxy(answer: Answer) -> float | None:
    """Confidence proxy when the backend returned no explicit confidence.

    - Choice: the top probability (decisiveness of the pick).
    - Noul: ``2*|p-0.5`` — 0 at maximum uncertainty, 1 at certainty.
    - Score: no proxy — a score is a position, not a belief; without an
      explicit confidence there is nothing to judge against a floor.
    """
    if isinstance(answer, ChoiceAnswer):
        return max(answer.probabilities.values())
    if isinstance(answer, NoulAnswer):
        return 2.0 * abs(answer.probability - 0.5)
    return None


def apply_abstain_floor(question: "Question", answer: Answer) -> Answer:
    """Mark the answer abstained when confidence is below the floor.

    The raw probabilities are kept (provenance); ``abstained`` + reason are
    set. Never raises, never escalates — the caller decides what an abstain
    means.
    """
    floor = question.abstain_below
    if floor is None or answer.abstained:
        return answer
    confidence = answer.confidence
    source = "confidence"
    if confidence is None:
        confidence = _uncertainty_proxy(answer)
        source = "uncertainty proxy"
    if confidence is None:
        return answer  # nothing to judge against the floor
    if confidence < floor:
        return replace(
            answer,
            abstained=True,
            abstain_reason=(f"{source} {confidence:.3f} below abstain floor {floor}"),
        )
    return answer


@dataclass(frozen=True)
class Question:
    """Base typed question. Subclasses define kind, wire shape, and parsing."""

    name: str
    prompt: str = ""
    kind: str = field(init=False)
    abstain_below: float | None = None
    prompt_id: str | None = None

    def to_wire(self, *, prompt_text: str | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def parse_answer(self, payload: Any) -> Answer:
        raise NotImplementedError

    def default_answer(self, value: Any) -> Answer:
        """Build a deterministic answer from a caller-supplied default value."""
        raise NotImplementedError

    def effective_prompt(
        self, registry: PromptRegistry
    ) -> tuple[str, PromptRef | None]:
        """Resolve the prompt text, via the registry when ``prompt_id`` set.

        Fail-closed: unknown prompt ids and empty prompts raise
        ``DecisionError`` — a question with no prompt is a configuration bug.
        """
        if self.prompt_id:
            ref = registry.resolve(self.prompt_id)
            return ref.text, ref
        if not self.prompt:
            raise DecisionError(
                f"question {self.name!r} has neither prompt text nor prompt_id"
            )
        return self.prompt, None

    def _wire_prompt(self, prompt_text: str | None) -> str:
        """Prompt text for the wire; fails closed when empty."""
        text = prompt_text if prompt_text is not None else self.prompt
        if not text:
            raise DecisionError(f"question {self.name!r} has no prompt text to send")
        return text


@dataclass(frozen=True)
class Noul(Question):
    """Yes/no probability question."""

    kind: str = field(default="noul", init=False)

    def to_wire(self, *, prompt_text: str | None = None) -> dict[str, Any]:
        return {
            "wire_version": wire_version_for(self.kind),
            "type": "noul",
            "question": self._wire_prompt(prompt_text),
        }

    def parse_answer(self, payload: Any) -> NoulAnswer:
        where = f"answer[{self.name!r}]"
        if not isinstance(payload, dict):
            raise SchemaViolationError(
                f"{where}: expected object, got {type(payload).__name__}"
            )
        if "probability" not in payload:
            raise SchemaViolationError(f"{where}: missing 'probability'")
        return NoulAnswer(
            probability=_check_probability(payload["probability"], where),
            confidence=_check_confidence(payload.get("confidence"), where),
        )

    def default_answer(self, value: Any) -> NoulAnswer:
        return NoulAnswer(
            probability=_check_probability(value, f"default[{self.name!r}]"),
            confidence=1.0,
        )


@dataclass(frozen=True)
class Choice(Question):
    """Pick-one-from-options question."""

    options: tuple[str, ...] = ()
    kind: str = field(default="choice", init=False)

    def __post_init__(self) -> None:
        if not self.options or len(set(self.options)) != len(self.options):
            raise ValueError("Choice requires a non-empty list of unique options")
        object.__setattr__(self, "options", tuple(self.options))

    def to_wire(self, *, prompt_text: str | None = None) -> dict[str, Any]:
        return {
            "wire_version": wire_version_for(self.kind),
            "type": "choice",
            "question": self._wire_prompt(prompt_text),
            "options": list(self.options),
        }

    def parse_answer(self, payload: Any) -> ChoiceAnswer:
        where = f"answer[{self.name!r}]"
        if not isinstance(payload, dict):
            raise SchemaViolationError(
                f"{where}: expected object, got {type(payload).__name__}"
            )
        if "choice" not in payload or "probabilities" not in payload:
            raise SchemaViolationError(f"{where}: missing 'choice' or 'probabilities'")
        choice = payload["choice"]
        probs = payload["probabilities"]
        if choice not in self.options:
            raise SchemaViolationError(
                f"{where}: choice {choice!r} not in options {list(self.options)!r}"
            )
        if not isinstance(probs, dict) or set(probs.keys()) != set(self.options):
            raise SchemaViolationError(
                f"{where}: 'probabilities' must cover exactly the options {list(self.options)!r}"
            )
        clean: dict[str, float] = {}
        for opt in self.options:
            clean[opt] = _check_probability(
                probs[opt], f"{where}.probabilities[{opt!r}]"
            )
        total = sum(clean.values())
        if abs(total - 1.0) > 0.01:
            raise SchemaViolationError(
                f"{where}: probabilities sum to {total}, expected ~1.0"
            )
        best = max(clean.values())
        if clean[choice] < best - 1e-9:
            raise SchemaViolationError(
                f"{where}: choice {choice!r} is not the highest-probability option"
            )
        return ChoiceAnswer(
            choice=choice,
            probabilities=clean,
            confidence=_check_confidence(payload.get("confidence"), where),
        )

    def default_answer(self, value: Any) -> ChoiceAnswer:
        where = f"default[{self.name!r}]"
        if value not in self.options:
            raise SchemaViolationError(
                f"{where}: {value!r} not in options {list(self.options)!r}"
            )
        return ChoiceAnswer(
            choice=value,
            probabilities={opt: (1.0 if opt == value else 0.0) for opt in self.options},
            confidence=1.0,
        )


@dataclass(frozen=True)
class Score(Question):
    """Position on a scale, default 0 (safe) to 1 (dangerous)."""

    min: float = 0.0
    max: float = 1.0
    kind: str = field(default="score", init=False)

    def __post_init__(self) -> None:
        if not _is_number(self.min) or not _is_number(self.max) or self.min >= self.max:
            raise ValueError("Score requires min < max")

    def to_wire(self, *, prompt_text: str | None = None) -> dict[str, Any]:
        return {
            "wire_version": wire_version_for(self.kind),
            "type": "score",
            "question": self._wire_prompt(prompt_text),
            "min": self.min,
            "max": self.max,
        }

    def parse_answer(self, payload: Any) -> ScoreAnswer:
        where = f"answer[{self.name!r}]"
        if not isinstance(payload, dict):
            raise SchemaViolationError(
                f"{where}: expected object, got {type(payload).__name__}"
            )
        if "score" not in payload:
            raise SchemaViolationError(f"{where}: missing 'score'")
        score = payload["score"]
        if not _is_number(score) or not self.min <= score <= self.max:
            raise SchemaViolationError(
                f"{where}: expected score in [{self.min}, {self.max}], got {score!r}"
            )
        return ScoreAnswer(
            score=float(score),
            confidence=_check_confidence(payload.get("confidence"), where),
        )

    def default_answer(self, value: Any) -> ScoreAnswer:
        where = f"default[{self.name!r}]"
        if not _is_number(value) or not self.min <= value <= self.max:
            raise SchemaViolationError(
                f"{where}: expected score in [{self.min}, {self.max}], got {value!r}"
            )
        return ScoreAnswer(score=float(value), confidence=1.0)
