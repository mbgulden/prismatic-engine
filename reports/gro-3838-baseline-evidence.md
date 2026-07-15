# GRO-3838 Baseline Evidence Ledger

- Branch: `feature/fred-gro3838-baseline-evidence`
- Base commit at verification start: `24cd6d84`
- Evidence directory: `/tmp/gro3838-fred-evidence-20260715T193925Z`
- Scope: credential-free public launch/security/release/dashboard/plugin/PWP baseline after lane-safe Fred script fix.
- Boundary: ad-hoc targeted baseline proof, not canonical full suite green.

## Code change

`scripts/public_security_readiness_audit.py` now skips the local `worktrees/` directory during public secret scanning so archived/agent worktree fixtures cannot contaminate the public-readiness audit.

## Verification commands

| Evidence file | Expected marker | Result | Bytes | SHA256 prefix |
|---|---|---:|---:|---|
| `00-pycompile.txt` | `py_compile exit 0` | PASS | 18 | `22b4b90c901eeb37` |
| `01-public-security-readiness.txt` | `PUBLIC_SECURITY_READINESS_OK` | PASS | 373 | `9f311a6eda5980f6` |
| `02-release-smoke.txt` | `RELEASE_SMOKE_OK` | PASS | 1690 | `3d8ee6411287580a` |
| `03-release-check.txt` | `RELEASE_READINESS_OK` | PASS | 230 | `7a2e36b635bfd1ef` |
| `04-public-launch-smoke.txt` | `PUBLIC_LAUNCH_SMOKE_OK` | PASS | 1834 | `0c589bb09272dae8` |
| `05-dashboard-visual-qa.txt` | `DASHBOARD_VISUAL_QA_OK` | PASS | 469 | `7ff3839c623f0345` |
| `06-plugin-catalog.txt` | `"ready_count": 5` | PASS | 26939 | `840ef34e83025e8d` |
| `07-proof-loop-demo-wedge.txt` | `Verdict: PASS` | PASS | 918 | `e0585e2f46d78969` |

## Commands represented

```text
PYTHONPATH=. python3 -m py_compile scripts/public_security_readiness_audit.py scripts/public_launch_smoke.py scripts/release_smoke.py scripts/release_check.py
PYTHONPATH=. python3 scripts/public_security_readiness_audit.py
PYTHONPATH=. python3 scripts/release_smoke.py
PYTHONPATH=. python3 scripts/release_check.py
PYTHONPATH=. python3 scripts/public_launch_smoke.py
PYTHONPATH=. python3 scripts/dashboard_visual_qa.py
PYTHONPATH=. python3 scripts/plugin_architecture catalog
PYTHONPATH=. python3 scripts/proof_loop_demo_wedge.py
```

## Acceptance notes

- `PUBLIC_SECURITY_READINESS_OK` now passes with the script fix applied.
- `PUBLIC_LAUNCH_SMOKE_OK`, `RELEASE_SMOKE_OK`, `RELEASE_READINESS_OK`, and `DASHBOARD_VISUAL_QA_OK` passed in this branch.
- Plugin catalog reports `ready_count: 5`.
- PWP proof-loop demo reports `Verdict: PASS`.
- Generated demo files are not included in this PR; the report references the exact captured command outputs in `/tmp`.
