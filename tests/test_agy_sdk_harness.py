"""Comprehensive unit test suite for AGYSDKHarness and dual-runtime canary."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prismatic.client.interceptor import DualReturn, HypervisorClient
from prismatic.harnesses import AGYCLIHarness, get_agy_harness
from prismatic.harnesses.agy_sdk import (
    AGYSDKHarness,
    HarnessStatus,
    HarnessTurnResult,
    is_sdk_available,
)


class MockUsageMetadata:
    def __init__(self, prompt: int = 150, candidates: int = 50, total: int = 200, thinking: int = 20):
        self.prompt_token_count = prompt
        self.candidates_token_count = candidates
        self.total_token_count = total
        self.thinking_token_count = thinking


class MockChatResponse:
    def __init__(self, text: str = "Mock response from Antigravity SDK"):
        self.text = text
        self.usage_metadata = MockUsageMetadata()


class MockSDKAgent:
    def __init__(self, response_text: str = "Mock turn response"):
        self.response_text = response_text
        self.chat_calls: list[Any] = []

    async def chat(self, prompt: Any):
        self.chat_calls.append(prompt)
        return MockChatResponse(self.response_text)


@pytest.fixture
def mock_hypervisor():
    """Mock HypervisorClient supporting DualReturn interfaces."""
    client = MagicMock(spec=HypervisorClient)
    client.acquire_swarmlock.return_value = DualReturn({"ok": True, "lease_id": "test-lease-1"})
    client.release_swarmlock.return_value = DualReturn({"ok": True})
    client.emit_signal.return_value = DualReturn(True)
    return client


def test_harness_instantiation_and_contract():
    """Test 1: AGYSDKHarness implements standard AgentHarness contract."""
    harness = AGYSDKHarness()
    assert harness.name == "agy-sdk"
    assert "gemini-3.7-flash" in harness.models
    assert "local-qwen-27b-q8-fred" in harness.models

    caps = harness.capabilities()
    assert caps.streaming_logs is True
    assert caps.cost_tracking is True
    assert caps.extra.get("transport") == "in-process-sdk-async"

    health = harness.health()
    assert health["status"] == "ok"
    assert health["harness"] == "agy-sdk"


def test_model_config_resolution_gemini():
    """Test 2: Model config resolution for standard Gemini cloud model."""
    harness = AGYSDKHarness()
    config = harness.resolve_model_config(
        model="gemini-3.7-flash",
        system_instructions="You are a helpful assistant",
    )
    assert config.model == "gemini-3.7-flash"
    assert config.system_instructions == "You are a helpful assistant"


def test_model_config_resolution_vllm():
    """Test 3: Model config resolution for local LAN vLLM models."""
    harness = AGYSDKHarness()
    config = harness.resolve_model_config(
        model="local-qwen-27b-q8-fred",
        base_url="http://192.168.1.230:8000/v1",
    )
    assert config.model == "local-qwen-27b-q8-fred"
    assert config.base_url == "http://192.168.1.230:8000/v1"


def test_model_config_resolution_litert_gpu():
    """Test 4: Model config resolution for LiteRT on-device GPU inference."""
    harness = AGYSDKHarness()
    config = harness.resolve_model_config(
        model="gemma-2-27b-it",
        model_path="models/gemma-2-27b.bin",
        use_gpu=True,
    )
    assert config.model_path == "models/gemma-2-27b.bin"
    assert str(config.backend).lower().endswith("gpu")


def test_canary_runtime_switch_default_cli():
    """Test 5: Guardrail 1 - Canary default is 'cli' when PRISMATIC_AGY_RUNTIME is unset."""
    with patch.dict("os.environ", {}, clear=True):
        harness = get_agy_harness()
        assert isinstance(harness, AGYCLIHarness)
        assert harness.name == "agy-cli"

    harness_explicit = get_agy_harness("cli")
    assert isinstance(harness_explicit, AGYCLIHarness)


def test_canary_runtime_switch_sdk():
    """Test 6: Canary switch resolves AGYSDKHarness when 'sdk' is requested."""
    harness = get_agy_harness("sdk")
    assert isinstance(harness, AGYSDKHarness)
    assert harness.name == "agy-sdk"

    with patch.dict("os.environ", {"PRISMATIC_AGY_RUNTIME": "sdk"}):
        harness_env = get_agy_harness()
        assert isinstance(harness_env, AGYSDKHarness)


def test_canary_fallback_on_missing_sdk():
    """Test 7: Guardrail 2 - Clean fallback to AGYCLIHarness if SDK is unavailable."""
    with patch("prismatic.harnesses.agy_sdk.is_sdk_available", return_value=False):
        harness = get_agy_harness("sdk")
        # Must fall back cleanly to CLI harness rather than crashing
        assert isinstance(harness, AGYCLIHarness)
        assert harness.name == "agy-cli"


@pytest.mark.asyncio
async def test_execute_turn_mock_agent_success_with_tokens(mock_hypervisor):
    """Test 8: execute_turn extracts usage metadata and returns typed HarnessTurnResult."""
    mock_agent = MockSDKAgent(response_text="Refactored mesh module successfully")
    harness = AGYSDKHarness(
        hypervisor_client=mock_hypervisor,
        agent_factory=lambda: mock_agent,
    )

    result = await harness.execute_turn(
        prompt="Please refactor prismatic/mesh/tailscale.py",
        session_id="test-session-123",
        task_id="GRO-4861",
        model="gemini-3.7-flash",
    )

    assert isinstance(result, HarnessTurnResult)
    assert result.status == HarnessStatus.COMPLETED
    assert result.response_text == "Refactored mesh module successfully"
    assert result.prompt_tokens == 150
    assert result.completion_tokens == 50
    assert result.thinking_tokens == 20
    assert result.total_tokens == 200
    assert result.duration_seconds >= 0.0
    assert result.error is None

    # Verify telemetry signal emissions via DualReturn
    assert mock_hypervisor.emit_signal.call_count >= 2
    assert mock_hypervisor.emit_signal.call_args_list[0][1]["action"] == "sdk_turn_started"
    assert mock_hypervisor.emit_signal.call_args_list[1][1]["action"] == "sdk_turn_completed"


@pytest.mark.asyncio
async def test_execute_turn_swarmlock_and_signals_dual_return(mock_hypervisor):
    """Test 9: Guardrail 3 - execute_turn uses SwarmLock and DualReturn lifecycle."""
    mock_agent = MockSDKAgent("File written safely")
    harness = AGYSDKHarness(
        hypervisor_client=mock_hypervisor,
        agent_factory=lambda: mock_agent,
    )

    result = await harness.execute_turn(
        prompt="Write new feature",
        session_id="test-session-456",
        task_id="GRO-4861",
        lock_resource="prismatic/harnesses/agy_sdk.py",
    )

    assert result.status == HarnessStatus.COMPLETED
    assert mock_hypervisor.acquire_swarmlock.called
    assert mock_hypervisor.release_swarmlock.called
    assert mock_hypervisor.acquire_swarmlock.call_args[1]["resource"] == "prismatic/harnesses/agy_sdk.py"
    assert mock_hypervisor.release_swarmlock.call_args[1]["resource"] == "prismatic/harnesses/agy_sdk.py"


@pytest.mark.asyncio
async def test_execute_turn_deflection_on_locked_resource():
    """Test 10: execute_turn fails closed if target resource is already locked."""
    locked_client = MagicMock(spec=HypervisorClient)
    locked_client.emit_signal.return_value = DualReturn(True)
    locked_client.acquire_swarmlock.return_value = DualReturn({
        "ok": False,
        "error": "Resource 'file.py' is currently locked by 'fred'",
    })

    harness = AGYSDKHarness(hypervisor_client=locked_client)
    result = await harness.execute_turn(
        prompt="Mutate file",
        lock_resource="file.py",
        task_id="GRO-4861",
    )

    assert result.status == HarnessStatus.FAILED
    assert "currently locked" in result.error
    assert result.metadata.get("locked") is True


def test_dispatch_status_cost_lifecycle():
    """Test 11: AgentHarness lifecycle methods (dispatch, status, cost, cancel, logs)."""
    harness = AGYSDKHarness()
    task = {
        "task_id": "GRO-4861",
        "prompt": "Test dispatch task",
        "model": "gemini-3.7-flash",
    }
    run_id = harness.dispatch(task)
    assert run_id.startswith("sdk-run-")

    status_data = harness.status(run_id)
    assert status_data["run_id"] == run_id
    assert status_data["status"] in ("running", "completed")

    cost_data = harness.cost(run_id)
    assert cost_data["available"] is True
    assert "tokens_in" in cost_data
    assert "dollars" in cost_data

    log_lines = harness.logs(run_id)
    assert len(log_lines) >= 1
    assert "Task dispatched" in log_lines[0]

    cancel_res = harness.cancel(run_id)
    assert cancel_res is True
