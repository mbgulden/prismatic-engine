"""
Unit tests for Prismatic Topological Wave Partitioning.
"""

from prismatic.curator.topological_waves import (
    QueueTask,
    TopologicalWave,
    partition_topological_waves,
)


def test_disjoint_tasks_assigned_to_same_wave():
    tasks = [
        QueueTask(task_id="T1", channel="telegram", agent_id="fred", title="Edit Server", affected_paths=["prismatic/gateway/server.py"]),
        QueueTask(task_id="T2", channel="linear", agent_id="kai", title="Edit UI", affected_paths=["prismatic/gateway/dashboard_src/scripts/dashboard.js"]),
        QueueTask(task_id="T3", channel="agy_cli", agent_id="agy", title="Edit Lock", affected_paths=["prismatic/core/locking.py"]),
    ]

    waves = partition_topological_waves(tasks)
    assert len(waves) == 1
    assert waves[0].wave_index == 1
    assert len(waves[0].tasks) == 3
    assert "prismatic/gateway/server.py" in waves[0].locked_paths
    assert "prismatic/core/locking.py" in waves[0].locked_paths


def test_conflicting_tasks_partitioned_to_successive_waves():
    tasks = [
        QueueTask(task_id="T1", channel="telegram", agent_id="fred", title="Fred Edit Server", affected_paths=["prismatic/gateway/server.py"]),
        QueueTask(task_id="T2", channel="agy_cli", agent_id="agy", title="AGY Edit Server", affected_paths=["prismatic/gateway/server.py", "prismatic/core/locking.py"]),
        QueueTask(task_id="T3", channel="linear", agent_id="kai", title="Kai Edit UI", affected_paths=["prismatic/gateway/dashboard_src/scripts/dashboard.js"]),
    ]

    waves = partition_topological_waves(tasks)
    assert len(waves) == 2
    # T1 and T3 can run in Wave 1
    w1_task_ids = {t.task_id for t in waves[0].tasks}
    assert "T1" in w1_task_ids
    assert "T3" in w1_task_ids

    # T2 must run in Wave 2 because server.py is locked in Wave 1
    w2_task_ids = {t.task_id for t in waves[1].tasks}
    assert "T2" in w2_task_ids


def test_dependency_chain_ordering():
    tasks = [
        QueueTask(task_id="T1", channel="linear", agent_id="ned", title="DB Migration", affected_paths=["prismatic/review_factory/db.py"]),
        QueueTask(task_id="T2", channel="agy_cli", agent_id="agy", title="API Update", affected_paths=["prismatic/review_factory/routes.py"], depends_on=["T1"]),
    ]

    waves = partition_topological_waves(tasks)
    assert len(waves) == 2
    assert waves[0].tasks[0].task_id == "T1"
    assert waves[1].tasks[0].task_id == "T2"
