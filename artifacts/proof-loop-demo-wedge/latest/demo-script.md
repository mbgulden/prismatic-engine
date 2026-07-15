# 90-second demo script — Prismatic Proof Loop 2

## One-line positioning
Prismatic turns an issue label into governed agent execution with proof, not self-report.

## 0–15s — Pain
"Most agent systems can start work. The hard part is knowing what triggered it, which agent owned it, and whether the result was actually verified."

## 15–35s — Trigger
Show fixture `DEMO-90` with labels: `dispatch:ready, agent:agy, proof-loop:demo, github:fixture`.
Explain: `dispatch:ready` means work is eligible; `agent:agy` selects the bounded builder lane; the GitHub fixture branch is `feature/demo-90-proof-loop`.

## 35–60s — Governed routing and work
Run:

```bash
python3 scripts/proof_loop_demo_wedge.py --output-dir artifacts/proof-loop-demo-wedge/latest --clean
```

Point at `demo-evidence.json`: trigger, routing, bounded execution, verification, cleanup.

## 60–80s — Proof
Show verification status: `verified`.
The demo does not claim "done" until the verification command passes and the artifact names cleanup status.

## 80–90s — Ask
"If this handled one of your production queues, which approval or verification step would you want visible on your phone first?"
