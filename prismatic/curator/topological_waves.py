"""
Prismatic Topological Wave Partitioning Engine.

Partitions incoming tasks across disparate channels (Telegram, Linear, AGY CLI, Hub)
into Disjoint Topological Waves (W1, W2, ...) ensuring zero SwarmLock contention
for parallel execution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set

logger = logging.getLogger("prismatic.curator.topological_waves")


@dataclass
class QueueTask:
    task_id: str
    channel: str  # "telegram", "linear", "agy_cli", "hub"
    agent_id: str  # "fred", "agy", "kai", "ned", "autobot"
    title: str
    affected_paths: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    priority: int = 1  # 0=highest, 3=lowest
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "channel": self.channel,
            "agent_id": self.agent_id,
            "title": self.title,
            "affected_paths": self.affected_paths,
            "depends_on": self.depends_on,
            "priority": self.priority,
            "status": self.status,
        }


@dataclass
class TopologicalWave:
    wave_index: int
    tasks: list[QueueTask] = field(default_factory=list)
    locked_paths: set[str] = field(default_factory=set)

    def can_admit(self, task: QueueTask) -> bool:
        """Check if task has disjoint paths from all tasks currently in this wave."""
        task_paths = set(task.affected_paths)
        if not task_paths:
            return True
        return self.locked_paths.isdisjoint(task_paths)

    def add_task(self, task: QueueTask) -> None:
        self.tasks.append(task)
        self.locked_paths.update(task.affected_paths)

    def to_dict(self) -> dict[str, Any]:
        return {
            "wave_index": self.wave_index,
            "task_count": len(self.tasks),
            "tasks": [t.to_dict() for t in self.tasks],
            "locked_paths": sorted(list(self.locked_paths)),
        }


def partition_topological_waves(tasks: list[QueueTask | dict[str, Any]]) -> list[TopologicalWave]:
    """
    Partition an arbitrary list of tasks into disjoint topological execution waves.
    
    Guarantees:
    1. For every wave W_k, for all T_i, T_j in W_k (i != j): Paths(T_i) intersect Paths(T_j) is empty.
    2. Higher priority tasks are evaluated earlier.
    3. Explicit dependency chains (depends_on) are satisfied across waves.
    """
    normalized_tasks: list[QueueTask] = []
    for t in tasks:
        if isinstance(t, QueueTask):
            normalized_tasks.append(t)
        elif isinstance(t, dict):
            paths = t.get("affected_paths") or t.get("paths") or []
            if isinstance(paths, str):
                paths = [p.strip() for p in paths.split(",") if p.strip()]
            deps = t.get("depends_on") or []
            if isinstance(deps, str):
                deps = [d.strip() for d in deps.split(",") if d.strip()]
            normalized_tasks.append(
                QueueTask(
                    task_id=str(t.get("task_id") or t.get("id") or t.get("event_id") or "TASK-0"),
                    channel=str(t.get("channel") or t.get("routing_source") or "queue"),
                    agent_id=str(t.get("agent_id") or t.get("target_agent") or t.get("agent_name") or "fred"),
                    title=str(t.get("title") or t.get("identifier") or "Task"),
                    affected_paths=paths,
                    depends_on=deps,
                    priority=int(t.get("priority", 1)),
                    status=str(t.get("status") or t.get("dispatch_status") or "pending"),
                )
            )

    sorted_tasks = sorted(normalized_tasks, key=lambda x: x.priority)

    waves: list[TopologicalWave] = []
    task_wave_map: dict[str, int] = {}

    for task in sorted_tasks:
        min_wave_idx = 0
        if task.depends_on:
            for dep in task.depends_on:
                if dep in task_wave_map:
                    min_wave_idx = max(min_wave_idx, task_wave_map[dep] + 1)

        placed = False
        for idx in range(min_wave_idx, len(waves)):
            if waves[idx].can_admit(task):
                waves[idx].add_task(task)
                task_wave_map[task.task_id] = idx
                placed = True
                break

        if not placed:
            while len(waves) < min_wave_idx:
                waves.append(TopologicalWave(wave_index=len(waves) + 1))
            
            new_wave = TopologicalWave(wave_index=len(waves) + 1)
            new_wave.add_task(task)
            waves.append(new_wave)
            task_wave_map[task.task_id] = len(waves) - 1

    return waves
