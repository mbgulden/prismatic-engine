"""
tests/test_linear_api_compat.py
=================================

Verifies the linear_api_compat shim routes through the engine's
LinearBudget-gated gql() — replacing the broken legacy linear_call import
that broke 4 cron scripts after the GRO-2020 module move.

Refs: GRO-2050 cron reduction + GRO-2034 budget codification.
"""

import unittest
import sys
import os
from unittest.mock import patch, MagicMock


class TestLinearApiCompat(unittest.TestCase):
    def setUp(self):
        # Ensure a clean import each test. The shim caches the engine's gql
        # in module-level state — pop BOTH the shim and the engine dispatcher
        # so each test re-imports fresh.
        for mod in ("linear_api_compat", "prismatic.dispatcher"):
            sys.modules.pop(mod, None)

    def tearDown(self):
        for mod in ("linear_api_compat", "prismatic.dispatcher"):
            sys.modules.pop(mod, None)

    def test_linear_call_routes_through_engine_gql(self):
        """linear_call should call prismatic.dispatcher.gql, not bypass it."""
        with patch.dict(sys.modules, {
            "prismatic": MagicMock(),
            "prismatic.dispatcher": MagicMock(),
        }):
            mock_engine_gql = sys.modules["prismatic.dispatcher"].gql
            mock_engine_gql.return_value = {"data": {"viewer": {"id": "abc"}}}

            # Import after mocks are in place
            import linear_api_compat
            result = linear_api_compat.linear_call(
                "cron.test", "query{ viewer { id } }"
            )

            # Verify engine gql was called
            mock_engine_gql.assert_called_once_with("query{ viewer { id } }", None)
            # Verify result is the full response dict
            self.assertEqual(result, {"data": {"viewer": {"id": "abc"}}})

    def test_linear_call_sets_agent_env_var(self):
        """The agent_name must be set in PRISMATIC_CURRENT_AGENT_NAME."""
        with patch.dict(sys.modules, {
            "prismatic": MagicMock(),
            "prismatic.dispatcher": MagicMock(),
        }):
            mock_engine_gql = sys.modules["prismatic.dispatcher"].gql
            mock_engine_gql.return_value = {"data": {}}
            import linear_api_compat
            # Make sure env var is unset before call
            os.environ.pop("PRISMATIC_CURRENT_AGENT_NAME", None)
            linear_api_compat.linear_call(
                "cron.my_special_job", "query{ x }", {"y": 1}
            )
            self.assertEqual(
                os.environ.get("PRISMATIC_CURRENT_AGENT_NAME"),
                "cron.my_special_job",
            )

    def test_linear_call_passes_variables(self):
        """Variables arg is forwarded to engine gql."""
        with patch.dict(sys.modules, {
            "prismatic": MagicMock(),
            "prismatic.dispatcher": MagicMock(),
        }):
            mock_engine_gql = sys.modules["prismatic.dispatcher"].gql
            mock_engine_gql.return_value = {"data": {}}
            import linear_api_compat
            variables = {"id": "GRO-1234", "first": 10}
            linear_api_compat.linear_call("cron.test", "query($id: ID!){ issue(id: $id) { id } }", variables)
            mock_engine_gql.assert_called_once_with(
                "query($id: ID!){ issue(id: $id) { id } }",
                variables,
            )


if __name__ == "__main__":
    unittest.main()