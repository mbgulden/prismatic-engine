"""Tests for DynamicFallbackRouter in prismatic/router.py."""
import pytest
from unittest.mock import MagicMock, call

from prismatic.router import DynamicFallbackRouter


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def mock_telemetry():
    """Create a mock TelemetryCollector."""
    tm = MagicMock()
    tm.check_circuit.return_value = False  # all routes open by default
    return tm


@pytest.fixture
def router(mock_telemetry):
    """Create a DynamicFallbackRouter with default chains."""
    return DynamicFallbackRouter(mock_telemetry)


@pytest.fixture
def custom_router(mock_telemetry):
    """Create a router with custom fallback chains."""
    chains = {
        "test-agent": {
            "primary": ["model-a", "model-b", "model-c"],
        },
    }
    return DynamicFallbackRouter(mock_telemetry, fallback_chains=chains)


# ── Constructor tests ────────────────────────────────────────


class TestConstructor:
    def test_default_chains(self, router):
        """Default chains include all six agents."""
        assert "agy" in router._chains
        assert "fred" in router._chains
        assert "kai" in router._chains
        assert "jules" in router._chains
        assert "codex" in router._chains
        assert "claude-code" in router._chains
        assert router.stats["total_checks"] == 0
        assert router.stats["fallbacks_activated"] == 0

    def test_custom_chains(self, custom_router):
        """Custom chains override defaults."""
        assert "test-agent" in custom_router._chains
        assert "agy" not in custom_router._chains
        assert custom_router._chains["test-agent"]["primary"] == [
            "model-a", "model-b", "model-c",
        ]

    def test_empty_chains(self, mock_telemetry):
        """Empty chains dict results in no fallback capability."""
        r = DynamicFallbackRouter(mock_telemetry, fallback_chains={})
        assert r._chains == {}

    def test_none_chains_uses_default(self, mock_telemetry):
        """None chains fall back to DEFAULT_CHAINS."""
        r = DynamicFallbackRouter(mock_telemetry, fallback_chains=None)
        assert r._chains == DynamicFallbackRouter.DEFAULT_CHAINS


# ── select_route tests ───────────────────────────────────────


class TestSelectRoute:
    def test_primary_route_selected(self, custom_router, mock_telemetry):
        """When no breakers are tripped, primary model is returned."""
        result = custom_router.select_route("ISSUE-1", "test-agent", "primary")
        assert result["agent"] == "test-agent"
        assert result["model"] == "model-a"
        assert result["fallback"] is False
        assert mock_telemetry.check_circuit.call_count == 1

    def test_fallback_on_tripped_primary(self, custom_router, mock_telemetry):
        """When primary model's breaker is tripped, next in chain is used."""
        mock_telemetry.check_circuit.side_effect = [True, False]

        result = custom_router.select_route("ISSUE-2", "test-agent", "primary")

        assert result["model"] == "model-b"
        assert result["fallback"] is True
        # Should have checked circuit breaker twice
        assert mock_telemetry.check_circuit.call_count == 2

    def test_all_fallbacks_exhausted(self, custom_router, mock_telemetry):
        """When all models in chain are tripped, model is None."""
        mock_telemetry.check_circuit.side_effect = [True, True, True]

        result = custom_router.select_route("ISSUE-3", "test-agent", "primary")

        assert result["model"] is None
        assert result["fallback"] is False
        # Should record a fallback_exhausted loop event
        mock_telemetry.record_loop.assert_called_once()
        call_kwargs = mock_telemetry.record_loop.call_args[1]
        assert call_kwargs["loop_type"] == "fallback_exhausted"

    def test_no_chain_for_agent(self, router, mock_telemetry):
        """Unknown agent returns None model without checking circuit."""
        result = router.select_route("ISSUE-4", "nonexistent-agent")
        assert result["model"] is None
        assert result["fallback"] is False
        # Should NOT have called check_circuit at all
        mock_telemetry.check_circuit.assert_not_called()

    def test_fallback_is_counted_in_stats(self, custom_router, mock_telemetry):
        """Fallback count increments when fallback is selected."""
        mock_telemetry.check_circuit.side_effect = [True, False]

        custom_router.select_route("ISSUE-5", "test-agent", "primary")
        assert custom_router.stats["fallbacks_activated"] == 1
        assert custom_router.stats["total_checks"] == 2

    def test_same_role_as_primary_when_role_missing(self, mock_telemetry):
        """When agent has no matching role, falls back to 'primary'."""
        chains = {
            "test-agent": {
                "primary": ["model-p1"],
                "optimizer": ["model-o1"],
            },
        }
        r = DynamicFallbackRouter(mock_telemetry, fallback_chains=chains)
        result = r.select_route("ISSUE-6", "test-agent", "non-existent-role")
        # Should use 'primary' as default
        assert result["model"] == "model-p1"

    def test_micro_cost_passed_to_check_circuit(self, custom_router, mock_telemetry):
        """Custom micro_cost is forwarded to check_circuit."""
        mock_telemetry.check_circuit.return_value = False
        custom_router.select_route("ISSUE-7", "test-agent", micro_cost=3)
        mock_telemetry.check_circuit.assert_called_with(
            issue_id="ISSUE-7",
            agent="test-agent",
            micro_count=3,
            macro_count=0,
        )


# ── report_success tests ─────────────────────────────────────


class TestReportSuccess:
    def test_resets_breaker(self, router, mock_telemetry):
        """report_success resets the breaker and records a reset event."""
        router.report_success("ISSUE-10", "fred")

        mock_telemetry.reset_breaker.assert_called_once_with("ISSUE-10")
        mock_telemetry.record_loop.assert_called_once()
        call_kwargs = mock_telemetry.record_loop.call_args[1]
        assert call_kwargs["loop_type"] == "breaker_reset"
        assert call_kwargs["resolved"] is True


# ── report_failure tests ─────────────────────────────────────


class TestReportFailure:
    def test_returns_same_model_when_not_tripped(self, custom_router, mock_telemetry):
        """If breaker doesn't trip, the same model is returned."""
        mock_telemetry.check_circuit.return_value = False

        result = custom_router.report_failure("ISSUE-20", "test-agent", "model-a")

        assert result["model"] == "model-a"
        assert result["fallback"] is False

    def test_fallback_on_trip(self, custom_router, mock_telemetry):
        """When failure trips the breaker, fallback is attempted."""
        # First call (report_failure's check): True (tripped)
        # Second call (select_route's check): False (fallback available)
        mock_telemetry.check_circuit.side_effect = [True, False]

        result = custom_router.report_failure("ISSUE-21", "test-agent", "model-a")

        assert result["model"] == "model-b"
        assert result["fallback"] is True


# ── get_chain tests ──────────────────────────────────────────


class TestGetChain:
    def test_returns_chain_for_known_agent(self, router):
        chain = router.get_chain("agy", "researcher")
        assert chain == ["deepseek-v4-pro", "deepseek-v4-flash", "gpt-4o-mini"]

    def test_returns_empty_for_unknown_agent(self, router):
        assert router.get_chain("unknown-agent") == []

    def test_returns_primary_for_missing_role(self, router):
        chain = router.get_chain("kai", "nonexistent")
        # Kai has no "nonexistent" key, so falls back to primary
        assert chain == []


# ── Default chain structure tests ────────────────────────────


class TestDefaultChains:
    """Verify default chain structure for all configured agents."""

    @pytest.mark.parametrize("agent,roles,expected_first", [
        ("agy", ["researcher", "builder"], "deepseek-v4-flash"),
        ("fred", ["primary"], "deepseek-v4-flash"),
        ("kai", ["developer", "css", "js", "content"], "deepseek-v4-flash"),
        ("jules", ["primary"], "deepseek-v4-flash"),
        ("codex", ["primary"], "gpt-4o"),
        ("claude-code", ["primary"], "claude-sonnet-4-20250514"),
    ])
    def test_default_chain_first_entry(self, router, agent, roles, expected_first):
        """First entry of the default chain for each agent/role."""
        for role in roles:
            chain = router.get_chain(agent, role)
            assert len(chain) > 0, f"{agent}/{role} should have a chain"
            # First entry should be the expected model
            assert chain[0] == expected_first or "deepseek-v4-flash" in chain, \
                f"{agent}/{role} first entry mismatch: {chain[0]}"
