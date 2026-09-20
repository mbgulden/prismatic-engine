# Prompt4 assigned-agent packet gate

## Purpose

Prompt4 evaluates the staged Fred/AGY output-resilience proof for GRO-3952 and GRO-3954. It is a packet-state gate, not a raw OAuth check and not a Prompt5 unlock.

## Required agents

- Fred — required.
- AGY — required.
- George — manual verifier only. George is reported but is not a required dispatched-agent packet until a separate resolver/launcher task adds real George dispatch support.

## Packet resolution rule

For each agent, parse recognized compact packet markers from Linear comments and pair each marker with its nearest preceding `RESULT=` line. Order observations by `createdAt` and packet line position. Only the latest recognized packet for that agent is active.

A later valid PASS supersedes historical BLOCKED packets. Historical blockers remain counted and visible as `superseded_blocked`; they are not deleted. A newer blocker still wins and keeps the gate blocked.

Recognized Fred success markers include the explicit Prompt4 supersession marker, the canonical repair-queue marker, the verified PR-ready marker, and the original raw-output queue acceptance marker. AGY uses `AGY_PACKET_FIXTURES_REPAIR_HINTS_OK`.

## Status contract

- `COMPLETE` — latest Fred and AGY packets are PASS and neither has an active latest blocker.
- `BLOCKED_PACKET_PRESENT` — at least one required agent's latest recognized packet is blocked/failed.
- `WAITING_REQUIRED_PACKET` — a required agent has no active PASS packet.

`COMPLETE` does not mean Prompt5 is unlocked, production is deployed, PRs are merged, or the canonical suite is green.

## Reconciliation repair

`scripts/assigned_agent_result_writeback.py` must inspect terminal AGY launch rows when a durable output log exists. If a terminal run contains a valid packet but the marker comment was missed, it writes the packet once and emits the visible result event. If the marker already exists, reconciliation is idempotent. Historical terminal rows with no packet do not generate new duplicate blockers.

## Commands

```bash
python3 scripts/live_prompt4_agent_monitor.py
python3 -m pytest tests/test_prompt4_packet_gate.py tests/test_prompt4_result_writeback.py tests/test_assigned_agent_visible_stream.py -q
```

Expected fix marker: `GEORGE_AGY_PREFLIGHT_GATE_FIX_OK`.
