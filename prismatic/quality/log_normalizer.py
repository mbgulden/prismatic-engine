"""prismatic.quality.log_normalizer — Deterministic Log Digest (DLD) & Replay Engine.

Normalizes raw test execution output streams by stripping non-deterministic wall-clock timing,
memory pointer addresses, and platform-specific path separators to produce 100% byte-identical
SHA-256 verification receipts.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Regex patterns for Deterministic Log Digest (DLD) normalization
TIMING_RE = re.compile(r"in \d+(?:\.\d+)?s(?: \(\d+:\d+:\d+\))?", re.IGNORECASE)
HEX_PTR_RE = re.compile(r"0x[0-9a-fA-F]{8,16}")
PATH_WIN_SEP_RE = re.compile(r"\\")


@dataclass
class PacketReplayResult:
    marker: str
    status: str
    packet_id: str
    candidate_head: str
    candidate_tree: str
    claimed_dld_sha256: str
    recomputed_dld_sha256: str
    dld_bytes_matched: bool
    clean_room_status: str

    def to_dict(self) -> dict:
        return {
            "marker": self.marker,
            "status": self.status,
            "packet_id": self.packet_id,
            "candidate_head": self.candidate_head,
            "candidate_tree": self.candidate_tree,
            "claimed_dld_sha256": self.claimed_dld_sha256,
            "recomputed_dld_sha256": self.recomputed_dld_sha256,
            "dld_bytes_matched": self.dld_bytes_matched,
            "clean_room_status": self.clean_room_status,
        }


def normalize_log(raw_text: str) -> str:
    """Normalize raw test execution log text for deterministic hashing.

    Strips:
      1. Execution timing lines (`passed in 7.15s` -> `passed in <DURATION>`)
      2. Memory pointer addresses (`0x00000265F4CAFFE0` -> `0x<ADDR>`)
      3. Windows backslashes (`\\` -> `/`)
    """
    text = TIMING_RE.sub("in <DURATION>", raw_text)
    text = HEX_PTR_RE.sub("0x<ADDR>", text)
    text = PATH_WIN_SEP_RE.sub("/", text)
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip() + "\n"


def compute_dld_sha256(raw_text: str) -> str:
    """Compute upper-case 64-hex SHA-256 hash over normalized log text."""
    norm = normalize_log(raw_text)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest().upper()


def verify_packet_file(packet_path: Path, repo_root: Optional[Path] = None) -> PacketReplayResult:
    """Replay verification packet by executing candidate head in clean-room and asserting DLD hash."""
    from prismatic.quality.clean_room import CleanRoomRunner

    if not packet_path.exists():
        raise FileNotFoundError(f"Packet file not found: {packet_path}")

    packet_data = json.loads(packet_path.read_text(encoding="utf-8"))
    claimed_sha = packet_data.get("LOG_SHA256", "").upper()
    packet_id = packet_data.get("ATTEMPT_ID", packet_data.get("TASK_ID", "unknown"))

    runner = CleanRoomRunner(repo_root=repo_root)
    clean_res = runner.run_clean_room_verification(cleanup=True)

    recomputed_sha = compute_dld_sha256(clean_res.raw_log)
    matched = (recomputed_sha == claimed_sha) or (clean_res.deterministic_log_sha256 == claimed_sha)

    status = "PASS" if matched and clean_res.status == "PASS" else "FAIL"
    marker = "PE_PACKET_REPLAY_VERIFIED_OK" if status == "PASS" else "PE_PACKET_REPLAY_VERIFICATION_FAILED"

    return PacketReplayResult(
        marker=marker,
        status=status,
        packet_id=packet_id,
        candidate_head=clean_res.candidate_head,
        candidate_tree=clean_res.candidate_tree,
        claimed_dld_sha256=claimed_sha,
        recomputed_dld_sha256=recomputed_sha,
        dld_bytes_matched=matched,
        clean_room_status=clean_res.status,
    )
