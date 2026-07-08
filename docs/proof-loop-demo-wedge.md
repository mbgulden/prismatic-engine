# Prismatic Proof Loop 2 — Linear/GitHub Automation Demo Wedge

## Goal

One externally understandable 90-second demo shows:

`Linear/GitHub trigger → governed agent route → bounded work product → verification verdict → cleanup status`

The demo must be replayable without production Linear, GitHub, AGY, or credentials.

## Run the fixture demo

From repo root:

```bash
python3 scripts/proof_loop_demo_wedge.py --output-dir artifacts/proof-loop-demo-wedge/latest --clean
```

Expected result:

```text
Verdict: PASS
Routed agent: agy
Artifacts: fixture-event.json, demo-evidence.json, demo-script.md, capture-checklist.md, three-user-feedback-package.md
```

## What the fixture represents

- Linear issue: `DEMO-90`
- Labels: `dispatch:ready`, `agent:agy`, `proof-loop:demo`, `github:fixture`
- GitHub repo fixture: `example/prismatic-demo-repo`
- GitHub branch fixture: `feature/demo-90-proof-loop`
- Verification command: `python3 -m py_compile demo_workspace/agent_output.py`

The script does **not** call real Linear, GitHub, AGY, or network endpoints. It creates a deterministic fixture and evidence bundle so the demo can be recorded or reviewed safely.

## Generated artifacts

| Artifact | Purpose |
|---|---|
| `fixture-event.json` | Replayable Linear/GitHub trigger fixture |
| `demo-evidence.json` | Timestamped trigger/routing/execution/verification/cleanup evidence |
| `demo-script.md` | 90-second founder/operator narrative |
| `capture-checklist.md` | Recording checklist with expected outputs |
| `three-user-feedback-package.md` | Message and prompts for three reviewer feedback test |
| `demo_workspace/agent_output.py` | Safe bounded agent-output fixture |

## 90-second narrative

1. **Pain:** Starting agents is easy; proving why they started and whether they are actually done is hard.
2. **Trigger:** A Linear issue receives `dispatch:ready` and `agent:agy`; GitHub fixture context points at the branch.
3. **Governed route:** Prismatic resolves the owning agent from labels and records the route.
4. **Bounded work:** The AGY fixture writes a tiny safe work product in the demo workspace.
5. **Verification:** The demo only passes when `py_compile` verifies the work product and the evidence includes cleanup status.
6. **Ask:** “If this handled one of your production queues, which approval or verification step would you want visible on your phone first?”

## Exit criterion mapping

| Epic 2 child | Evidence produced |
|---|---|
| Create 90-second demo script and capture checklist | `demo-script.md`, `capture-checklist.md` |
| Build safe demo fixture issue/event for webhook dispatch | `fixture-event.json` |
| Capture proof-loop demo output artifact | `demo-evidence.json` |
| Write demo narrative for operator/founder audience | `demo-script.md` |
| Package demo links for three-user feedback test | `three-user-feedback-package.md` |

## Verification scope

This demo is **ad hoc targeted fixture verification**, not canonical/full-suite green. It proves the demo wedge is reproducible and understandable without external side effects. It does not prove live Linear/GitHub webhook delivery or real AGY execution.

## Remaining live-integration blocker

Before calling the live integration done, run a separate credentialed/live smoke once the Linear API budget is healthy:

1. Create or select a non-production Linear issue.
2. Apply `dispatch:ready` and `agent:agy`.
3. Observe the gateway/dispatcher consuming the event.
4. Confirm evidence lands in the operator surface and Linear comment without exceeding shared budget.
