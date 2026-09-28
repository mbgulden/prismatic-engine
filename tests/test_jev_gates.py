"""Tests for prismatic.jev gates: the no-downgrade invariant and default-off gates."""

import unittest
from unittest.mock import patch

from prismatic.jev.errors import DecisionError
from prismatic.jev.gates import (
    VERDICT_CLEAN,
    VERDICT_ESCALATE,
    VERDICT_REJECT,
    VERDICT_REPAIR,
    CallSiteGate,
    apply_jev_advice,
)


class TestNoDowngradeInvariant(unittest.TestCase):
    """Jev may escalate but NEVER downgrades a deterministic REPAIR/REJECT."""

    def test_reject_plus_jev_clean_stays_reject(self):
        # The adversarial case: Jev is 99% sure it's clean; deterministic REJECT wins.
        self.assertEqual(apply_jev_advice(VERDICT_REJECT, "CLEAN"), VERDICT_REJECT)

    def test_reject_plus_jev_repair_stays_reject(self):
        self.assertEqual(apply_jev_advice(VERDICT_REJECT, "REPAIR"), VERDICT_REJECT)

    def test_repair_plus_jev_clean_stays_repair(self):
        self.assertEqual(apply_jev_advice(VERDICT_REPAIR, "CLEAN"), VERDICT_REPAIR)

    def test_repair_plus_jev_reject_stays_repair(self):
        # Jev cannot upgrade REPAIR to REJECT either — deterministic verdicts are final.
        self.assertEqual(apply_jev_advice(VERDICT_REPAIR, "REJECT"), VERDICT_REPAIR)

    def test_clean_plus_jev_clean_stays_clean(self):
        self.assertEqual(apply_jev_advice(VERDICT_CLEAN, "CLEAN"), VERDICT_CLEAN)

    def test_clean_plus_jev_repair_escalates(self):
        self.assertEqual(apply_jev_advice(VERDICT_CLEAN, "REPAIR"), VERDICT_ESCALATE)

    def test_clean_plus_jev_reject_escalates(self):
        self.assertEqual(apply_jev_advice(VERDICT_CLEAN, "REJECT"), VERDICT_ESCALATE)

    def test_clean_plus_jev_escalate_escalates(self):
        self.assertEqual(apply_jev_advice(VERDICT_CLEAN, "ESCALATE"), VERDICT_ESCALATE)

    def test_no_jev_advice_keeps_deterministic(self):
        self.assertEqual(apply_jev_advice(VERDICT_REJECT, None), VERDICT_REJECT)
        self.assertEqual(apply_jev_advice(VERDICT_CLEAN, None), VERDICT_CLEAN)

    def test_unknown_deterministic_verdict_fails_closed(self):
        with self.assertRaises(DecisionError):
            apply_jev_advice("MAYBE", "CLEAN")

    def test_unrecognized_jev_advice_ignored_not_invented(self):
        # Fail-closed direction: deterministic verdict stands; never invent escalation.
        self.assertEqual(apply_jev_advice(VERDICT_CLEAN, "SHRUG"), VERDICT_CLEAN)
        self.assertEqual(apply_jev_advice(VERDICT_REJECT, "SHRUG"), VERDICT_REJECT)

    def test_case_insensitive(self):
        self.assertEqual(apply_jev_advice("reject", "clean"), VERDICT_REJECT)


class TestCallSiteGate(unittest.TestCase):
    def _env(self, **kw):
        base = {"SWARMJEV_ENABLED": "0"}
        base.update(kw)
        return patch.dict("os.environ", base, clear=True)

    def test_default_off(self):
        with self._env():
            self.assertFalse(CallSiteGate("rf_triage").allow())

    def test_master_without_site_stays_closed(self):
        with self._env(SWARMJEV_ENABLED="1"):
            self.assertFalse(CallSiteGate("rf_triage").allow())

    def test_site_without_master_stays_closed(self):
        with self._env(SWARMJEV_CALLSITE_RF_TRIAGE_ENABLED="1"):
            self.assertFalse(CallSiteGate("rf_triage").allow())

    def test_both_on_opens(self):
        with self._env(
            SWARMJEV_ENABLED="true", SWARMJEV_CALLSITE_RF_TRIAGE_ENABLED="yes"
        ):
            self.assertTrue(CallSiteGate("rf_triage").allow())

    def test_gates_are_independent_per_site(self):
        with self._env(SWARMJEV_ENABLED="1", SWARMJEV_CALLSITE_RF_TRIAGE_ENABLED="1"):
            self.assertTrue(CallSiteGate("rf_triage").allow())
            self.assertFalse(CallSiteGate("canary_triage").allow())

    def test_truthy_values(self):
        for val in ("1", "true", "TRUE", "yes", "on", " On "):
            with self._env(SWARMJEV_ENABLED=val, SWARMJEV_CALLSITE_X_ENABLED=val):
                self.assertTrue(CallSiteGate("x").allow(), msg=f"value={val!r}")

    def test_falsy_values_stay_closed(self):
        for val in ("0", "false", "no", "off", "", "maybe"):
            with self._env(SWARMJEV_ENABLED=val, SWARMJEV_CALLSITE_X_ENABLED=val):
                self.assertFalse(CallSiteGate("x").allow(), msg=f"value={val!r}")

    def test_non_string_deterministic_verdict_raises_decision_error(self):
        # Contract: an unrecognized deterministic verdict is a DecisionError
        # (fail-closed), never an AttributeError from string handling.
        for bad in (123, 4.5, ["CLEAN"], ("REPAIR",), object()):
            with self.assertRaises(DecisionError, msg=f"verdict={bad!r}"):
                apply_jev_advice(bad, None)
            with self.assertRaises(DecisionError, msg=f"verdict={bad!r}"):
                apply_jev_advice(bad, "CLEAN")

    def test_site_name_normalized(self):
        gate = CallSiteGate("failure-triage #26")
        self.assertEqual(gate.site_env, "SWARMJEV_CALLSITE_FAILURE_TRIAGE__26_ENABLED")


if __name__ == "__main__":
    unittest.main()
