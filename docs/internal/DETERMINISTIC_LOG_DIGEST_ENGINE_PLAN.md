# 📐 Architectural Specification: Deterministic Log Digest (DLD) & Replay Engine

**Engine Version**: Prismatic Engine v0.3.1  
**Feature Name**: Deterministic Log Digest Engine & Packet Replay CLI (`prismatic verify-packet`)  
**Task Identifier**: [GRO-4361](https://prismatic.growthwebdev.com/tab/tasks?issue=GRO-4361)  
**Status**: ARCHITECTURAL SPECIFICATION & PRODUCTION DESIGN  

---

## 1. Executive Summary & Design Rationale

When AI agents or automated CI test harnesses run test suites, the resulting output streams contain non-deterministic noise:
1. **Wall-Clock Duration Variance**: Execution timing lines (`passed in 7.15s` vs `passed in 6.92s`).
2. **Memory Pointer Hex Addresses**: Object representation strings (`<object at 0x00000265F4CAFFE0>`).
3. **Platform Path Separators**: Windows backslashes (`C:\Users\...`) vs Unix forward slashes (`/home/...`).

Because raw `Get-FileHash` SHA-256 digests change whenever any of these non-deterministic variables change, reviewers running the exact same test suite against identical code produce different SHA-256 log digests.

The **Deterministic Log Digest (DLD) Engine** introduces regex-driven stream normalization before hash computation, guaranteeing **100% byte-identical SHA-256 verification receipts** across any OS or reviewer workstation.

---

## 2. DLD Normalization Algorithm

```text
  Raw Test Log Output Stream
  │
  ├─▶ 1. Wall-Clock Duration Replacement:   `in \d+(?:\.\d+)?s` ──▶ `in <DURATION>`
  ├─▶ 2. Hex Memory Address Replacement:    `0x[0-9a-fA-F]{8,16}` ──▶ `0x<ADDR>`
  ├─▶ 3. Cross-Platform Path Normalization:  `\` ──▶ `/`
  └─▶ 4. Trailing Whitespace Stripping & Canonical LF Newline Termination
  │
  ▼
  Normalized Log Stream ──▶ SHA-256 Hash Computation ──▶ Deterministic Log Digest (DLD)
```

---

## 3. Packet Replay CLI Interface (`prismatic verify-packet`)

```bash
# Standard CLI packet replay invocation
prismatic verify-packet result-packet.json

# JSON payload output
prismatic verify-packet result-packet.json --json
```

### Machine-Readable Output Schema (`prismatic verify-packet --json`)
```json
{
  "marker": "PE_PACKET_REPLAY_VERIFIED_OK",
  "status": "PASS",
  "packet_id": "attempt-20260811-pwp-p3-v6",
  "candidate_head": "4ad21d3403ea7b56bd3937fa17ea189b26903063",
  "candidate_tree": "d06bd9f2913aaa983cabe947db2c1a62584a85df",
  "claimed_dld_sha256": "9F0C7F3A6A6AF7CC9328E4FE02C296A568B55CEB71E4C11DAFD2933CE7D387FC",
  "recomputed_dld_sha256": "9F0C7F3A6A6AF7CC9328E4FE02C296A568B55CEB71E4C11DAFD2933CE7D387FC",
  "dld_bytes_matched": true
}
```

---

## 4. Subsystem Components

1. **`prismatic/quality/log_normalizer.py`**: Core `DeterministicLogNormalizer` and `PacketReplayEngine`.
2. **`prismatic/cli/__init__.py`**: Mounts `verify-packet` subcommand.
3. **`tests/test_log_normalizer.py`**: Complete unit test suite.
