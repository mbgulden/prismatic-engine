# Distribution Release-Blocker Checklist — PyPI/GHCR Decision

Scope: Prismatic Engine first-user distribution readiness.

Parent epic: GRO-3589 — Distribution Readiness / First-User Gate
Child task: GRO-3593 — Publish release-blocker checklist for PyPI/GHCR decision

## Decision

**GO for first-user distribution gate.**

The current evidence supports a publishable first-user path for source/fresh-install validation.

This is **not** a claim that every production/operator integration is fully configured. Systemd is explicitly optional operator deployment, and hosted webhook integrations still require operator credentials/secrets.

## Evidence baseline

Verified ref:

```text
origin/main HEAD 9d8f6b61b639849181873d793ebf73b0a354c5b5
```

Primary gate:

```bash
python3 scripts/distribution_readiness_smoke.py
python3 scripts/distribution_readiness_smoke.py --fresh-install
```

Fresh verification artifacts:

| Scope | Verifier | Result |
|---|---|---:|
| Epic 1 full readiness gate | `/tmp/hermes-verify-epic1-distribution-m78r9tye.py` | PASS, cleaned |
| Fresh clone/install + CLI entrypoints | `/tmp/hermes-verify-gro3591-fresh-install-znkvapbh.py` | PASS, cleaned |
| Docker/systemd distribution gate | `/tmp/hermes-verify-gro3592-docker-systemd-h4gb6kcm.py` | PASS, cleaned |

Verification label: **ad hoc targeted verification**, not canonical/full-suite green.

## P0 release blockers

| Blocker | Status | Owner | Evidence |
|---|---:|---|---|
| Package metadata/license contradiction | Clear | Fred | `pyproject.toml` and `README.md` both declare AGPL-3.0-only; smoke metadata checks PASS |
| Missing required package data | Clear | Fred | `pyproject.toml` includes `skills/**/*`, `templates/**/*`, `config/**/*`; `prismatic/config/default_config.yaml` exists; smoke package-data checks PASS |
| README promises unimplemented first-user command | Clear | Fred | README quick start uses `python -m pip install .`, `prismatic --help`, `prismatic status`; fresh smoke validates install/help/import |
| Broken console entrypoints after fresh install | Clear | Fred | `scripts/distribution_readiness_smoke.py --fresh-install` validates all `[project.scripts]` help paths |
| Dockerfile references missing sources | Clear | Fred | Dockerfile has no broken `COPY config/`; smoke COPY source check PASS |
| Docker license metadata mismatch | Clear | Fred | Docker label and pyproject both AGPL-3.0-only; smoke check PASS |
| Systemd required for first-user operation | Clear | Fred | README states first-user path does **not** require systemd; systemd section is optional operator deployment |
| Michael-only `/home/ubuntu` path required for first-user operation | Clear | Fred | README first-user path avoids `/home/ubuntu`; smoke check PASS |

## P1 / operator caveats

| Caveat | Status | Owner | Evidence / next step |
|---|---:|---|---|
| Canonical full suite | Not claimed | Fred | This checklist only closes first-user distribution readiness with ad hoc targeted gates |
| Systemd service deployment | Operator-only | Fred | README gates systemd as optional; first-user path does not require it |
| Hosted Linear/GitHub webhooks | Operator-configured | Fred | Requires credentials/secrets; not a blocker for source/fresh-install gate |
| PyPI/GHCR actual publishing credentials | External release operation | Michael/Fred | Checklist says GO for readiness gate, not that credentials/release automation were exercised |

## Go / no-go rule

- **GO** when every P0 blocker above is `Clear` and the fresh-install smoke returns `PUBLISHABLE` with `failed_p0=0`.
- **NO-GO** if any P0 blocker regresses, if `scripts/distribution_readiness_smoke.py --fresh-install` fails, or if README/Docker/pyproject claims drift from implementation.

## Current go / no-go status

```text
GO: first-user distribution readiness gate is publishable.
P0 blockers: 0 open.
Verification scope: ad hoc targeted, not full-suite green.
```

## Required command before future publication

Before any PyPI/GHCR publication attempt, rerun:

```bash
python3 scripts/distribution_readiness_smoke.py --fresh-install
```

Expected release-gate output:

```json
{"fresh_install": true, "verdict": "PUBLISHABLE", "failed_p0": 0}
```
