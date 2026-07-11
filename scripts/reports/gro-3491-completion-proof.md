# GRO-3491 — Completion proof contract

## What changed

Local Prismatic task completion now requires durable proof. A launcher can no longer close a local task by returning a silent/truthy value alone.

## Contract

A successful terminal local-task status (`completed`, `complete`, `done`, `success`, or `succeeded`) must include at least one proof field in task metadata:

- `artifact_path`
- `result_path`
- `result_artifact`
- `completion_marker`
- `completion_proof`

If no proof is present, the status update raises `ValueError` and the dispatcher marks the local task `failed` with the error in metadata instead of treating silence as completion.

## Dispatcher behavior

- Truthy non-dict launcher result: dispatch acknowledgement only; task moves to `dispatched`.
- Dict launcher result with non-terminal status: stored in metadata; task moves to `dispatched`.
- Dict launcher result with terminal status plus proof: task moves to `completed`; artifact/marker metadata is stored.
- Dict launcher result with terminal status and no proof: task moves to `failed`; the run cannot close without proof.

## Verification

Targeted test module: `prismatic/test_local_task_completion_proof.py`

This test covers direct queue updates, artifact-path storage, dispatcher completion with proof, and dispatcher refusal of proofless completion.
