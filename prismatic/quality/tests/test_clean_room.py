"""Tests for the Ephemeral Clean-Room Execution Engine."""

from __future__ import annotations

from pathlib import Path
import pytest

from prismatic.quality.clean_room import (
    CleanRoomRunner,
    compute_deterministic_sha256,
    normalize_log_for_deterministic_hash,
)


def test_deterministic_log_digest_normalizes_timing_and_ptrs() -> None:
    raw1 = "============================= 480 passed in 7.85s =============================\n<object at 0x00000265F4CAFFE0>"
    raw2 = "============================= 480 passed in 6.92s =============================\n<object at 0x0000019A88BB3A10>"

    norm1 = normalize_log_for_deterministic_hash(raw1)
    norm2 = normalize_log_for_deterministic_hash(raw2)

    assert norm1 == norm2
    assert compute_deterministic_sha256(raw1) == compute_deterministic_sha256(raw2)


def test_clean_room_runner_extracts_shas() -> None:
    runner = CleanRoomRunner()
    head_sha = runner.get_head_sha()
    tree_sha = runner.get_tree_sha()

    assert len(head_sha) == 40
    assert len(tree_sha) == 40


def test_clean_room_spawns_and_cleans_up() -> None:
    runner = CleanRoomRunner()
    head_sha = runner.get_head_sha()

    worktree_dir = runner.spawn_worktree(head_sha)
    try:
        assert worktree_dir.exists()
        assert (worktree_dir / "pyproject.toml").exists()
    finally:
        runner.cleanup_worktree(worktree_dir)
        assert not worktree_dir.exists()
