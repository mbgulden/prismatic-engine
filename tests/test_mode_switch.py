import unittest
from prismatic.mode_switch import ModeSwitch, OrchestrationMode, STATES

class TestModeSwitchTransitions(unittest.TestCase):
    """
    Verifies 21 transition scenarios (3 modes x 7 states) to ensure correct
    gating of state-machine transition approvals.
    """

    def setUp(self):
        # Setup mapping of next states for the 7 canonical states
        self.next_state_map = {
            "decompose": "dispatch",
            "dispatch": "execute",
            "execute": "review",
            "review": "feedback",
            "feedback": "refine",
            "refine": "integrate",
            "integrate": "done",
        }

    # ═══════════════════════════════════════════════════════════════
    # Interactive Mode (7 scenarios)
    # ═══════════════════════════════════════════════════════════════

    def test_interactive_decompose(self):
        ms = ModeSwitch(OrchestrationMode.INTERACTIVE)
        # 1. decompose -> dispatch
        self.assertFalse(ms.request_approval("decompose", "dispatch"))
        self.assertTrue(ms.is_pending("decompose", "dispatch"))
        self.assertTrue(ms.approve_transition("decompose", "dispatch"))
        self.assertFalse(ms.is_pending("decompose", "dispatch"))

    def test_interactive_dispatch(self):
        ms = ModeSwitch(OrchestrationMode.INTERACTIVE)
        # 2. dispatch -> execute
        self.assertFalse(ms.request_approval("dispatch", "execute"))
        self.assertTrue(ms.is_pending("dispatch", "execute"))
        self.assertTrue(ms.approve_transition("dispatch", "execute"))

    def test_interactive_execute(self):
        ms = ModeSwitch(OrchestrationMode.INTERACTIVE)
        # 3. execute -> review
        self.assertFalse(ms.request_approval("execute", "review"))
        self.assertTrue(ms.is_pending("execute", "review"))
        self.assertTrue(ms.approve_transition("execute", "review"))

    def test_interactive_review(self):
        ms = ModeSwitch(OrchestrationMode.INTERACTIVE)
        # 4. review -> feedback
        self.assertFalse(ms.request_approval("review", "feedback"))
        self.assertTrue(ms.is_pending("review", "feedback"))
        self.assertTrue(ms.approve_transition("review", "feedback"))

    def test_interactive_feedback(self):
        ms = ModeSwitch(OrchestrationMode.INTERACTIVE)
        # 5. feedback -> refine
        self.assertFalse(ms.request_approval("feedback", "refine"))
        self.assertTrue(ms.is_pending("feedback", "refine"))
        self.assertTrue(ms.approve_transition("feedback", "refine"))

    def test_interactive_refine(self):
        ms = ModeSwitch(OrchestrationMode.INTERACTIVE)
        # 6. refine -> integrate
        self.assertFalse(ms.request_approval("refine", "integrate"))
        self.assertTrue(ms.is_pending("refine", "integrate"))
        self.assertTrue(ms.approve_transition("refine", "integrate"))

    def test_interactive_integrate(self):
        ms = ModeSwitch(OrchestrationMode.INTERACTIVE)
        # 7. integrate -> done
        self.assertFalse(ms.request_approval("integrate", "done"))
        self.assertTrue(ms.is_pending("integrate", "done"))
        self.assertTrue(ms.approve_transition("integrate", "done"))

    # ═══════════════════════════════════════════════════════════════
    # Collaborative Mode (7 scenarios: 4 major pause, 3 minor fire)
    # ═══════════════════════════════════════════════════════════════

    def test_collaborative_decompose(self):
        ms = ModeSwitch(OrchestrationMode.COLLABORATIVE)
        # 8. decompose -> dispatch (major: pause)
        self.assertFalse(ms.request_approval("decompose", "dispatch"))
        self.assertTrue(ms.is_pending("decompose", "dispatch"))
        self.assertTrue(ms.approve_transition("decompose", "dispatch"))

    def test_collaborative_dispatch(self):
        ms = ModeSwitch(OrchestrationMode.COLLABORATIVE)
        # 9. dispatch -> execute (major: pause)
        self.assertFalse(ms.request_approval("dispatch", "execute"))
        self.assertTrue(ms.is_pending("dispatch", "execute"))
        self.assertTrue(ms.approve_transition("dispatch", "execute"))

    def test_collaborative_execute(self):
        ms = ModeSwitch(OrchestrationMode.COLLABORATIVE)
        # 10. execute -> review (minor: auto-fire)
        self.assertTrue(ms.request_approval("execute", "review"))
        self.assertFalse(ms.is_pending("execute", "review"))

    def test_collaborative_review(self):
        ms = ModeSwitch(OrchestrationMode.COLLABORATIVE)
        # 11. review -> feedback (major: pause)
        self.assertFalse(ms.request_approval("review", "feedback"))
        self.assertTrue(ms.is_pending("review", "feedback"))
        self.assertTrue(ms.approve_transition("review", "feedback"))

    def test_collaborative_feedback(self):
        ms = ModeSwitch(OrchestrationMode.COLLABORATIVE)
        # 12. feedback -> refine (minor: auto-fire)
        self.assertTrue(ms.request_approval("feedback", "refine"))
        self.assertFalse(ms.is_pending("feedback", "refine"))

    def test_collaborative_refine(self):
        ms = ModeSwitch(OrchestrationMode.COLLABORATIVE)
        # 13. refine -> integrate (major: pause)
        self.assertFalse(ms.request_approval("refine", "integrate"))
        self.assertTrue(ms.is_pending("refine", "integrate"))
        self.assertTrue(ms.approve_transition("refine", "integrate"))

    def test_collaborative_integrate(self):
        ms = ModeSwitch(OrchestrationMode.COLLABORATIVE)
        # 14. integrate -> done (minor: auto-fire)
        self.assertTrue(ms.request_approval("integrate", "done"))
        self.assertFalse(ms.is_pending("integrate", "done"))

    # ═══════════════════════════════════════════════════════════════
    # Autonomous Mode (7 scenarios)
    # ═══════════════════════════════════════════════════════════════

    def test_autonomous_decompose(self):
        ms = ModeSwitch(OrchestrationMode.AUTONOMOUS)
        # 15. decompose -> dispatch
        self.assertTrue(ms.request_approval("decompose", "dispatch"))

    def test_autonomous_dispatch(self):
        ms = ModeSwitch(OrchestrationMode.AUTONOMOUS)
        # 16. dispatch -> execute
        self.assertTrue(ms.request_approval("dispatch", "execute"))

    def test_autonomous_execute(self):
        ms = ModeSwitch(OrchestrationMode.AUTONOMOUS)
        # 17. execute -> review
        self.assertTrue(ms.request_approval("execute", "review"))

    def test_autonomous_review(self):
        ms = ModeSwitch(OrchestrationMode.AUTONOMOUS)
        # 18. review -> feedback
        self.assertTrue(ms.request_approval("review", "feedback"))

    def test_autonomous_feedback(self):
        ms = ModeSwitch(OrchestrationMode.AUTONOMOUS)
        # 19. feedback -> refine
        self.assertTrue(ms.request_approval("feedback", "refine"))

    def test_autonomous_refine(self):
        ms = ModeSwitch(OrchestrationMode.AUTONOMOUS)
        # 20. refine -> integrate
        self.assertTrue(ms.request_approval("refine", "integrate"))

    def test_autonomous_integrate(self):
        ms = ModeSwitch(OrchestrationMode.AUTONOMOUS)
        # 21. integrate -> done
        self.assertTrue(ms.request_approval("integrate", "done"))

    # ═══════════════════════════════════════════════════════════════
    # Escalation and Callback Hook Tests (Extra)
    # ═══════════════════════════════════════════════════════════════

    def test_autonomous_escalation_pauses(self):
        """Verifies that even in Autonomous mode, an escalation pauses transition."""
        ms = ModeSwitch(OrchestrationMode.AUTONOMOUS)
        
        # Setup escalation hook tracking
        hook_called = False
        def escalation_hook(f, t, reason):
            nonlocal hook_called
            hook_called = True
            self.assertEqual(f, "execute")
            self.assertEqual(t, "review")
            self.assertEqual(reason, "Stall warning")

        ms.register_escalation_hook(escalation_hook)
        
        # Request approval with is_escalation=True
        self.assertFalse(ms.request_approval("execute", "review", is_escalation=True, reason="Stall warning"))
        self.assertTrue(hook_called)
        self.assertTrue(ms.is_pending("execute", "review"))
        self.assertTrue(ms.approve_transition("execute", "review"))
        self.assertFalse(ms.is_pending("execute", "review"))

if __name__ == "__main__":
    unittest.main()
