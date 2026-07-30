"""Tests for the funnel_config dispatcher (Phase 4).

Coverage:
  - validate_form (good form, missing fields, bad site_slug, bad form_version)
  - build_issue_title (init / refinement)
  - build_issue_description (embeds context, kpi_collections, provision_state)
  - FunnelConfigSubmission roundtrip + load
  - dispatch: dedupes by title, posts comment on resubmit, persists log
  - dispatch: creates new issue when none exists
  - dispatch: raises FunnelConfigError on missing epic
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from plugins.pwp.capabilities.provision_site.funnel_config import (
    DEFAULT_TEAM_ID,
    DispatchResult,
    EPIC_TITLE_FRAGMENT,
    FORM_SCHEMA_V1,
    FunnelConfigError,
    FunnelConfigSubmission,
    build_issue_description,
    build_issue_title,
    dispatch,
    find_parent_epic,
    validate_form,
)
from plugins.pwp.capabilities.provision_site.linear_client import (
    LinearClient,
    LinearIssue,
)


# --- helpers --------------------------------------------------------------

def _good_form(**overrides) -> dict:
    """Return a valid form with sensible defaults. Apply overrides last."""
    base = {
        "site_slug": "ezshare",
        "site_domain": "ezshare.systems",
        "form_version": 1,
        "kind": "init",
        "owner": "michael@growthwebdev.com",
        "tenant_id": "growthwebdev",
        "context": {
            "primary_goal": "Track booking funnel conversions",
            "funnel_ideas": "Step 1: Visit pricing. Step 2: Click book. Step 3: Complete booking.",
            "traffic_patterns": "Mostly organic search + direct",
            "data_sources": ["stripe", "zapier"],
            "platform": "vercel",
            "notes": "Use FareHarbor-style booking events.",
        },
    }
    base.update(overrides)
    return base


def _mock_client(
    *,
    epic: LinearIssue | None = None,
    existing_issue: LinearIssue | None = None,
    created_issue: LinearIssue | None = None,
) -> MagicMock:
    """Build a MagicMock that quacks like LinearClient but doesn't hit the
    real API. Each method returns a sensible default."""
    client = MagicMock(spec=LinearClient)
    if epic is None:
        epic = LinearIssue(
            id="epic-1",
            identifier="GRO-4356",
            title="[PE-KPI-FUNNEL] LLM-driven funnel config + Linear dispatch",
            url="https://linear.app/growthwebdev/issue/GRO-4356",
            state="Todo",
            state_id="s",
            state_type="unstarted",
            labels=["type:epic", "plugin:pwp"],
            team_id=DEFAULT_TEAM_ID,
            team_key="GRO",
        )
    # find_parent_epic
    client.list_issues_by_label.return_value = [epic] if epic else []
    # find_issue_by_title
    client.find_issue_by_title.return_value = existing_issue
    # create_issue
    if created_issue is None:
        created_issue = LinearIssue(
            id="new-1",
            identifier="GRO-9999",
            title="[PE-KPI-FUNNEL] Configure KPIs for ezshare.systems",
            url="https://linear.app/growthwebdev/issue/GRO-9999",
            state="Todo",
            state_id="s",
            state_type="unstarted",
            labels=["plugin:pwp"],
            parent_id=epic.id if epic else None,
            team_id=DEFAULT_TEAM_ID,
            team_key="GRO",
        )
    client.create_issue.return_value = created_issue
    # add_comment
    client.add_comment.return_value = {"comment": {"id": "c-1"}}
    # label lookup (used by _resolve_label_id)
    client._request.return_value = {
        "team": {
            "labels": {
                "nodes": [
                    {"id": "lab-pwp", "name": "plugin:pwp"},
                    {"id": "lab-pe", "name": "prismatic-engine"},
                    {"id": "lab-task", "name": "type:task"},
                    {"id": "lab-ned", "name": "agent:ned"},
                    {"id": "lab-dash", "name": "pipeline:dashboard-ui"},
                    {"id": "lab-tnt", "name": "tenant:growthwebdev"},
                ],
            },
        },
    }
    return client


# --- validate_form --------------------------------------------------------

def test_validate_form_passes_for_good_form() -> None:
    assert validate_form(_good_form()) == []


def test_validate_form_rejects_missing_site_slug() -> None:
    f = _good_form()
    del f["site_slug"]
    errors = validate_form(f)
    assert any("site_slug" in e for e in errors)


def test_validate_form_rejects_bad_site_slug() -> None:
    f = _good_form(site_slug="Has Spaces!")
    errors = validate_form(f)
    assert any("site_slug" in e for e in errors)


def test_validate_form_rejects_old_form_version() -> None:
    f = _good_form(form_version=2)
    errors = validate_form(f)
    assert any("form_version" in e for e in errors)


def test_validate_form_rejects_invalid_kind() -> None:
    f = _good_form(kind="draft")
    errors = validate_form(f)
    assert any("kind" in e for e in errors)


def test_validate_form_requires_primary_goal() -> None:
    f = _good_form()
    del f["context"]["primary_goal"]
    errors = validate_form(f)
    assert any("primary_goal" in e for e in errors)


# --- build_issue_title ----------------------------------------------------

def test_build_issue_title_init() -> None:
    title = build_issue_title(_good_form())
    assert "[PE-KPI-FUNNEL]" in title
    assert "Configure" in title
    assert "ezshare.systems" in title


def test_build_issue_title_refinement() -> None:
    f = _good_form(kind="refinement")
    title = build_issue_title(f)
    assert "Refine" in title
    assert "ezshare.systems" in title


# --- build_issue_description ----------------------------------------------

def test_build_issue_description_embeds_context() -> None:
    f = _good_form()
    desc = build_issue_description(f)
    assert "ezshare.systems" in desc
    assert "booking funnel" in desc.lower()
    assert "stripe" in desc
    assert "zapier" in desc
    assert "Tenant" in desc  # tenant_id rendered
    assert "Agent instructions" in desc


def test_build_issue_description_embeds_kpi_collections() -> None:
    f = _good_form()
    kpi = {"site_slug": "ezshare", "domain": "ezshare.systems", "metrics": []}
    desc = build_issue_description(
        f, site_context={"kpi_collections": kpi}
    )
    assert "kpi-collections" in desc
    # The full JSON should be inlined
    assert '"site_slug": "ezshare"' in desc or '"site_slug":"ezshare"' in desc


def test_build_issue_description_embeds_provision_state() -> None:
    f = _good_form()
    desc = build_issue_description(
        f, site_context={"provision_state": {"domain": "ezshare.systems"}}
    )
    assert "provision_state" in desc
    assert "ezshare.systems" in desc


def test_build_issue_description_embeds_github_context() -> None:
    """When kpi_collections has external_sources.github, the issue body
    must include a 'GitHub repo context' section with repo, branch, HEAD,
    HEAD message, HEAD author, and a link to GitHubClient.get_file()."""
    f = _good_form()
    kpi = {
        "site_slug": "ezshare",
        "domain": "ezshare.systems",
        "metrics": [],
        "external_sources": {
            "github": {
                "repo": "mbgulden/EZShare",
                "branch": "master",
                "default_branch": "master",
                "head_sha": "bf8d05e96c8dc369e7552e6dc822706988392aab",
                "head_url": "https://github.com/mbgulden/EZShare/commit/bf8d05e96c8dc369e7552e6dc822706988392aab",
                "head_message": "feat: bypass resend rigid custom domain requirements",
                "head_author": "Sovereign AI",
                "html_url": "https://github.com/mbgulden/EZShare",
                "permissions": {"push": True, "admin": True, "maintain": True},
            },
        },
    }
    desc = build_issue_description(
        f, site_context={"kpi_collections": kpi}
    )
    assert "## GitHub repo context" in desc
    assert "`mbgulden/EZShare`" in desc
    assert "https://github.com/mbgulden/EZShare" in desc
    assert "`bf8d05e96c8d`" in desc  # short SHA
    assert "feat: bypass resend" in desc
    assert "Sovereign AI" in desc
    assert "admin" in desc  # at least one perm
    assert "GitHubClient.get_file" in desc  # agent hint


def test_build_issue_description_no_github_when_missing() -> None:
    """When kpi_collections has no external_sources.github, no GitHub
    section should be added."""
    f = _good_form()
    kpi = {
        "site_slug": "ezshare",
        "domain": "ezshare.systems",
        "metrics": [],
        "external_sources": {"stripe": {"validated": True}},
    }
    desc = build_issue_description(
        f, site_context={"kpi_collections": kpi}
    )
    assert "GitHub repo context" not in desc


def test_build_issue_description_github_branch_mismatch() -> None:
    """When the current branch differs from the default branch, both are shown."""
    f = _good_form()
    kpi = {
        "site_slug": "ezshare", "domain": "ezshare.systems",
        "external_sources": {
            "github": {
                "repo": "mbgulden/EZShare", "branch": "feature/cta",
                "default_branch": "master", "head_sha": "abc123",
                "head_url": "https://x", "html_url": "https://y",
            }
        },
    }
    desc = build_issue_description(f, site_context={"kpi_collections": kpi})
    assert "feature/cta" in desc
    assert "master" in desc


# --- FunnelConfigSubmission roundtrip -------------------------------------

def test_submission_roundtrip(tmp_path: Path) -> None:
    s = FunnelConfigSubmission(
        site_slug="ezshare",
        site_domain="ezshare.systems",
        form=_good_form(),
        linear_issue_id="i-1",
        linear_issue_identifier="GRO-9999",
        linear_issue_url="u",
        submitted_at="2026-07-30T00:00:00Z",
    )
    s.save(tmp_path)
    loaded = FunnelConfigSubmission.load("ezshare", tmp_path)
    assert loaded is not None
    assert loaded.linear_issue_id == "i-1"
    assert loaded.form["site_slug"] == "ezshare"


def test_submission_load_returns_none_when_missing(tmp_path: Path) -> None:
    assert FunnelConfigSubmission.load("nope", tmp_path) is None


def test_submission_to_dict_is_json_serializable() -> None:
    s = FunnelConfigSubmission(
        site_slug="ezshare", site_domain="ezshare.systems",
        form=_good_form(), submitted_at="2026-07-30",
    )
    assert json.dumps(s.to_dict())


# --- dispatch --------------------------------------------------------------

def test_dispatch_creates_new_issue_when_no_existing(tmp_path: Path) -> None:
    client = _mock_client()
    result = dispatch(
        _good_form(),
        client=client,
        log_dir=tmp_path,
    )
    assert result.created is True
    assert result.linear_issue.identifier == "GRO-9999"
    assert result.linear_issue.parent_id == "epic-1"
    assert result.epic_id == "epic-1"

    # The submission log was persisted
    saved = FunnelConfigSubmission.load("ezshare", tmp_path)
    assert saved is not None
    assert saved.linear_issue_id == "new-1"


def test_dispatch_dedupes_by_title(tmp_path: Path) -> None:
    """If an issue with the same title already exists under the epic,
    dispatch must post a comment instead of creating a duplicate."""
    existing = LinearIssue(
        id="existing-1",
        identifier="GRO-7777",
        title="[PE-KPI-FUNNEL] Configure KPIs for ezshare.systems",
        url="u",
        state="In Progress",
        state_id="s",
        state_type="started",
        labels=["plugin:pwp"],
        parent_id="epic-1",
        team_id=DEFAULT_TEAM_ID,
        team_key="GRO",
    )
    client = _mock_client(existing_issue=existing)

    result = dispatch(
        _good_form(),
        client=client,
        log_dir=tmp_path,
    )
    assert result.created is False
    assert result.linear_issue.identifier == "GRO-7777"
    client.create_issue.assert_not_called()
    client.add_comment.assert_called_once()


def test_dispatch_raises_when_epic_not_found(tmp_path: Path) -> None:
    client = _mock_client(epic=None)
    client.list_issues_by_label.return_value = []
    with pytest.raises(FunnelConfigError, match="parent epic"):
        dispatch(_good_form(), client=client, log_dir=tmp_path)


def test_dispatch_rejects_invalid_form(tmp_path: Path) -> None:
    client = _mock_client()
    with pytest.raises(FunnelConfigError, match="Invalid funnel_config form"):
        dispatch(_good_form(site_slug="Bad Spaces!"), client=client, log_dir=tmp_path)


def test_dispatch_refinement_appends_to_history(tmp_path: Path) -> None:
    """A refinement submission must append to the existing submission's
    refinements list, not replace it."""
    # Seed an existing submission log
    seed = FunnelConfigSubmission(
        site_slug="ezshare",
        site_domain="ezshare.systems",
        form=_good_form(),
        linear_issue_id="existing-1",
        linear_issue_identifier="GRO-7777",
        linear_issue_url="u",
        submitted_at="2026-07-30T00:00:00Z",
    )
    seed.save(tmp_path)

    client = _mock_client(existing_issue=LinearIssue(
        id="existing-1", identifier="GRO-7777",
        title="[PE-KPI-FUNNEL] Configure KPIs for ezshare.systems",
        url="u", state="In Progress", state_id="s", state_type="started",
        labels=["plugin:pwp"], parent_id="epic-1",
        team_id=DEFAULT_TEAM_ID, team_key="GRO",
    ))

    result = dispatch(
        _good_form(kind="refinement", context={
            "primary_goal": "Refined goal: also track funnel velocity",
        }),
        client=client,
        log_dir=tmp_path,
    )
    assert result.created is False
    loaded = FunnelConfigSubmission.load("ezshare", tmp_path)
    assert loaded is not None
    assert len(loaded.refinements) == 1
    assert loaded.refinements[0]["form"]["kind"] == "refinement"


# --- find_parent_epic -----------------------------------------------------

def test_find_parent_epic_returns_issue_with_epic_label() -> None:
    client = MagicMock()
    client.list_issues_by_label.return_value = [
        LinearIssue(
            id="x", identifier="GRO-1", title="Other epic",
            url="u", state="", state_id="", state_type="",
            labels=["type:epic"], team_id=DEFAULT_TEAM_ID, team_key="GRO",
        ),
        LinearIssue(
            id="epic-1", identifier="GRO-4356",
            title=f"[PE-KPI-FUNNEL] {EPIC_TITLE_FRAGMENT} dispatch",
            url="u", state="", state_id="", state_type="",
            labels=["type:epic", "plugin:pwp"],
            team_id=DEFAULT_TEAM_ID, team_key="GRO",
        ),
    ]
    epic = find_parent_epic(client, DEFAULT_TEAM_ID)
    assert epic is not None
    assert epic.identifier == "GRO-4356"


def test_find_parent_epic_returns_none_when_missing() -> None:
    client = MagicMock()
    client.list_issues_by_label.return_value = []
    assert find_parent_epic(client, DEFAULT_TEAM_ID) is None


# --- DispatchResult.to_dict -----------------------------------------------

def test_dispatch_result_to_dict_is_serializable(tmp_path: Path) -> None:
    client = _mock_client()
    result = dispatch(_good_form(), client=client, log_dir=tmp_path)
    d = result.to_dict()
    assert json.dumps(d)  # JSON-serializable
    assert "submission" in d
    assert "linear_issue" in d
    assert "created" in d
    assert "epic_id" in d
