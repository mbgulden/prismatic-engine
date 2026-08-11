"""Tests for Deterministic Log Digest (DLD) Engine and Packet Replay."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from prismatic.quality.log_normalizer import (
    compute_dld_sha256,
    normalize_log,
    verify_packet_file,
)


def test_dld_normalizes_durations_ptrs_and_slashes() -> None:
    raw1 = "============================= 480 passed in 7.15s =============================\n<object at 0x00000265F4CAFFE0>\npath\\to\\file.py"
    raw2 = "============================= 480 passed in 6.92s =============================\n<object at 0x0000019A88BB3A10>\npath/to/file.py"

    norm1 = normalize_log(raw1)
    norm2 = normalize_log(raw2)

    assert norm1 == norm2
    assert compute_dld_sha256(raw1) == compute_dld_sha256(raw2)


def test_dld_hash_is_upper_hex_64_chars() -> None:
    raw = "test output line\n"
    dld_hash = compute_dld_sha256(raw)

    assert len(dld_hash) == 64
    assert dld_hash.isupper()


def test_verify_packet_file(tmp_path: Path) -> None:
    # Create mock packet
    packet = {
        "TASK_ID": "GRO-4361",
        "ATTEMPT_ID": "attempt-test-1",
        "LOG_SHA256": "AE473A0E9D07771D4CC50D6FBF946BFD51E7FF5BFA746C393A4CBD9EFA326E72",
    }
    packet_path = tmp_path / "result-packet.json"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")

    res = verify_packet_file(packet_path)
    assert res.packet_id == "attempt-test-1"
    assert len(res.candidate_head) == 40
