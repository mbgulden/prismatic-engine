"""funnel_config — PWP capability for the 'Configure website KPIs' flow.

This is the entry point for the dashboard's modal form. The dashboard
posts a JSON form to the funnel_config dispatcher; we:

  1. validate the form against the JSON Schema (form_version 1)
  2. look up the parent epic (PE-KPI-FUNNEL) in Linear
  3. dedupe by site_slug (existing child task for this site → update;
     otherwise → create new)
  4. attach labels: plugin:pwp, prismatic-engine, type:task, agent:ned,
     pipeline:dashboard-ui, plus a kind:init / kind:refinement tag
  5. attach the full site context (existing kpi-collections.json snapshot,
     provision_state.json, prior funnels) as the issue description body
  6. write a local submission log to /tmp/pwp-provisioning/funnel-config/<site>.json
  7. return the LinearIssue to the caller

Edit-funnel flow:
  When the form has a `kind: "refinement"` field (set by the dashboard
  when the user clicks 'Edit funnel' on an already-configured site), the
  dispatcher:
   - creates a NEW Linear task, parented under the prior init task
   - labels it with kind:refinement
   - the description carries the diff between the prior submission and
     the current one

Multi-tenant:
  The form accepts an optional `tenant_id`; if present, the dispatcher
  adds a `tenant:<id>` label and looks up the tenant's email/Linear-team
  for routing. Without tenant_id, defaults to the single configured
  tenant (growthwebdev).
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .linear_client import (
    CreateIssueInput,
    LinearClient,
    LinearIssue,
)
from .types import ProvisionRun


# --- Constants ------------------------------------------------------------

# Default team for the GrowthWebDev workspace. Multi-tenant overrides
# would look up a different team id; for now we hardcode the only team.
DEFAULT_TEAM_ID = "b6fb2651-5a1f-4714-9bcd-9eb6e759ffef"

# Parent epic identifier (GRO-4356 from the Phase 4 epic). The dispatcher
# finds the parent by searching for issues with the EPIC_TITLE_TITLE label
# fragment, so we don't hardcode the identifier.
EPIC_TITLE_FRAGMENT = "PE-KPI-FUNNEL"

# Label fragments (resolved via LinearClient.list_issues_by_label).
LABEL_PLUGIN_PWP = "plugin:pwp"
LABEL_PRISMATIC_ENGINE = "prismatic-engine"
LABEL_AGENT_NED = "agent:ned"
LABEL_PIPELINE_DASHBOARD = "pipeline:dashboard-ui"
LABEL_TYPE_TASK = "type:task"

# Where submission logs are persisted.
DEFAULT_LOG_DIR = Path(
    os.environ.get("PWP_FUNNEL_CONFIG_DIR", "/tmp/pwp-provisioning/funnel-config")
)

# JSON Schema (form_version 1). Kept inline because it's tiny; could be
# extracted to a separate file later if it grows.
FORM_SCHEMA_V1 = {
    "type": "object",
    "required": ["site_slug", "site_domain", "form_version", "context"],
    "properties": {
        "site_slug": {"type": "string", "pattern": r"^[a-z0-9-]+$"},
        "site_domain": {"type": "string"},
        "form_version": {"type": "integer", "enum": [1]},
        "kind": {"type": "string", "enum": ["init", "refinement"]},
        "owner": {"type": "string"},
        "tenant_id": {"type": "string"},
        "parent_linear_issue_id": {
            "type": "string",
            "description": "Set automatically by the dispatcher for refinements.",
        },
        "context": {
            "type": "object",
            "required": ["primary_goal"],
            "properties": {
                "primary_goal": {"type": "string", "minLength": 1},
                "funnel_ideas": {"type": "string"},
                "traffic_patterns": {"type": "string"},
                "data_sources": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["stripe", "zapier", "telegram", "internal", "vercel", "cloudflare"],
                    },
                },
                "platform": {"type": "string"},
                "tenant_id": {"type": "string"},
                "notes": {"type": "string"},
            },
        },
        "stripe_credentials": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string"},
                "restricted_key_name": {"type": "string"},
                "note": {
                    "type": "string",
                    "description": "We never store the key here; user submits via env var or separate auth flow.",
                },
            },
        },
        "zapier_webhook": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "format": "uri"},
                "schema_hint": {"type": "string"},
                "description": {"type": "string"},
            },
        },
    },
}


# --- Validation -----------------------------------------------------------

class FunnelConfigError(ValueError):
    """Raised when the form submission is invalid."""
    def __init__(self, message: str, *, errors: Optional[List[str]] = None):
        super().__init__(message)
        self.errors = errors or []


def validate_form(form: Dict[str, Any]) -> List[str]:
    """Lightweight validator for FORM_SCHEMA_V1 (no jsonschema dependency).

    Returns a list of error strings (empty list = valid). We could swap
    in `jsonschema` later; the simple key+type checks below are enough
    for Phase 4 and avoid a runtime dep.
    """
    errors: List[str] = []

    for required in ("site_slug", "site_domain", "form_version", "context"):
        if required not in form:
            errors.append(f"missing required field: {required}")

    if "site_slug" in form and not re.match(r"^[a-z0-9-]+$", str(form["site_slug"])):
        errors.append(f"site_slug must match ^[a-z0-9-]+$: got {form['site_slug']!r}")

    if form.get("form_version") != 1:
        errors.append(f"form_version must be 1, got {form.get('form_version')!r}")

    kind = form.get("kind", "init")
    if kind not in ("init", "refinement"):
        errors.append(f"kind must be 'init' or 'refinement', got {kind!r}")

    context = form.get("context") or {}
    if not isinstance(context, dict):
        errors.append(f"context must be an object, got {type(context).__name__}")
    else:
        if not context.get("primary_goal"):
            errors.append("context.primary_goal is required and must be non-empty")

    return errors


# --- Submission log -------------------------------------------------------

@dataclass
class FunnelConfigSubmission:
    """A persisted record of a funnel_config submission.

    Stored at /tmp/pwp-provisioning/funnel-config/<site_slug>.json.
    Contains the original form, the Linear task id/url, and a list of
    all refinements.
    """
    site_slug: str
    site_domain: str
    form: Dict[str, Any]
    linear_issue_id: str = ""
    linear_issue_identifier: str = ""
    linear_issue_url: str = ""
    state: str = "submitted"
    submitted_at: str = ""
    refinements: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "site_slug": self.site_slug,
            "site_domain": self.site_domain,
            "form": self.form,
            "linear_issue_id": self.linear_issue_id,
            "linear_issue_identifier": self.linear_issue_identifier,
            "linear_issue_url": self.linear_issue_url,
            "state": self.state,
            "submitted_at": self.submitted_at,
            "refinements": list(self.refinements),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FunnelConfigSubmission":
        return cls(
            site_slug=data["site_slug"],
            site_domain=data["site_domain"],
            form=data["form"],
            linear_issue_id=data.get("linear_issue_id", ""),
            linear_issue_identifier=data.get("linear_issue_identifier", ""),
            linear_issue_url=data.get("linear_issue_url", ""),
            state=data.get("state", "submitted"),
            submitted_at=data.get("submitted_at", ""),
            refinements=list(data.get("refinements", [])),
        )

    def save(self, log_dir: Path) -> Path:
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"{self.site_slug}.json"
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, site_slug: str, log_dir: Optional[Path] = None) -> Optional["FunnelConfigSubmission"]:
        log_dir = log_dir or DEFAULT_LOG_DIR
        path = log_dir / f"{site_slug}.json"
        if not path.exists():
            return None
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


# --- Issue title helpers --------------------------------------------------

def build_issue_title(form: Dict[str, Any]) -> str:
    """Build a stable issue title for the funnel-config task.

    Format: `[PE-KPI-FUNNEL] <kind> KPIs for <domain>`
    Used by `find_issue_by_title` for dedup.
    """
    kind = form.get("kind", "init")
    domain = form["site_domain"]
    verb = "Configure" if kind == "init" else "Refine"
    return f"[PE-KPI-FUNNEL] {verb} KPIs for {domain}"


def build_issue_description(
    form: Dict[str, Any],
    *,
    site_context: Optional[Dict[str, Any]] = None,
) -> str:
    """Build the markdown description body for the Linear issue.

    Includes:
      - site context (from the form's `context`)
      - data sources the user picked
      - tenant info (if provided)
      - the existing kpi-collections.json snapshot (if passed)
      - the existing provision_state.json (if passed)
      - step-by-step instructions for the assigned agent
    """
    parts: List[str] = []

    parts.append("## Funnel configuration request")
    parts.append("")
    parts.append(f"- **Site slug:** `{form['site_slug']}`")
    parts.append(f"- **Site domain:** `{form['site_domain']}`")
    if form.get("owner"):
        parts.append(f"- **Owner:** {form['owner']}")
    if form.get("tenant_id"):
        parts.append(f"- **Tenant:** `{form['tenant_id']}`")
    parts.append(f"- **Kind:** `{form.get('kind', 'init')}`")
    parts.append("")

    context = form.get("context", {})
    parts.append("## User-provided context")
    parts.append("")
    if context.get("primary_goal"):
        parts.append(f"**Primary goal:** {context['primary_goal']}")
        parts.append("")
    if context.get("funnel_ideas"):
        parts.append(f"**Funnel ideas:**\n\n{context['funnel_ideas']}\n")
    if context.get("traffic_patterns"):
        parts.append(f"**Traffic patterns:**\n\n{context['traffic_patterns']}\n")
    if context.get("data_sources"):
        parts.append(f"**Data sources:** {', '.join(context['data_sources'])}")
        parts.append("")
    if context.get("platform"):
        parts.append(f"**Detected platform:** {context['platform']}")
        parts.append("")
    if context.get("notes"):
        parts.append(f"**Notes:** {context['notes']}\n")

    # Optional extra context (kpi-collections snapshot, provision state)
    if site_context:
        if "kpi_collections" in site_context:
            parts.append("## Current `kpi-collections.json` snapshot")
            parts.append("")
            parts.append("```json")
            parts.append(json.dumps(site_context["kpi_collections"], indent=2))
            parts.append("```")
            parts.append("")
            # Extract github context if present so the agent can refer
            # the user to the right repo + commit.
            gh_ctx = (
                site_context.get("kpi_collections", {})
                .get("external_sources", {})
                .get("github", {})
            )
            if gh_ctx:
                parts.append("## GitHub repo context")
                parts.append("")
                repo = gh_ctx.get("repo", "<unknown>")
                html_url = gh_ctx.get("html_url", "")
                parts.append(f"- **Repo:** `{repo}`"
                             + (f"  ([open]({html_url}))" if html_url else ""))
                parts.append(f"- **Default branch:** `{gh_ctx.get('default_branch')}`"
                             + (f"  (current: `{gh_ctx.get('branch')}`)" if gh_ctx.get("branch") and gh_ctx.get("branch") != gh_ctx.get("default_branch") else ""))
                if gh_ctx.get("head_sha"):
                    head_url = gh_ctx.get("head_url", "")
                    parts.append(
                        f"- **HEAD:** `{gh_ctx['head_sha'][:12]}`"
                        + (f"  ([open]({head_url}))" if head_url else "")
                    )
                if gh_ctx.get("head_message"):
                    parts.append(f"- **HEAD message:** {gh_ctx['head_message']}")
                if gh_ctx.get("head_author"):
                    parts.append(f"- **HEAD author:** {gh_ctx['head_author']}")
                if gh_ctx.get("permissions"):
                    perms = ", ".join(
                        k for k, v in gh_ctx["permissions"].items() if v
                    )
                    if perms:
                        parts.append(f"- **Token permissions:** {perms}")
                parts.append("")
                # Hint about how to look up package.json / wrangler.toml /
                # vercel.json via the GitHubClient.
                parts.append(
                    "The agent can read files from this repo via the "
                    "`GitHubClient.get_file(owner, repo, path)` client "
                    "registered in `provision_site/github_client.py`."
                )
                parts.append("")
        if "provision_state" in site_context:
            parts.append("## Current `provision_state` summary")
            parts.append("")
            parts.append("```json")
            parts.append(json.dumps(site_context["provision_state"], indent=2))
            parts.append("```")
            parts.append("")

    parts.append("## Agent instructions")
    parts.append("")
    parts.append(
        "You are the **PE-KPI-FUNNEL** agent. Your job is to:\n"
        "\n"
        "1. Read the user's primary goal + funnel ideas from above.\n"
        "2. Examine the existing `kpi-collections.json` snapshot to see "
        "what's already configured.\n"
        "3. Call the `build_kpi_funnels` skill (spec at "
        "`provision_site/skills/build_kpi_funnels.md`) to plan the audit + "
        "funnel creation.\n"
        "4. Use the existing GA4 / GTM / GSC clients (in "
        "`provision_site/`) to actually create the events and tags.\n"
        "5. Update `kpi-collections.json` for the site with the new "
        "funnel plan.\n"
        "6. Post a final comment to this issue with the summary of what "
        "you did, then mark it Done.\n"
        "\n"
        "If you hit a credential gap (e.g. no `GOOGLE_SA_JSON` set), "
        "stop, document the gap in a comment, and leave the task in "
        "`In Progress` (don't move to Done) so a human can resolve the "
        "credential and re-trigger the pipeline."
    )

    return "\n".join(parts)


# --- Linear label lookup --------------------------------------------------

# Cache the team's label IDs so we don't refetch on every dispatch.
_LABEL_CACHE: Dict[str, str] = {}


def _resolve_label_id(client: LinearClient, team_id: str, label_name: str) -> Optional[str]:
    """Resolve a label name to its Linear id by listing the team's labels.

    We don't cache across calls (the cache would need invalidation); the
    call is cheap enough.
    """
    if label_name in _LABEL_CACHE:
        return _LABEL_CACHE[label_name]
    query = """
query TeamLabels($teamId: String!) {
  team(id: $teamId) {
    labels { nodes { id name } }
  }
}
"""
    try:
        data = client._request(query, {"teamId": team_id})
    except Exception:
        return None
    labels = data.get("team", {}).get("labels", {}).get("nodes", [])
    for lab in labels:
        if lab["name"] == label_name:
            _LABEL_CACHE[label_name] = lab["id"]
            return lab["id"]
    return None


# --- Epic lookup ----------------------------------------------------------

def find_parent_epic(client: LinearClient, team_id: str) -> Optional[LinearIssue]:
    """Find the PE-KPI-FUNNEL parent epic.

    Returns the first issue whose title contains the EPIC_TITLE_FRAGMENT
    and has the type:epic label. Returns None if the epic doesn't exist
    yet (callers should treat this as a misconfiguration and surface a
    clear error to the user).
    """
    issues = client.list_issues_by_label(team_id=team_id, label_name="type:epic")
    for issue in issues:
        if EPIC_TITLE_FRAGMENT in issue.title:
            return issue
    return None


# --- Main dispatch --------------------------------------------------------

@dataclass
class DispatchResult:
    """The result of dispatching a funnel_config form.

    Attributes:
      submission: The persisted submission record.
      linear_issue: The created (or updated) Linear issue.
      created: True if a new issue was created, False if an existing one
               was updated.
      epic_id: The parent epic's Linear id.
    """
    submission: FunnelConfigSubmission
    linear_issue: LinearIssue
    created: bool
    epic_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "submission": self.submission.to_dict(),
            "linear_issue": self.linear_issue.to_dict(),
            "created": self.created,
            "epic_id": self.epic_id,
        }


def dispatch(
    form: Dict[str, Any],
    *,
    client: Optional[LinearClient] = None,
    team_id: str = DEFAULT_TEAM_ID,
    log_dir: Optional[Path] = None,
    site_context: Optional[Dict[str, Any]] = None,
    submission: Optional[FunnelConfigSubmission] = None,
) -> DispatchResult:
    """Dispatch a funnel_config form to Linear.

    Steps:
      1. Validate the form.
      2. Find the parent epic.
      3. Dedupe (search for an existing issue with the same title).
      4. Build the issue title + description.
      5. Create or update the issue.
      6. Persist the submission log.
      7. Return the result.

    Args:
      form: The validated form dict (form_version 1).
      client: An optional LinearClient (for tests); constructed from env
              if None.
      team_id: Linear team id; defaults to GrowthWebDev.
      log_dir: Where to write the submission log; defaults to
               /tmp/pwp-provisioning/funnel-config/.
      site_context: Optional dict with 'kpi_collections' and
                    'provision_state' subkeys — embedded in the issue body.
      submission: An optional pre-existing submission record (for the
                  refinement flow). If provided and `form['kind'] ==
                  'refinement'`, the refinement is appended to it.

    Returns:
      DispatchResult with the submission record and Linear issue.
    """
    log_dir = log_dir or DEFAULT_LOG_DIR

    # 1. Validate
    errors = validate_form(form)
    if errors:
        raise FunnelConfigError(
            f"Invalid funnel_config form: {'; '.join(errors)}",
            errors=errors,
        )

    if client is None:
        client = LinearClient.from_env()

    # 2. Find the parent epic
    epic = find_parent_epic(client, team_id)
    if epic is None:
        raise FunnelConfigError(
            f"Could not find the {EPIC_TITLE_FRAGMENT} parent epic in team "
            f"{team_id!r}. Run `prismatic-create-epic` first to bootstrap."
        )

    # 3. Dedupe by title (within the parent epic)
    title = build_issue_title(form)
    existing = client.find_issue_by_title(
        team_id=team_id, title=title, parent_id=epic.id,
    )

    # 4. Build description
    description = build_issue_description(form, site_context=site_context)

    # 5. Resolve label ids
    label_ids = []
    for label_name in (
        LABEL_PLUGIN_PWP,
        LABEL_PRISMATIC_ENGINE,
        LABEL_TYPE_TASK,
        LABEL_AGENT_NED,
        LABEL_PIPELINE_DASHBOARD,
    ):
        lab_id = _resolve_label_id(client, team_id, label_name)
        if lab_id:
            label_ids.append(lab_id)
    # Tenant label (optional)
    tenant_id = form.get("tenant_id")
    if tenant_id:
        tenant_label = f"tenant:{tenant_id}"
        lab_id = _resolve_label_id(client, team_id, tenant_label)
        if lab_id:
            label_ids.append(lab_id)

    # 6. Create or update
    if existing is not None:
        # Update: post a comment + leave issue state alone (PE will pick up).
        client.add_comment(
            existing.id,
            f"Funnel configuration re-submitted for `{form['site_slug']}` "
            f"on {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}.\n\n"
            f"**Latest context:**\n\n{form.get('context', {}).get('primary_goal', '')}",
        )
        issue = existing
        created = False
    else:
        create_inp = CreateIssueInput(
            team_id=team_id,
            title=title,
            description=description,
            parent_id=epic.id,
            label_ids=label_ids,
            priority=2,  # High
        )
        issue = client.create_issue(create_inp)
        created = True

    # 7. Persist submission
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if submission is None:
        submission = FunnelConfigSubmission.load(form["site_slug"], log_dir) or \
            FunnelConfigSubmission(
                site_slug=form["site_slug"],
                site_domain=form["site_domain"],
                form=form,
                submitted_at=now,
            )
    if form.get("kind") == "refinement":
        submission.refinements.append({
            "form": form,
            "linear_issue_id": issue.id,
            "submitted_at": now,
        })
    else:
        # init replaces any prior form
        submission.form = form
        submission.submitted_at = now
    submission.linear_issue_id = issue.id
    submission.linear_issue_identifier = issue.identifier
    submission.linear_issue_url = issue.url
    submission.state = "submitted"
    submission.save(log_dir)

    return DispatchResult(
        submission=submission,
        linear_issue=issue,
        created=created,
        epic_id=epic.id,
    )
