"""Tests for prismatic.jev question wire shapes and strict answer validation."""

import unittest

from prismatic.jev.errors import SchemaViolationError
from prismatic.jev.questions import Choice, Noul, Score


class TestQuestionWire(unittest.TestCase):
    def test_noul_wire_shape(self):
        q = Noul("urgent", "Is this urgent?")
        self.assertEqual(
            q.to_wire(),
            {"type": "noul", "question": "Is this urgent?", "wire_version": "noul.v1"},
        )

    def test_choice_wire_shape(self):
        q = Choice("verdict", "Triage verdict.", options=["CLEAN", "REPAIR"])
        self.assertEqual(
            q.to_wire(),
            {
                "type": "choice",
                "question": "Triage verdict.",
                "options": ["CLEAN", "REPAIR"],
                "wire_version": "choice.v1",
            },
        )

    def test_score_wire_shape(self):
        q = Score("risk", "Risk 0-1.")
        self.assertEqual(
            q.to_wire(),
            {
                "type": "score",
                "question": "Risk 0-1.",
                "min": 0.0,
                "max": 1.0,
                "wire_version": "score.v1",
            },
        )

    def test_choice_requires_unique_nonempty_options(self):
        with self.assertRaises(ValueError):
            Choice("v", "p", options=[])
        with self.assertRaises(ValueError):
            Choice("v", "p", options=["A", "A"])

    def test_score_requires_min_lt_max(self):
        with self.assertRaises(ValueError):
            Score("r", "p", min=1.0, max=1.0)


class TestWirePromptRequired(unittest.TestCase):
    def test_empty_prompt_fails_closed(self):
        from prismatic.jev.errors import DecisionError

        with self.assertRaises(DecisionError):
            Noul("u", "").to_wire()


class TestNoulParsing(unittest.TestCase):
    def setUp(self):
        self.q = Noul("urgent", "Is this urgent?")

    def test_valid(self):
        ans = self.q.parse_answer({"probability": 0.87, "confidence": 0.9})
        self.assertAlmostEqual(ans.probability, 0.87)
        self.assertAlmostEqual(ans.confidence, 0.9)

    def test_confidence_optional(self):
        ans = self.q.parse_answer({"probability": 0.5})
        self.assertIsNone(ans.confidence)

    def test_missing_probability(self):
        with self.assertRaises(SchemaViolationError):
            self.q.parse_answer({"confidence": 0.9})

    def test_probability_out_of_range(self):
        for bad in (-0.1, 1.5, "high", True, None):
            with self.assertRaises(SchemaViolationError, msg=f"value={bad!r}"):
                self.q.parse_answer({"probability": bad})

    def test_non_object_payload(self):
        with self.assertRaises(SchemaViolationError):
            self.q.parse_answer(0.87)


class TestChoiceParsing(unittest.TestCase):
    def setUp(self):
        self.q = Choice(
            "verdict", "Triage verdict.", options=["CLEAN", "REPAIR", "REJECT"]
        )

    def _payload(self, **kw):
        base = {
            "choice": "REPAIR",
            "probabilities": {"CLEAN": 0.05, "REPAIR": 0.88, "REJECT": 0.07},
            "confidence": 0.9,
        }
        base.update(kw)
        return base

    def test_valid(self):
        ans = self.q.parse_answer(self._payload())
        self.assertEqual(ans.choice, "REPAIR")
        self.assertAlmostEqual(ans.probabilities["REPAIR"], 0.88)

    def test_choice_not_in_options(self):
        with self.assertRaises(SchemaViolationError):
            self.q.parse_answer(self._payload(choice="MAYBE"))

    def test_probabilities_missing_option(self):
        with self.assertRaises(SchemaViolationError):
            self.q.parse_answer(
                self._payload(probabilities={"CLEAN": 0.5, "REPAIR": 0.5})
            )

    def test_probabilities_extra_option(self):
        with self.assertRaises(SchemaViolationError):
            self.q.parse_answer(
                self._payload(
                    probabilities={
                        "CLEAN": 0.05,
                        "REPAIR": 0.88,
                        "REJECT": 0.05,
                        "X": 0.02,
                    }
                )
            )

    def test_probabilities_not_summing(self):
        with self.assertRaises(SchemaViolationError):
            self.q.parse_answer(
                self._payload(
                    probabilities={"CLEAN": 0.5, "REPAIR": 0.5, "REJECT": 0.5}
                )
            )

    def test_choice_not_argmax(self):
        # Contradictory data: the named choice isn't the top probability.
        with self.assertRaises(SchemaViolationError):
            self.q.parse_answer(
                self._payload(
                    choice="CLEAN",
                    probabilities={"CLEAN": 0.05, "REPAIR": 0.88, "REJECT": 0.07},
                )
            )

    def test_probability_value_out_of_range(self):
        with self.assertRaises(SchemaViolationError):
            self.q.parse_answer(
                self._payload(
                    probabilities={"CLEAN": 1.5, "REPAIR": -0.4, "REJECT": -0.1}
                )
            )

    def test_missing_fields(self):
        with self.assertRaises(SchemaViolationError):
            self.q.parse_answer({"choice": "REPAIR"})


class TestScoreParsing(unittest.TestCase):
    def setUp(self):
        self.q = Score("risk", "Risk 0-1.")

    def test_valid(self):
        ans = self.q.parse_answer({"score": 0.31})
        self.assertAlmostEqual(ans.score, 0.31)

    def test_out_of_range(self):
        for bad in (-0.01, 1.01, "low"):
            with self.assertRaises(SchemaViolationError, msg=f"value={bad!r}"):
                self.q.parse_answer({"score": bad})

    def test_custom_scale(self):
        q = Score("grade", "Grade 1-10.", min=1.0, max=10.0)
        self.assertAlmostEqual(q.parse_answer({"score": 7.5}).score, 7.5)
        with self.assertRaises(SchemaViolationError):
            q.parse_answer({"score": 0.5})

    def test_missing_score(self):
        with self.assertRaises(SchemaViolationError):
            self.q.parse_answer({})


class TestDefaultAnswers(unittest.TestCase):
    def test_choice_default(self):
        q = Choice("v", "p", options=["CLEAN", "REPAIR"])
        ans = q.default_answer("CLEAN")
        self.assertEqual(ans.choice, "CLEAN")
        self.assertEqual(ans.probabilities, {"CLEAN": 1.0, "REPAIR": 0.0})
        self.assertEqual(ans.confidence, 1.0)

    def test_choice_default_invalid(self):
        q = Choice("v", "p", options=["CLEAN", "REPAIR"])
        with self.assertRaises(SchemaViolationError):
            q.default_answer("MAYBE")

    def test_noul_default(self):
        self.assertAlmostEqual(Noul("u", "p").default_answer(0.2).probability, 0.2)

    def test_score_default(self):
        self.assertAlmostEqual(Score("r", "p").default_answer(0.9).score, 0.9)
        with self.assertRaises(SchemaViolationError):
            Score("r", "p").default_answer(2.0)


if __name__ == "__main__":
    unittest.main()
