"""
Unit tests for Multi-Channel Ingestion Queue (Telegram, AGY CLI, Linear).
"""

from prismatic.ingestion_queue import (
    enqueue_multi_channel_task,
    queue_payload,
    queue_stats_payload,
)


def test_multi_channel_task_admission():
    # 1. Enqueue Telegram Task for Fred
    tg_row = enqueue_multi_channel_task(
        identifier="TG-101",
        channel="telegram",
        target_agent="fred",
        title="Telegram Task for Fred",
        affected_paths=["prismatic/orchestrator/status.json"],
    )
    assert tg_row["identifier"] == "TG-101"
    assert tg_row["routing_source"] == "telegram"
    assert tg_row["target_agent"] == "fred"

    # 2. Enqueue AGY CLI Task
    agy_row = enqueue_multi_channel_task(
        identifier="GRO-4203",
        channel="agy_cli",
        target_agent="agy",
        title="AGY Kernel Update",
        affected_paths=["prismatic/core/locking.py"],
    )
    assert agy_row["identifier"] == "GRO-4203"
    assert agy_row["routing_source"] == "agy_cli"
    assert agy_row["target_agent"] == "agy"

    # 3. Query queue payload and assert topological waves
    payload = queue_payload(limit=50)
    assert payload["total"] >= 2
    assert "topological_waves" in payload
    assert isinstance(payload["topological_waves"], list)
