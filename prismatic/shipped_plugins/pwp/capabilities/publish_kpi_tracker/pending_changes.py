"""pending_changes — surface unfinished provisioning state on the dashboard.

Phase 3: when a `provision_site` run is missing credentials (GA4, GTM,
Vercel token, etc.), the affected steps are recorded as `_soft_failure: True`
but the run itself completes through to `migrate_kpi`. The user can then
come back later, set the missing credential, and re-run those steps —
but until then, the dashboard should remind them what's outstanding.

This module reads all `*.json` state files from a provision directory
(default: `/tmp/pwp-provisioning/`) and produces a list of
`PendingChange` records that the dashboard's index page can render.

A PendingChange is one of:
  - kind="soft_failed_step"     — a credential-gated step is recorded
                                   as failed; user should configure
                                   the missing credential.
  - kind="unmerged_run"         — provision_state.overall_status == "complete"
                                   but the canonical sites.json appendix
                                   doesn't yet include the domain.
                                   (Indicates an in-progress migration
                                   that needs `migrate --merge`.)
  - kind="failed_run"           — provision_state.overall_status == "failed"
                                   and not yet retried.

Each record carries the human-readable "what to do next" hint so the
dashboard can show it directly without the user needing to open logs.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_PROVISION_DIR = Path(
    os.environ.get("PWP_PROVISION_STATE_DIR", "/tmp/pwp-provisioning")
)


@dataclass(frozen=True)
class PendingChange:
    """A single outstanding item the user needs to address."""
    domain: str
    kind: str  # 'soft_failed_step' | 'unmerged_run' | 'failed_run'
    summary: str
    detail: str
    next_action: str

    def to_dict(self) -> dict[str, str]:
        return {
            "domain": self.domain,
            "kind": self.kind,
            "summary": self.summary,
            "detail": self.detail,
            "next_action": self.next_action,
        }


def _read_state(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _find_all_soft_failed_steps(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every step whose output dict contains `_soft_failure: True`."""
    out = []
    for step in state.get("steps", []):
        step_output = step.get("output") or {}
        if step_output.get("_soft_failure") is True:
            out.append(step)
    return out


def _soft_step_hint(step_name: str) -> str:
    """Return a 'next action' hint for a given soft-failed step name."""
    hints = {
        "ga4_property": (
            "Set GOOGLE_SA_JSON (path to service account JSON key with "
            "analytics.edit scope), set GA4_ACCOUNT_ID env var, then "
            "re-run: pwp-kpi-tracker provision --domain <domain> "
            "--steps ga4_property"
        ),
        "gtm_container": (
            "Set GOOGLE_SA_JSON (path to service account JSON key with "
            "tagmanager.edit scope), set GTM_ACCOUNT_ID env var, then "
            "re-run: pwp-kpi-tracker provision --domain <domain> "
            "--steps gtm_container"
        ),
        "vercel_project": (
            "Set VERCEL_TOKEN (personal access token from "
            "https://vercel.com/account/tokens), then re-run: "
            "pwp-kpi-tracker provision --domain <domain> "
            "--steps vercel_project"
        ),
    }
    return hints.get(
        step_name,
        f"Check the step's error message and configure the missing "
        f"credential, then re-run with --steps {step_name}",
    )


def scan_provision_state(
    provision_dir: Path | None = None,
    *,
    canonical_sites_path: Path | None = None,
) -> list[PendingChange]:
    """Scan `provision_dir` for `*.json` state files and return a list of
    PendingChange records.

    Args:
      provision_dir: where provision state files live
                      (default: /tmp/pwp-provisioning).
      canonical_sites_path: optional path to the live sites.json (used
                      to detect "unmerged_run" cases). If None, we skip
                      the unmerged_run detection (safer for tests).
    """
    if provision_dir is None:
        provision_dir = DEFAULT_PROVISION_DIR
    if not provision_dir.exists():
        return []

    # Optionally read the canonical sites.json to detect unmerged runs.
    canonical_sites: dict[str, Any] = {}
    if canonical_sites_path and canonical_sites_path.exists():
        try:
            canonical_sites = json.loads(
                canonical_sites_path.read_text(encoding="utf-8")
            )
        except Exception:
            canonical_sites = {}

    out: list[PendingChange] = []
    for state_path in sorted(provision_dir.glob("*.json")):
        state = _read_state(state_path)
        if state is None:
            continue
        domain = state.get("domain") or state_path.stem
        overall = state.get("overall_status", "unknown")

        # Failed runs.
        if overall == "failed":
            err = next(
                (s.get("error") for s in state.get("steps", []) if s.get("error")),
                None,
            )
            err_excerpt = (err or "unknown error")[:120]
            out.append(
                PendingChange(
                    domain=domain,
                    kind="failed_run",
                    summary=f"provision_site run failed: {overall}",
                    detail=err_excerpt,
                    next_action=(
                        f"Inspect the persisted state at {state_path} "
                        "and re-run with --resume to retry the failed steps."
                    ),
                )
            )
            continue

        # Soft-failed steps inside an otherwise-complete run. Each
        # soft-failed step surfaces as its own PendingChange so the user
        # sees every outstanding task, not just the first one.
        for soft_step in _find_all_soft_failed_steps(state):
            step_name = soft_step.get("name", "?")
            out.append(
                PendingChange(
                    domain=domain,
                    kind="soft_failed_step",
                    summary=(
                        f"{step_name} soft-failed — credential missing "
                        f"or step unsupported"
                    ),
                    detail=(soft_step.get("error") or "")[:200],
                    next_action=_soft_step_hint(step_name),
                )
            )

        # Unmerged runs (state is complete, but domain isn't in
        # the canonical sites.json). Skipped when canonical_sites
        # wasn't provided.
        if overall == "complete" and canonical_sites and isinstance(canonical_sites, dict):
            if "sites" in canonical_sites and isinstance(canonical_sites["sites"], list):
                in_canonical = any(
                    isinstance(s, dict) and s.get("domain") == domain
                    for s in canonical_sites["sites"]
                )
                if not in_canonical:
                    out.append(
                        PendingChange(
                            domain=domain,
                            kind="unmerged_run",
                            summary=(
                                f"provision_state complete for {domain} but "
                                "not yet in canonical sites.json"
                            ),
                            detail=(
                                "The provision run finished cleanly and "
                                "the sites.json appendix was written; the "
                                "canonical registry just hasn't been "
                                "merged yet."
                            ),
                            next_action=(
                                "Run `pwp-kpi-tracker operator_cli.py "
                                "migrate --merge` to fold the appendix "
                                "into the canonical sites.json."
                            ),
                        )
                    )

    return out


def render_pending_changes_html(changes: list[PendingChange]) -> str:
    """Render the pending changes panel as HTML (or empty string when none)."""
    if not changes:
        return ""
    rows = "".join(
        f"""<li>
            <strong>{_esc(c.domain)}</strong>
            <span class="pwp-pending-kind pwp-pending-{_esc(c.kind)}">{_esc(c.kind)}</span>
            <p>{_esc(c.summary)}</p>
            {f'<pre class="pwp-pending-detail">{_esc(c.detail)}</pre>' if c.detail else ''}
            <p class="pwp-pending-action">→ {_esc(c.next_action)}</p>
          </li>"""
        for c in changes
    )
    return f"""
<section class="pwp-pending-changes">
  <h2>Pending Changes ({len(changes)})</h2>
  <p class="muted">These provisioning tasks are waiting for you. They won't block the dashboard, but the affected steps won't run until they're resolved.</p>
  <ul>{rows}</ul>
</section>"""


def _esc(s: Any) -> str:
    """HTML-escape helper (avoid pulling html module for one call)."""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
