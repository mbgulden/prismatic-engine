"""
LifecycleManager — 7-Step Task Iterative Loop State Machine.

Governs the formal lifecycle of a task from DECOMPOSE to INTEGRATE.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger("prismatic.core.lifecycle")


class TaskState(Enum):
    IDLE = "IDLE"
    DECOMPOSING = "DECOMPOSING"
    PENDING_PLAN_APPROVAL = "PENDING_PLAN_APPROVAL"
    DISPATCHING = "DISPATCHING"
    EXECUTING = "EXECUTING"
    REVIEWING = "REVIEWING"
    FEEDBACK = "FEEDBACK"
    REFINING = "REFINING"
    PENDING_INTEGRATION = "PENDING_INTEGRATION"
    INTEGRATING = "INTEGRATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class OrchestrationMode(Enum):
    INTERACTIVE = "Interactive"
    COLLABORATIVE = "Collaborative"
    AUTONOMOUS = "Autonomous"


@dataclass
class TaskContext:
    task_id: str
    issue_id: str
    state: TaskState = TaskState.IDLE
    mode: OrchestrationMode = OrchestrationMode.COLLABORATIVE
    iteration_count: int = 0
    max_iterations: int = 3
    contracts: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)

    def add_history(self, event: str, from_state: TaskState, to_state: TaskState, note: str = ""):
        self.history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "from": from_state.value,
            "to": to_state.value,
            "note": note
        })


class LifecycleManager:
    """
    Manages transitions between steps in the 7-step iterative loop.
    """

    def __init__(self, mode: OrchestrationMode = OrchestrationMode.COLLABORATIVE):
        self.mode = mode
        self._tasks: dict[str, TaskContext] = {}

    def create_task(self, issue_id: str, mode: OrchestrationMode | None = None) -> TaskContext:
        task_id = str(uuid.uuid4())
        context = TaskContext(
            task_id=task_id,
            issue_id=issue_id,
            mode=mode or self.mode
        )
        self._tasks[task_id] = context
        logger.info(f"Created new task {task_id} for issue {issue_id} in {context.mode.value} mode")
        return context

    def get_task(self, task_id: str) -> TaskContext | None:
        return self._tasks.get(task_id)

    def transition(self, task_id: str, event: str, payload: Any | None = None) -> TaskState:
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        prev_state = task.state

        # State machine logic
        if task.state == TaskState.IDLE:
            if event == "MEGAPROMPT_RECEIVED":
                task.state = TaskState.DECOMPOSING

        elif task.state == TaskState.DECOMPOSING:
            if event == "PLAN_GENERATED":
                task.contracts = payload or []
                if task.mode == OrchestrationMode.INTERACTIVE:
                    task.state = TaskState.PENDING_PLAN_APPROVAL
                else:
                    task.state = TaskState.DISPATCHING

        elif task.state == TaskState.PENDING_PLAN_APPROVAL:
            if event == "PLAN_APPROVED":
                task.state = TaskState.DISPATCHING
            elif event == "PLAN_REJECTED":
                task.state = TaskState.FAILED

        elif task.state == TaskState.DISPATCHING:
            if event == "AGENTS_PROVISIONED":
                task.state = TaskState.EXECUTING

        elif task.state == TaskState.EXECUTING:
            if event == "BRANCH_PUSHED":
                task.state = TaskState.REVIEWING

        elif task.state == TaskState.REVIEWING:
            if event == "REVIEW_PASSED":
                if task.mode == OrchestrationMode.INTERACTIVE:
                    task.state = TaskState.PENDING_INTEGRATION
                else:
                    task.state = TaskState.INTEGRATING
            elif event == "REVIEW_FAILED":
                if task.iteration_count < task.max_iterations:
                    task.state = TaskState.FEEDBACK
                    task.iteration_count += 1
                else:
                    logger.warning(f"Task {task_id} exceeded max iterations ({task.max_iterations}). Escalating.")
                    task.state = TaskState.PENDING_PLAN_APPROVAL # Escalate to human

        elif task.state == TaskState.FEEDBACK:
            if event == "FEEDBACK_DELIVERED":
                task.state = TaskState.REFINING

        elif task.state == TaskState.REFINING:
            if event == "BRANCH_REVISED":
                task.state = TaskState.REVIEWING

        elif task.state == TaskState.PENDING_INTEGRATION:
            if event == "INTEGRATION_APPROVED":
                task.state = TaskState.INTEGRATING
            elif event == "INTEGRATION_REJECTED":
                task.state = TaskState.FEEDBACK

        elif task.state == TaskState.INTEGRATING:
            if event == "MERGE_SUCCESS":
                task.state = TaskState.COMPLETED

        if prev_state != task.state:
            task.add_history(event, prev_state, task.state)
            logger.info(f"Task {task_id} transitioned: {prev_state.value} --({event})--> {task.state.value}")

        return task.state
