from __future__ import annotations

import abc
import asyncio
import inspect

from prismatic.gateway.server import get_harnesses
from prismatic.harnesses.base import AgentHarness


class DemoHarness(AgentHarness):
    @property
    def name(self) -> str:
        return "demo"

    @property
    def models(self) -> list[str]:
        return ["demo-model"]

    def dispatch(self, task: dict) -> str:
        return "run-1"

    def status(self, run_id: str) -> dict:
        return {"status": "completed", "started_at": None, "completed_at": None, "error": None}

    def cancel(self, run_id: str) -> bool:
        return True

    def logs(self, run_id: str, tail: int = 100) -> list[str]:
        return ["ok"]

    def cost(self, run_id: str) -> dict:
        return {"tokens_in": 0, "tokens_out": 0, "dollars": 0.0}


def test_agent_harness_is_abstract_contract() -> None:
    assert inspect.isabstract(AgentHarness)
    assert issubclass(AgentHarness, abc.ABC)
    assert AgentHarness.__abstractmethods__ == {
        "name",
        "models",
        "dispatch",
        "status",
        "cancel",
        "logs",
        "cost",
    }


def test_agent_harness_default_health_uses_name() -> None:
    assert DemoHarness().health() == {"status": "ok", "harness": "demo"}


def test_harness_registry_endpoint_returns_configured_adapters() -> None:
    harnesses = asyncio.run(get_harnesses())
    by_name = {entry["name"]: entry for entry in harnesses}

    assert list(by_name) == ["agy-cli", "codex-cli", "hermes", "vllm"]
    assert by_name["agy-cli"] == {
        "name": "agy-cli",
        "module": "prismatic.harnesses.agy_cli",
        "enabled": True,
    }
    assert by_name["codex-cli"]["enabled"] is False
    assert by_name["hermes"]["enabled"] is True
    assert by_name["vllm"]["module"] == "prismatic.harnesses.vllm"
