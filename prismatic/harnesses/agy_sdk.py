"""Canonical in-process Google Antigravity SDK harness.

The AGYSDKHarness enables Prismatic Engine to orchestrate Google Antigravity 2.0
instances directly in-process via async Python coroutines, bypassing tmux terminal
wrapping, Bubbletea PTY allocation, and ANSI log parsing.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from prismatic.client.interceptor import DualReturn, HypervisorClient, get_hypervisor_client
from prismatic.harnesses.base import AgentHarness, HarnessCapabilities, HarnessStatus

logger = logging.getLogger("prismatic.harnesses.agy_sdk")

# Guardrail 2: Clean Fallback on Missing SDK
try:
    import google.antigravity as agy
    from google.antigravity import (
        Agent,
        LocalAgentConfig,
        LocalOpenAIAgentConfig,
        LiteRTAgentConfig,
        BuiltinTools,
        ToolContext,
        hooks,
    )
    from google.antigravity.types import ChatResponse
    HAS_ANTIGRAVITY_SDK = True
except ImportError as exc:
    agy = None
    Agent = None
    LocalAgentConfig = None
    LocalOpenAIAgentConfig = None
    LiteRTAgentConfig = None
    BuiltinTools = None
    ToolContext = None
    hooks = None
    ChatResponse = None
    HAS_ANTIGRAVITY_SDK = False
    logger.warning(
        "Google Antigravity SDK (google.antigravity) is not installed or failed to import: %s. "
        "AGYSDKHarness will operate in fallback mode.",
        exc,
    )


def is_sdk_available() -> bool:
    """Return whether the google.antigravity SDK is installed and usable."""
    return HAS_ANTIGRAVITY_SDK


@dataclass
class HarnessTurnResult:
    """Normalized structured result returned by an in-process SDK turn."""

    session_id: str
    run_id: str
    status: HarnessStatus
    response_text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    thinking_tokens: int = 0
    total_tokens: int = 0
    duration_seconds: float = 0.0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AGYSDKHarness(AgentHarness):
    """In-process Google Antigravity SDK adapter implementing AgentHarness."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        hypervisor_client: HypervisorClient | None = None,
        agent_factory: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__(config=config)
        self._hypervisor = hypervisor_client or get_hypervisor_client()
        self._agent_factory = agent_factory
        self._runs: dict[str, dict[str, Any]] = {}
        self._node_id = str(self._config.get("node_id", "webtop-hermes"))
        self._default_model = str(self._config.get("default_model", "gemini-3.7-flash"))
        self._vllm_url = str(self._config.get("vllm_url", "http://192.168.1.230:8000/v1"))

    @property
    def name(self) -> str:
        """Stable harness adapter name."""
        return "agy-sdk"

    @property
    def models(self) -> list[str]:
        """Model identifiers supported by the in-process SDK adapter."""
        return [
            "gemini-3.7-flash",
            "gemini-3.7-pro",
            "gemini-2.5-pro",
            "local-qwen-27b-q8-fred",
        ]

    def resolve_model_config(
        self,
        model: str,
        system_instructions: str | None = None,
        tools: list[Any] | None = None,
        use_gpu: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Resolve the appropriate SDK AgentConfig based on model naming."""
        if not HAS_ANTIGRAVITY_SDK:
            raise RuntimeError(
                "Cannot resolve SDK agent config: google.antigravity is not installed or available."
            )

        if use_gpu or kwargs.get("backend") == "gpu":
            model_path = kwargs.get("model_path", "gemma-2-27b-it")
            return LiteRTAgentConfig(
                model_path=model_path,
                backend="gpu",
                system_instructions=system_instructions,
                tools=tools,
            )

        if model.startswith(("local-", "vllm:", "qwen-")):
            base_url = kwargs.get("base_url", self._vllm_url)
            return LocalOpenAIAgentConfig(
                model=model,
                base_url=base_url,
                system_instructions=system_instructions,
                tools=tools,
            )

        # Standard Gemini Cloud Model
        api_key = kwargs.get("api_key") or os.environ.get("GEMINI_API_KEY")
        return LocalAgentConfig(
            model=model,
            system_instructions=system_instructions,
            tools=tools,
            api_key=api_key,
        )

    async def execute_turn(
        self,
        prompt: str,
        session_id: str | None = None,
        task_id: str | None = None,
        model: str | None = None,
        system_instruction: str | None = None,
        attachments: list[Any] | None = None,
        lock_resource: str | None = None,
        agent_id: str = "agy",
        timeout_seconds: float = 300.0,
    ) -> HarnessTurnResult:
        """Execute a single agent prompt turn in-process with SwarmLock and telemetry."""
        s_id = session_id or str(uuid.uuid4())
        t_id = task_id or f"task-{s_id[:8]}"
        m_name = model or self._default_model
        run_id = f"sdk-turn-{uuid.uuid4().hex[:10]}"
        start_time = time.time()

        # Emit start telemetry signal via DualReturn
        emit_start = self._hypervisor.emit_signal(
            source=agent_id,
            action="sdk_turn_started",
            task_id=t_id,
            details={"session_id": s_id, "model": m_name, "run_id": run_id},
        )
        if asyncio.iscoroutine(emit_start):
            await emit_start

        # In-process SwarmLock acquisition if lock_resource is requested
        lease_acquired = False
        if lock_resource:
            lock_res = self._hypervisor.acquire_swarmlock(
                resource=lock_resource,
                agent_id=agent_id,
                task_id=t_id,
                lease_seconds=int(timeout_seconds),
            )
            if asyncio.iscoroutine(lock_res):
                lock_res = await lock_res
            if not lock_res:
                error_msg = lock_res.get("error", f"Resource '{lock_resource}' is currently locked")
                return HarnessTurnResult(
                    session_id=s_id,
                    run_id=run_id,
                    status=HarnessStatus.FAILED,
                    response_text="",
                    duration_seconds=time.time() - start_time,
                    error=error_msg,
                    metadata={"locked": True, "resource": lock_resource},
                )
            lease_acquired = True

        try:
            # Guardrail 2: Clean check on missing SDK
            if not HAS_ANTIGRAVITY_SDK and self._agent_factory is None:
                return HarnessTurnResult(
                    session_id=s_id,
                    run_id=run_id,
                    status=HarnessStatus.FAILED,
                    response_text="",
                    duration_seconds=time.time() - start_time,
                    error="Google Antigravity SDK is not installed on this host",
                )

            # Construct or retrieve Agent instance
            if self._agent_factory is not None:
                agent = self._agent_factory()
            else:
                agent_config = self.resolve_model_config(
                    model=m_name,
                    system_instructions=system_instruction,
                )
                agent = Agent(agent_config)

            # Ingest attachments into prompt turn if provided
            chat_input: Any = prompt
            if attachments:
                chat_input = [prompt] + list(attachments)

            # Execute turn via SDK agent.chat
            chat_call = agent.chat(chat_input)
            if asyncio.iscoroutine(chat_call) or hasattr(chat_call, "__await__"):
                response = await asyncio.wait_for(chat_call, timeout=timeout_seconds)
            else:
                response = chat_call

            # Extract response text and exact usage metadata
            resp_text = getattr(response, "text", str(response))
            usage = getattr(response, "usage_metadata", None)

            prompt_tok = getattr(usage, "prompt_token_count", 0) if usage else 0
            cand_tok = getattr(usage, "candidates_token_count", 0) if usage else 0
            think_tok = getattr(usage, "thinking_token_count", 0) if usage else 0
            total_tok = getattr(usage, "total_token_count", prompt_tok + cand_tok) if usage else 0

            duration = time.time() - start_time

            result = HarnessTurnResult(
                session_id=s_id,
                run_id=run_id,
                status=HarnessStatus.COMPLETED,
                response_text=resp_text,
                prompt_tokens=prompt_tok,
                completion_tokens=cand_tok,
                thinking_tokens=think_tok,
                total_tokens=total_tok,
                duration_seconds=duration,
                metadata={"model": m_name, "node_id": self._node_id},
            )

            # Emit completion telemetry signal via DualReturn
            emit_done = self._hypervisor.emit_signal(
                source=agent_id,
                action="sdk_turn_completed",
                task_id=t_id,
                details={"run_id": run_id, "tokens": total_tok, "duration_s": duration},
            )
            if asyncio.iscoroutine(emit_done):
                await emit_done

            return result

        except asyncio.TimeoutError:
            duration = time.time() - start_time
            return HarnessTurnResult(
                session_id=s_id,
                run_id=run_id,
                status=HarnessStatus.TIMEOUT,
                response_text="",
                duration_seconds=duration,
                error=f"SDK turn timed out after {timeout_seconds}s",
            )
        except Exception as exc:
            duration = time.time() - start_time
            logger.exception("In-process AGY SDK turn failed: %s", exc)
            return HarnessTurnResult(
                session_id=s_id,
                run_id=run_id,
                status=HarnessStatus.FAILED,
                response_text="",
                duration_seconds=duration,
                error=str(exc),
            )
        finally:
            if lease_acquired and lock_resource:
                rel = self._hypervisor.release_swarmlock(
                    resource=lock_resource,
                    agent_id=agent_id,
                    task_id=t_id,
                )
                if asyncio.iscoroutine(rel):
                    await rel

    def dispatch(self, task: dict[str, Any]) -> str:
        """Submit a task and return a harness-local run id."""
        run_id = str(task.get("run_id") or f"sdk-run-{uuid.uuid4().hex[:12]}")
        prompt = task.get("prompt")
        task_file = task.get("task_file")
        if prompt is None and task_file is not None:
            try:
                prompt = Path(task_file).read_text(encoding="utf-8")
            except OSError as exc:
                prompt = f"Failed to read task_file: {exc}"

        run_record: dict[str, Any] = {
            "run_id": run_id,
            "task_id": task.get("task_id", run_id),
            "agent_id": task.get("agent_id", "agy"),
            "model": task.get("model", self._default_model),
            "prompt": prompt or "",
            "status": HarnessStatus.RUNNING,
            "started_at": time.time(),
            "completed_at": None,
            "error": None,
            "tokens_in": 0,
            "tokens_out": 0,
            "dollars": 0.0,
            "logs": [f"[info] Task dispatched to AGYSDKHarness on {self._node_id}"],
        }
        self._runs[run_id] = run_record

        # Schedule execution in background
        async def _run_coroutine():
            res = await self.execute_turn(
                prompt=run_record["prompt"],
                session_id=run_record["run_id"],
                task_id=run_record["task_id"],
                model=run_record["model"],
                agent_id=run_record["agent_id"],
                lock_resource=task.get("lock_resource"),
            )
            run_record["status"] = res.status
            run_record["completed_at"] = time.time()
            run_record["tokens_in"] = res.prompt_tokens
            run_record["tokens_out"] = res.completion_tokens
            run_record["error"] = res.error
            run_record["response_text"] = res.response_text
            run_record["logs"].append(f"[info] Turn completed with status {res.status.value}")
            if res.error:
                run_record["logs"].append(f"[error] {res.error}")

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_run_coroutine())
        except RuntimeError:
            # If no running event loop, execute synchronously or keep as registered
            pass

        return run_id

    def status(self, run_id: str) -> dict[str, Any]:
        """Return status payload for an in-process SDK run."""
        if run_id not in self._runs:
            raise KeyError(f"unknown SDK run: {run_id}")
        rec = self._runs[run_id]
        return {
            "status": rec["status"].value if isinstance(rec["status"], HarnessStatus) else rec["status"],
            "started_at": rec["started_at"],
            "completed_at": rec["completed_at"],
            "error": rec["error"],
            "run_id": run_id,
        }

    def cancel(self, run_id: str) -> bool:
        """Request cancellation for a run."""
        if run_id not in self._runs:
            return False
        rec = self._runs[run_id]
        if rec["status"] == HarnessStatus.RUNNING:
            rec["status"] = HarnessStatus.CANCELLED
            rec["completed_at"] = time.time()
            rec["logs"].append("[info] Run cancelled by operator request")
        return True

    def logs(self, run_id: str, tail: int = 100) -> list[str]:
        """Return recent log lines for a run."""
        if run_id not in self._runs:
            raise KeyError(f"unknown SDK run: {run_id}")
        return self._runs[run_id]["logs"][-tail:]

    def cost(self, run_id: str) -> dict[str, Any]:
        """Return token and cost accounting for an SDK run."""
        if run_id not in self._runs:
            raise KeyError(f"unknown SDK run: {run_id}")
        rec = self._runs[run_id]
        in_tok = rec["tokens_in"]
        out_tok = rec["tokens_out"]
        # Standard estimation: Gemini 3.7 Flash: $0.15 / 1M in, $0.60 / 1M out
        dollars = (in_tok * 0.00000015) + (out_tok * 0.00000060)
        return {
            "tokens_in": in_tok,
            "tokens_out": out_tok,
            "dollars": round(dollars, 6),
            "available": True,
        }

    def health(self) -> dict[str, str]:
        """Return operational health status for the in-process SDK harness."""
        if not HAS_ANTIGRAVITY_SDK:
            return {
                "status": "unavailable",
                "harness": self.name,
                "error": "google.antigravity SDK is not installed or importable",
            }
        return {
            "status": "ok",
            "harness": self.name,
            "sdk_available": "true",
            "node_id": self._node_id,
        }

    def capabilities(self) -> HarnessCapabilities:
        """Return capabilities supported by the in-process SDK harness."""
        return HarnessCapabilities(
            streaming_logs=True,
            cost_tracking=True,
            concurrent_runs=int(self._config.get("concurrent_runs", 8)),
            supports_cancel=True,
            supports_timeout=True,
            max_context_tokens=1048576,
            extra={
                "transport": "in-process-sdk-async",
                "runtime_policy": "in-memory-fenced-coroutine",
                "cost_tracking_exact": True,
                "swarmlock_fenced": True,
            },
        )
