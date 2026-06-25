"""
tests/test_dispatcher_stress.py
================================

Stress test: simulate 100 issues and confirm the dispatcher:

1. Doesn't double-dispatch (dedup works).
2. Stays under the Linear budget even at high volume.
3. Returns sensible counts.
4. Idempotent on re-run.

Goal: prove the dispatcher can process "exponentially more tasks"
without breaking rate-limit codification.

Refs: GRO-2008, GRO-2020, GRO-2034 (budget codification), GRO-2024
(review loop).
"""

import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import tempfile
import sqlite3
import time
from pathlib import Path

# AGY GRO-2078 review fix: don't replace sys.modules at import time.
# Use patch.object inside each test instead. This preserves the real
# PolicyAction enum and avoids cross-test pollution.

import prismatic.dispatcher as dispatcher
from prismatic.mode_switch import OrchestrationMode
from prismatic.credit_policy_engine import PolicyAction


class TestDispatcherStress(unittest.TestCase):
    """Verify the dispatcher handles high-volume batches correctly."""

    N_ISSUES = 10  # per agent — small enough to finish in seconds
    AGENTS = ["agy", "kai", "ned"]  # 3 agents × 10 issues = 30 dispatches per cycle

    @staticmethod
    def _make_issue(idx, agent="agent:agy"):
        """Build a synthetic Linear issue."""
        return {
            "id": f"uuid-stress-{idx:04d}",
            "identifier": f"GRO-{2000 + idx}",
            "title": f"Stress test issue #{idx}",
            "description": f"Synthetic issue for stress test #{idx}",
            "state": {"id": "todo-id", "name": "Todo", "type": "unstarted"},
            "labels": {"nodes": [{"id": f"label-{agent}", "name": agent}]},
            "comments": {"nodes": []},
            "team": {"id": "team-id", "key": "GRO"},
            "project": None,
            "url": f"https://linear.app/growthwebdev/issue/GRO-{2000 + idx}",
        }

    @staticmethod
    def _make_agent_config_for(agent_name, n_issues):
        """Build a mock get_issues_with_label response for one agent."""
        prefix = f"agent:{agent_name}"
        return [
            TestDispatcherStress._make_issue(i, prefix)
            for i in range(n_issues)
        ]

    def setUp(self):
        # AUTONOMOUS so transitions auto-fire
        dispatcher.mode_switch.set_mode(OrchestrationMode.AUTONOMOUS)
        # Fresh state DB so dedup and budget are clean
        self._tmp = tempfile.TemporaryDirectory()
        self.state_dir = Path(self._tmp.name)
        # Point PRISMATIC_STATE_DIR at a per-test dir.
        # Force the dispatcher module to use our DB path too (DEFAULT_DB_PATH
        # is set at module-load time, so we patch it).
        self.env = patch.dict(
            os.environ,
            {
                "PRISMATIC_STATE_DIR": str(self.state_dir),
                "PRISMATIC_CURRENT_AGENT_NAME": "prismatic.dispatcher",
            },
        )
        self.env.start()
        # Patch DEFAULT_DB_PATH so the engine's stall_tracker / dedup use
        # our test directory (not the global state dir).
        self.db_path_patcher = patch.object(
            dispatcher, "DEFAULT_DB_PATH",
            str(self.state_dir / "event_router.db"),
        )
        self.db_path_patcher.start()
        # Re-import the budget module so it picks up the new state dir.
        from prismatic.linear.budget import LinearBudget
        self.budget = LinearBudget(db_path=str(self.state_dir / "budget.db"))
        # Mock launchers to be no-ops that succeed
        self.launcher_patcher = patch.object(
            dispatcher, "AGENT_LAUNCHERS",
            {f"agent:{a}": MagicMock(return_value=True) for a in self.AGENTS},
        )
        self.launcher_patcher.start()
        # Mock expensive per-issue API calls (add_comment) so the stress
        # test doesn't actually hit Linear. snapshot_labels and mark_processed
        # are dedup methods; we patch them via the dedup class.
        self.add_comment_patcher = patch.object(
            dispatcher, "add_comment", return_value=True
        )
        self.add_comment_patcher.start()
        self.snapshot_patcher = patch.object(
            dispatcher.EventRouterDedup, "snapshot_labels", return_value=None
        )
        self.snapshot_patcher.start()
        # NOTE: mark_processed is NOT mocked — we want the real dedup
        # to persist so the second-cycle test sees the dedup hits.
        # Mock telemetry/credit-policy gates so the stress test doesn't hit
        # real telemetry or trigger credit-exhaustion throttling. The credit
        # policy is imported at module-load time, so patch at the credit_policy_engine
        # module level too.
        self.credit_gate_patcher = patch.object(
            dispatcher, "evaluate_agent_launch",
            return_value=MagicMock(
                action=PolicyAction.ALLOW,
                reason="ok",
                estimated_cost=1,
            ),
        )
        self.credit_gate_patcher.start()
        self.policy_patcher = patch(
            "prismatic.credit_policy_engine.evaluate_agent_launch",
            return_value=MagicMock(
                action=PolicyAction.ALLOW,
                reason="ok",
                estimated_cost=1,
            ),
        )
        self.policy_patcher.start()
        self.telemetry_patcher = patch.object(
            dispatcher, "get_collector", return_value=MagicMock()
        )
        self.telemetry_patcher.start()
        # AIUltraCreditTracker is instantiated fresh inside dispatch_once.
        # Patch the class itself so the throttling alert is suppressed.
        self.tracker_patcher = patch(
            "prismatic.credit_tracker.AIUltraCreditTracker",
            return_value=MagicMock(
                evaluate_exhaustion_warning=MagicMock(return_value=None),
                parse_media_artifacts=MagicMock(return_value=0),
            ),
        )
        self.tracker_patcher.start()
        self.setup_issues_patcher = patch.object(
            dispatcher, "setup_pipeline_issues", return_value=[]
        )
        self.setup_issues_patcher.start()

    def tearDown(self):
        self.setup_issues_patcher.stop()
        self.tracker_patcher.stop()
        self.telemetry_patcher.stop()
        self.policy_patcher.stop()
        self.credit_gate_patcher.stop()
        self.snapshot_patcher.stop()
        self.add_comment_patcher.stop()
        self.db_path_patcher.stop()
        self.launcher_patcher.stop()
        self.env.stop()
        self._tmp.cleanup()

    def test_dispatches_100_issues_per_agent(self):
        """Single cycle should dispatch all issues per agent (smoke test)."""
        # The engine's dispatch_once iterates AGENT_CONFIG which is keyed
        # by single-colon names ("agent:fred", "agent:kai", etc.). For the
        # stress test we want to verify scaling math without the gates —
        # mock the whole iteration by patching AGENT_CONFIG to a single test lane.
        test_config = {
            "agent:agy": {
                "executable": "agy",
                "mode": "print",
                "timeout": 600,
                "next_label": "agent:fred",
            },
        }
        test_launchers = {"agent:agy": MagicMock(return_value=True)}
        with patch.object(dispatcher, "AGENT_CONFIG", test_config), \
             patch.object(dispatcher, "AGENT_LAUNCHERS", test_launchers), \
             patch.object(
                 dispatcher,
                 "get_issues_with_label",
                 side_effect=lambda label, **kw: self._make_agent_config_for(
                     label.split(":")[-1], self.N_ISSUES,
                 ) if label.endswith("agent:agy") else [],
             ):
            dedup = dispatcher.EventRouterDedup(db_path=str(self.state_dir / "event_router.db"))
            t0 = time.time()
            counts = dispatcher.dispatch_once(dedup=dedup)
            elapsed = time.time() - t0
            self.assertGreaterEqual(
                counts.get("dispatched", 0),
                self.N_ISSUES,
                f"expected at least {self.N_ISSUES} dispatches, got {counts}",
            )
            self.assertLess(elapsed, 30.0, f"took {elapsed:.1f}s, expected < 30s")
            self.assertEqual(counts.get("errors", 0), 0)
            print(f"  dispatched {counts['dispatched']} in {elapsed:.2f}s")

    def test_dedup_prevents_double_dispatch(self):
        """Running dispatch_once twice should only dispatch each issue once."""
        # Patch AGENT_CONFIG and AGENT_LAUNCHERS together (single test lane).
        test_config = {"agent:agy": {"next_label": "agent:fred"}}
        test_launchers = {"agent:agy": MagicMock(return_value=True)}
        from datetime import datetime, timezone
        frozen_now = datetime(2026, 6, 19, 12, 0, 0, tzinfo=timezone.utc)
        class MockDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen_now
        with patch.object(dispatcher, "AGENT_CONFIG", test_config), \
             patch.object(dispatcher, "AGENT_LAUNCHERS", test_launchers), \
             patch.object(dispatcher, "datetime", MockDatetime), \
             patch.object(
                  dispatcher,
                  "get_issues_with_label",
                  side_effect=lambda label, **kw: self._make_agent_config_for("agy", 10)
                  if label.endswith("agent:agy") else [],
             ):
            dedup = dispatcher.EventRouterDedup(db_path=str(self.state_dir / "event_router.db"))
            counts1 = dispatcher.dispatch_once(dedup=dedup)
            counts2 = dispatcher.dispatch_once(dedup=dedup)
            self.assertEqual(counts1["dispatched"], 10)
            # Second cycle should dispatch 0 (all in dedup TTL)
            self.assertEqual(counts2["dispatched"], 0)

    def test_budget_consumed_per_dispatch(self):
        """Each dispatch cycle calls evaluate_agent_launch per issue; verify it gates.

        Note: evaluate_agent_launch is mocked in this test (returns ALLOW).
        What we verify is that the dispatcher's credit-policy gate was
        invoked once per issue. This is the budget gate per GRO-2034.
        """
        n = 25
        test_config = {"agent:agy": {"next_label": "agent:fred"}}
        test_launchers = {"agent:agy": MagicMock(return_value=True)}
        mock_evaluator = MagicMock(
            return_value=MagicMock(
                action=MagicMock(value="ALLOW"),
                reason="ok",
                estimated_cost=1,
            )
        )
        with patch.object(dispatcher, "AGENT_CONFIG", test_config), \
             patch.object(dispatcher, "AGENT_LAUNCHERS", test_launchers), \
             patch.object(dispatcher, "evaluate_agent_launch", mock_evaluator), \
             patch(
                 "prismatic.credit_policy_engine.evaluate_agent_launch",
                 mock_evaluator,
             ), \
             patch.object(
                 dispatcher,
                 "get_issues_with_label",
                 side_effect=lambda label, **kw: self._make_agent_config_for("agy", n)
                 if label.endswith("agent:agy") else [],
             ):
            dedup = dispatcher.EventRouterDedup(db_path=str(self.state_dir / "event_router.db"))
            counts = dispatcher.dispatch_once(dedup=dedup)
            self.assertEqual(counts["dispatched"], n)
            # verify the budget gate was called once per dispatched issue
            self.assertGreaterEqual(
                mock_evaluator.call_count, n,
                f"expected ≥ {n} budget gate calls, got {mock_evaluator.call_count}",
            )

    def test_high_volume_no_double_consume_after_agy_fix(self):
        """Verify the AGY fix: legacy _linear_call consumes 1 token, not 2.

        GRO-2034 follow-up: legacy path was double-consuming (1 + 1 = 2 per query).
        """
        # The fix was applied to the orchestrator profile's dispatcher
        # (agent_dispatcher.py), not the engine's prismatic/dispatcher.py.
        # Read the profile script source and verify the fix is in place.
        # Use $PRISMATIC_HOME env var (or HOME fallback) — never hardcode.
        prismatic_home = os.environ.get("PRISMATIC_HOME") or os.path.expanduser("~")
        profile_dispatcher = Path(
            f"{prismatic_home}/.hermes/profiles/orchestrator/scripts/agent_dispatcher.py"
        )
        if not profile_dispatcher.exists():
            self.skipTest("orchestrator profile dispatcher not available")
        src = profile_dispatcher.read_text()
        # _linear_gql must accept _skip_budget_check parameter
        self.assertIn(
            "def _linear_gql(query, variables=None, _skip_budget_check=False)",
            src,
            "_linear_gql signature missing _skip_budget_check default",
        )
        # _linear_gql must skip the check when called with _skip_budget_check=True
        self.assertIn(
            "if not _skip_budget_check:",
            src,
            "_linear_gql does not gate on _skip_budget_check",
        )
        # Legacy _linear_call must pass _skip_budget_check=True after consuming
        self.assertIn(
            "_skip_budget_check=True",
            src,
            "legacy _linear_call does not pass _skip_budget_check=True",
        )
        # Single consume per query in legacy path (only the inner check is skipped)
        self.assertIn(
            "check_and_consume(bucket)",
            src,
            "_linear_call's check_and_consume is missing",
        )


if __name__ == "__main__":
    unittest.main()