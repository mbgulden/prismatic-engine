"""orchestrator — coordinates the steps of a provisioning run.

A provisioning run is a sequence of independent steps, each of which
must succeed (or be marked `skipped` for non-applicable reasons)
before the next step runs. The orchestrator:

  1. Reads the requested domain + owner from the input.
  2. Verifies domain ownership (DNS TXT challenge).
  3. Runs each step in order; records status + error per step.
  4. Writes the running state to `<publish_root>/provisioning/<domain>.json`
     after every step (so a UI can poll progress).
  5. Idempotent — re-running a successful step is a no-op.

The step list is intentionally a hardcoded list for Phase 1. Later
phases will make this dynamic (e.g. only run `gsc_verify` if the
owner opted into GSC).
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import domain_verifier
from . import steps as step_module
from .types import ProvisionRun, StepResult


# Phase 1 step order. Each step is a function (run, run_state) -> StepResult.
# Steps must NOT raise — they return StepResult with status="failed" + error.
STEP_NAMES: List[str] = [
    "verify_domain",
    "cloudflare_zone",
    "gsc_verify",
    "register_in_registry",
    "migrate_kpi",
]


def _resolve_publish_root(publish_root: Optional[Path]) -> Path:
    """Default to `<repo>/prismatic-publish/provisioning/` for run state."""
    if publish_root is not None:
        return publish_root
    return Path("/tmp/pwp-provisioning")


def _state_path_for(domain: str, publish_root: Path) -> Path:
    return publish_root / f"{domain}.json"


def _run_step(
    step_name: str,
    domain: str,
    owner: str,
    run: ProvisionRun,
    *,
    publish_root: Path,
    prior_outputs: Optional[Dict[str, Dict[str, Any]]] = None,
) -> StepResult:
    """Run a single step by name. Looks up the step fn in step_module.

    `prior_outputs` is a dict keyed by step name mapping to that step's
    last output (from a prior persisted run, when resuming). Step
    functions can read this to pick up state from a previous attempt
    (e.g. a challenge token issued before the user created the TXT
    record).
    """
    fn: Optional[Callable[..., StepResult]] = getattr(step_module, f"step_{step_name}", None)
    if fn is None:
        return StepResult(
            name=step_name,
            status="failed",
            error=f"step function step_{step_name} not found in steps module",
        )
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    result = StepResult(name=step_name, status="pending", started_at=started)
    try:
        out = fn(
            domain=domain,
            owner=owner,
            run=run,
            publish_root=publish_root,
            prior_outputs=prior_outputs or {},
        )
        # Step functions return either a StepResult (preferred) or a dict.
        if isinstance(out, StepResult):
            result = out
        elif isinstance(out, dict):
            result.status = out.get("status", "complete")
            result.output = out.get("output", {})
            if out.get("error"):
                result.error = out["error"]
        else:
            result.status = "complete"
    except Exception as exc:  # step functions shouldn't raise, but defensive
        result.status = "failed"
        result.error = repr(exc)
    result.finished_at = dt.datetime.now(dt.timezone.utc).isoformat()
    return result


def run(
    *,
    domain: str,
    owner: str,
    publish_root: Optional[Path] = None,
    resume: bool = True,
    step_filter: Optional[List[str]] = None,
) -> ProvisionRun:
    """Run the provisioning flow for `domain`.

    Args:
      domain: the bare domain (e.g. "example.com").
      owner: the email of the person who will own the site (recorded
        in the registry's site entry).
      publish_root: directory where run-state files land. Defaults
        to `/tmp/pwp-provisioning`.
      resume: if True and a prior run-state file exists, skip steps
        marked `complete`. Default True.
      step_filter: if not None, only run steps whose names are in this
        list. Useful for re-running a failed step in isolation.
    """
    publish_root = _resolve_publish_root(publish_root)
    publish_root.mkdir(parents=True, exist_ok=True)
    state_path = _state_path_for(domain, publish_root)

    started_at = dt.datetime.now(dt.timezone.utc).isoformat()

    # Load prior state if resuming.
    prior: Dict[str, Any] = {}
    if resume and state_path.exists():
        try:
            prior = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            prior = {}

    run_state = ProvisionRun(
        domain=domain,
        owner=owner,
        started_at=prior.get("started_at", started_at),
    )
    # Restore completed steps from prior state.
    prior_steps = {s["name"]: s for s in prior.get("steps", [])}
    for sname in STEP_NAMES:
        prior_step = prior_steps.get(sname)
        if prior_step and prior_step.get("status") == "complete":
            run_state.steps.append(StepResult(**prior_step))

    # Build prior_outputs: a dict mapping step_name -> last output dict
    # from the prior persisted run (if any). Steps use this to recover
    # state between attempts (e.g. reuse a challenge token across
    # attempts while the user creates the TXT record).
    prior_outputs: Dict[str, Dict[str, Any]] = {}
    for prior_step in prior.get("steps", []):
        if prior_step.get("status") != "complete" and prior_step.get("output"):
            prior_outputs[prior_step["name"]] = prior_step["output"]

    # Run the remaining steps.
    for sname in STEP_NAMES:
        if step_filter is not None and sname not in step_filter:
            continue
        # Skip if already complete (from resume).
        if any(s.name == sname and s.status == "complete" for s in run_state.steps):
            continue
        result = _run_step(
            sname, domain, owner, run_state,
            publish_root=publish_root,
            prior_outputs=prior_outputs,
        )
        run_state.steps.append(result)
        # Persist after every step so the UI can poll progress.
        state_path.write_text(
            json.dumps(run_state.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        # If a step fails, mark the overall run as failed and stop.
        if result.status == "failed":
            run_state.overall_status = "failed"
            run_state.finished_at = dt.datetime.now(dt.timezone.utc).isoformat()
            state_path.write_text(
                json.dumps(run_state.to_dict(), indent=2, sort_keys=True),
                encoding="utf-8",
            )
            return run_state

    # Domain verification is a precondition that must succeed.
    # If we got past verify_domain, we are at least ok.
    run_state.overall_status = "complete"
    run_state.finished_at = dt.datetime.now(dt.timezone.utc).isoformat()
    state_path.write_text(
        json.dumps(run_state.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return run_state


def status(domain: str, publish_root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Read the current run state for `domain`. Returns None if no run exists."""
    publish_root = _resolve_publish_root(publish_root)
    state_path = _state_path_for(domain, publish_root)
    if not state_path.exists():
        return None
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return None
