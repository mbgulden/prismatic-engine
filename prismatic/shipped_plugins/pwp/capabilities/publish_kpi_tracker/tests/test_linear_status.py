"""Tests for the dashboard Linear status cache (Phase 4.4)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest import mock


# Repo root on sys.path so prismatic.shipped_plugins.pwp.* resolves
# regardless of how the test harness is invoked.
HERE = Path(__file__).resolve()
REPO_ROOT = HERE.parents[6]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status import (  # noqa: E402
    CACHE_PATH,
    DEFAULT_ERROR_TTL_SECONDS,
    DEFAULT_OK_TTL_SECONDS,
    LinearStatus,
    SUBMISSION_LOG_DIR,
    _age_days_from_iso,
    _call_linear_status,
    _latest_issue_id_for_site,
    _read_cache,
    _write_cache,
    get_status_for_site,
    render_status_html,
    scan_status,
)


# ── Constants ────────────────────────────────────────────────────────────
class TestConstants:
    def test_default_ok_ttl(self):
        assert DEFAULT_OK_TTL_SECONDS == 300

    def test_default_error_ttl(self):
        assert DEFAULT_ERROR_TTL_SECONDS == 60

    def test_submission_log_dir(self):
        assert SUBMISSION_LOG_DIR.name == "funnel-config"

    def test_cache_path_default(self):
        # Should default to <SUBMISSION_LOG_DIR>/linear-status-cache.json.
        assert CACHE_PATH == SUBMISSION_LOG_DIR / "linear-status-cache.json"


# ── LinearStatus dataclass ───────────────────────────────────────────────
class TestLinearStatus:
    def test_defaults(self):
        ls = LinearStatus(slug="ezshare")
        assert ls.slug == "ezshare"
        assert ls.linear_issue_id == ""
        assert ls.linear_issue_identifier == ""
        assert ls.state == ""
        assert ls.state_type == ""
        assert ls.assignee_name == ""
        assert ls.age_days == 0.0
        assert ls.error is None

    def test_to_dict_round_trip(self):
        ls = LinearStatus(
            slug="ezshare",
            linear_issue_id="abc-123",
            linear_issue_identifier="GRO-99",
            state="In Progress",
            state_type="started",
            assignee_name="Ned",
            age_days=2.5,
            tenant_id="growthwebdev",
        )
        d = ls.to_dict()
        assert d["slug"] == "ezshare"
        assert d["linear_issue_identifier"] == "GRO-99"
        assert d["state"] == "In Progress"
        assert d["state_type"] == "started"
        assert d["assignee_name"] == "Ned"
        assert d["age_days"] == 2.5
        assert d["tenant_id"] == "growthwebdev"
        assert d["error"] is None

    def test_repr_redacts_safe_fields(self):
        ls = LinearStatus(
            slug="ezshare",
            linear_issue_id="abc-123-internal-uuid",
            linear_issue_identifier="GRO-99",
            state="In Progress",
        )
        # repr should include the slug + identifier + state for log readability,
        # but omit the internal Linear uuid (linear_issue_id).
        r = repr(ls)
        assert "ezshare" in r
        assert "GRO-99" in r
        assert "In Progress" in r
        assert "abc-123-internal-uuid" not in r


# ── Render status HTML ───────────────────────────────────────────────────
class TestRenderStatusHtml:
    def test_empty_when_no_identifier(self):
        ls = LinearStatus(slug="ezshare")
        assert render_status_html(ls) == ""

    def test_empty_when_no_state_and_no_error(self):
        ls = LinearStatus(
            slug="ezshare",
            linear_issue_id="abc",
            linear_issue_identifier="GRO-99",
        )
        assert render_status_html(ls) == ""

    def test_renders_basic_status(self):
        ls = LinearStatus(
            slug="ezshare",
            linear_issue_id="abc",
            linear_issue_identifier="GRO-99",
            state="In Progress",
            state_type="started",
        )
        html = render_status_html(ls)
        assert "GRO-99" in html
        assert "In Progress" in html
        assert 'class="pwp-kpi-linear-status pwp-kpi-linear-status-started"' in html

    def test_renders_error_state(self):
        ls = LinearStatus(
            slug="ezshare",
            linear_issue_id="abc",
            linear_issue_identifier="GRO-99",
            error="rate_limited",
        )
        html = render_status_html(ls)
        assert "rate_limited" in html
        assert "pwp-kpi-linear-status-error" in html

    def test_renders_assignee_and_age(self):
        ls = LinearStatus(
            slug="ezshare",
            linear_issue_id="abc",
            linear_issue_identifier="GRO-99",
            state="Backlog",
            state_type="backlog",
            assignee_name="Ned",
            age_days=2.5,
        )
        html = render_status_html(ls)
        assert "GRO-99" in html
        assert "Backlog" in html
        assert "Ned" in html
        assert "2.5d old" in html

    def test_age_trims_trailing_zeros(self):
        ls = LinearStatus(
            slug="ezshare",
            linear_issue_id="abc",
            linear_issue_identifier="GRO-99",
            state="Backlog",
            state_type="backlog",
            age_days=0.35,
        )
        html = render_status_html(ls)
        assert "0.35d old" in html


# ── Age computation ──────────────────────────────────────────────────────
class TestAgeDaysFromIso:
    def test_iso_with_z_suffix(self):
        # 2026-07-30T00:00:00Z is "today" by the time we test (relative date).
        days = _age_days_from_iso("2026-07-30T00:00:00Z")
        # Just verify it's a non-negative float.
        assert isinstance(days, float)
        assert days >= 0.0

    def test_iso_with_offset(self):
        days = _age_days_from_iso("2026-07-30T00:00:00+00:00")
        assert isinstance(days, float)
        assert days >= 0.0

    def test_empty_string(self):
        assert _age_days_from_iso("") == 0.0

    def test_garbage_string(self):
        assert _age_days_from_iso("not a date") == 0.0

    def test_naive_datetime(self):
        # No timezone - should be treated as UTC.
        days = _age_days_from_iso("2026-07-30T00:00:00")
        assert isinstance(days, float)
        assert days >= 0.0


# ── Cache I/O ─────────────────────────────────────────────────────────────
class TestCacheIO:
    def test_read_cache_missing(self, tmp_path):
        cache_path = tmp_path / "cache.json"
        assert _read_cache(cache_path) == {}

    def test_read_cache_corrupt(self, tmp_path):
        cache_path = tmp_path / "cache.json"
        cache_path.write_text("not json", encoding="utf-8")
        assert _read_cache(cache_path) == {}

    def test_write_cache_creates_file(self, tmp_path):
        cache_path = tmp_path / "subdir" / "cache.json"
        _write_cache({"abc": {"state": "Done"}}, cache_path)
        assert cache_path.exists()
        data = json.loads(cache_path.read_text())
        assert "abc" in data

    def test_read_write_round_trip(self, tmp_path):
        cache_path = tmp_path / "cache.json"
        _write_cache({"abc": {"state": "In Progress", "ts": 12345}}, cache_path)
        data = _read_cache(cache_path)
        assert data["abc"]["state"] == "In Progress"
        assert data["abc"]["ts"] == 12345


# ── Submission log mapping ───────────────────────────────────────────────
class TestLatestIssueId:
    def test_missing_log_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status.SUBMISSION_LOG_DIR",
            tmp_path / "missing",
        )
        assert _latest_issue_id_for_site("ezshare") is None

    def test_missing_site(self, tmp_path, monkeypatch):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status.SUBMISSION_LOG_DIR",
            log_dir,
        )
        assert _latest_issue_id_for_site("nope") is None

    def test_corrupt_log(self, tmp_path, monkeypatch):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "ezshare.json").write_text("not json", encoding="utf-8")
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status.SUBMISSION_LOG_DIR",
            log_dir,
        )
        assert _latest_issue_id_for_site("ezshare") is None

    def test_no_issue_id(self, tmp_path, monkeypatch):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "ezshare.json").write_text(
            json.dumps({"form": {"primary_goal": "g"}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status.SUBMISSION_LOG_DIR",
            log_dir,
        )
        assert _latest_issue_id_for_site("ezshare") is None

    def test_valid_log(self, tmp_path, monkeypatch):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "ezshare.json").write_text(
            json.dumps(
                {
                    "linear_issue_id": "abc-123",
                    "linear_issue_identifier": "GRO-99",
                    "linear_issue_url": "https://linear.app/x/issue/GRO-99",
                    "tenant_id": "growthwebdev",
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status.SUBMISSION_LOG_DIR",
            log_dir,
        )
        result = _latest_issue_id_for_site("ezshare")
        assert result is not None
        assert result["linear_issue_id"] == "abc-123"
        assert result["linear_issue_identifier"] == "GRO-99"
        assert result["tenant_id"] == "growthwebdev"


# ── Linear API call ─────────────────────────────────────────────────────
class TestCallLinearStatus:
    def test_ok_response(self):
        with mock.patch(
            "prismatic.shipped_plugins.pwp.capabilities.provision_site.linear_client.LinearClient.from_env"
        ) as mock_from_env:
            mock_client = mock.MagicMock()
            mock_issue = mock.MagicMock()
            mock_issue.state = "In Progress"
            mock_issue.state_type = "started"
            mock_issue.assignee_name = "Ned"
            mock_issue.url = "https://linear.app/x"
            mock_issue.identifier = "GRO-99"
            mock_issue.title = "Configure KPIs"
            mock_client.get_issue_status.return_value = mock_issue
            mock_from_env.return_value = mock_client

            result = _call_linear_status("abc-123")
            assert result["ok"] is True
            assert result["state"] == "In Progress"
            assert result["state_type"] == "started"
            assert result["assignee_name"] == "Ned"
            assert result["identifier"] == "GRO-99"
            assert result["error"] is None

    def test_404_response(self):
        from prismatic.shipped_plugins.pwp.capabilities.provision_site.linear_client import LinearError

        with mock.patch(
            "prismatic.shipped_plugins.pwp.capabilities.provision_site.linear_client.LinearClient.from_env"
        ) as mock_from_env:
            mock_client = mock.MagicMock()
            mock_client.get_issue_status.side_effect = LinearError(
                "not found", status=404
            )
            mock_from_env.return_value = mock_client

            result = _call_linear_status("abc-123")
            assert result["ok"] is False
            assert result["error"] == "not_found"

    def test_401_response(self):
        from prismatic.shipped_plugins.pwp.capabilities.provision_site.linear_client import LinearError

        with mock.patch(
            "prismatic.shipped_plugins.pwp.capabilities.provision_site.linear_client.LinearClient.from_env"
        ) as mock_from_env:
            mock_client = mock.MagicMock()
            mock_client.get_issue_status.side_effect = LinearError(
                "unauthorized", status=401
            )
            mock_from_env.return_value = mock_client

            result = _call_linear_status("abc-123")
            assert result["ok"] is False
            assert result["error"] == "auth_failed"

    def test_429_response(self):
        from prismatic.shipped_plugins.pwp.capabilities.provision_site.linear_client import LinearError

        with mock.patch(
            "prismatic.shipped_plugins.pwp.capabilities.provision_site.linear_client.LinearClient.from_env"
        ) as mock_from_env:
            mock_client = mock.MagicMock()
            mock_client.get_issue_status.side_effect = LinearError(
                "rate limited", status=429
            )
            mock_from_env.return_value = mock_client

            result = _call_linear_status("abc-123")
            assert result["ok"] is False
            assert result["error"] == "rate_limited"


# ── scan_status (integration) ────────────────────────────────────────────
class TestScanStatus:
    def test_no_log_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status.SUBMISSION_LOG_DIR",
            tmp_path / "missing",
        )
        results = scan_status()
        assert results == []

    def test_skips_sites_without_issue_id(self, tmp_path, monkeypatch):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "ezshare.json").write_text(
            json.dumps({"form": {"primary_goal": "g"}}), encoding="utf-8"
        )
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status.SUBMISSION_LOG_DIR",
            log_dir,
        )
        results = scan_status()
        assert results == []

    def test_cache_hit_skips_linear(self, tmp_path, monkeypatch):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "ezshare.json").write_text(
            json.dumps(
                {
                    "linear_issue_id": "abc-123",
                    "linear_issue_identifier": "GRO-99",
                    "linear_issue_url": "https://x",
                    "submitted_at": "2026-07-30T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        cache_path = log_dir / "cache.json"
        # Pre-populate cache with a fresh entry.
        cache_path.write_text(
            json.dumps(
                {
                    "abc-123": {
                        "ts": time.time(),
                        "fetched_at_iso": "2026-07-30T00:00:00Z",
                        "state": "In Progress",
                        "state_type": "started",
                        "assignee_name": "Ned",
                        "linear_issue_identifier": "GRO-99",
                        "linear_issue_url": "https://x",
                        "error": None,
                    }
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status.SUBMISSION_LOG_DIR",
            log_dir,
        )
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status.CACHE_PATH",
            cache_path,
        )

        # If Linear is called, the test fails. We assert the cache hit path.
        with mock.patch(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status._call_linear_status"
        ) as mock_call:
            results = scan_status()
            assert mock_call.call_count == 0
            assert len(results) == 1
            ls = results[0]
            assert ls.slug == "ezshare"
            assert ls.linear_issue_identifier == "GRO-99"
            assert ls.state == "In Progress"
            assert ls.assignee_name == "Ned"

    def test_cache_miss_calls_linear(self, tmp_path, monkeypatch):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "ezshare.json").write_text(
            json.dumps(
                {
                    "linear_issue_id": "abc-123",
                    "linear_issue_identifier": "GRO-99",
                    "linear_issue_url": "https://x",
                }
            ),
            encoding="utf-8",
        )
        cache_path = log_dir / "cache.json"
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status.SUBMISSION_LOG_DIR",
            log_dir,
        )
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status.CACHE_PATH",
            cache_path,
        )

        with mock.patch(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status._call_linear_status"
        ) as mock_call:
            mock_call.return_value = {
                "ok": True,
                "state": "Backlog",
                "state_type": "backlog",
                "assignee_name": "",
                "url": "https://x",
                "identifier": "GRO-99",
                "title": "Configure KPIs",
                "ts": time.time(),
                "error": None,
            }
            results = scan_status()
            assert mock_call.call_count == 1
            assert len(results) == 1
            ls = results[0]
            assert ls.state == "Backlog"
            assert ls.state_type == "backlog"

    def test_force_bypasses_cache(self, tmp_path, monkeypatch):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "ezshare.json").write_text(
            json.dumps(
                {
                    "linear_issue_id": "abc-123",
                    "linear_issue_identifier": "GRO-99",
                }
            ),
            encoding="utf-8",
        )
        cache_path = log_dir / "cache.json"
        # Fresh cache entry.
        cache_path.write_text(
            json.dumps(
                {
                    "abc-123": {
                        "ts": time.time(),
                        "fetched_at_iso": "2026-07-30T00:00:00Z",
                        "state": "Backlog",
                        "state_type": "backlog",
                        "assignee_name": "",
                        "linear_issue_identifier": "GRO-99",
                        "linear_issue_url": "",
                        "error": None,
                    }
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status.SUBMISSION_LOG_DIR",
            log_dir,
        )
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status.CACHE_PATH",
            cache_path,
        )

        with mock.patch(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status._call_linear_status"
        ) as mock_call:
            mock_call.return_value = {
                "ok": True,
                "state": "In Progress",
                "state_type": "started",
                "assignee_name": "Ned",
                "url": "",
                "identifier": "GRO-99",
                "title": "",
                "ts": time.time(),
                "error": None,
            }
            # force=True bypasses cache.
            scan_status(force=True)
            assert mock_call.call_count == 1

    def test_cache_persists_to_disk(self, tmp_path, monkeypatch):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "ezshare.json").write_text(
            json.dumps(
                {
                    "linear_issue_id": "abc-123",
                    "linear_issue_identifier": "GRO-99",
                }
            ),
            encoding="utf-8",
        )
        cache_path = log_dir / "cache.json"
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status.SUBMISSION_LOG_DIR",
            log_dir,
        )
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status.CACHE_PATH",
            cache_path,
        )

        with mock.patch(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status._call_linear_status"
        ) as mock_call:
            mock_call.return_value = {
                "ok": True,
                "state": "Backlog",
                "state_type": "backlog",
                "assignee_name": "",
                "url": "",
                "identifier": "GRO-99",
                "title": "",
                "ts": time.time(),
                "error": None,
            }
            scan_status()
            # Cache file should now exist.
            assert cache_path.exists()
            data = json.loads(cache_path.read_text())
            assert "abc-123" in data
            assert data["abc-123"]["state"] == "Backlog"

    def test_error_results_have_error_set(self, tmp_path, monkeypatch):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "ezshare.json").write_text(
            json.dumps(
                {
                    "linear_issue_id": "abc-123",
                    "linear_issue_identifier": "GRO-99",
                }
            ),
            encoding="utf-8",
        )
        cache_path = log_dir / "cache.json"
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status.SUBMISSION_LOG_DIR",
            log_dir,
        )
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status.CACHE_PATH",
            cache_path,
        )

        with mock.patch(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status._call_linear_status"
        ) as mock_call:
            mock_call.return_value = {
                "ok": False,
                "state": "",
                "state_type": "",
                "assignee_name": "",
                "url": "",
                "identifier": "",
                "title": "",
                "ts": time.time(),
                "error": "rate_limited",
            }
            results = scan_status()
            assert len(results) == 1
            assert results[0].error == "rate_limited"
            assert results[0].state == ""

    def test_tenant_id_propagated(self, tmp_path, monkeypatch):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "ezshare.json").write_text(
            json.dumps(
                {
                    "linear_issue_id": "abc-123",
                    "linear_issue_identifier": "GRO-99",
                    "tenant_id": "growthwebdev",
                }
            ),
            encoding="utf-8",
        )
        cache_path = log_dir / "cache.json"
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status.SUBMISSION_LOG_DIR",
            log_dir,
        )
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status.CACHE_PATH",
            cache_path,
        )

        with mock.patch(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status._call_linear_status"
        ) as mock_call:
            mock_call.return_value = {
                "ok": True,
                "state": "Backlog",
                "state_type": "backlog",
                "assignee_name": "",
                "url": "",
                "identifier": "GRO-99",
                "title": "",
                "ts": time.time(),
                "error": None,
            }
            results = scan_status()
            assert results[0].tenant_id == "growthwebdev"


# ── get_status_for_site ──────────────────────────────────────────────────
class TestGetStatusForSite:
    def test_returns_none_for_unknown_site(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status.SUBMISSION_LOG_DIR",
            tmp_path / "missing",
        )
        assert get_status_for_site("nope") is None

    def test_returns_status_for_known_site(self, tmp_path, monkeypatch):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "ezshare.json").write_text(
            json.dumps(
                {
                    "linear_issue_id": "abc-123",
                    "linear_issue_identifier": "GRO-99",
                }
            ),
            encoding="utf-8",
        )
        cache_path = log_dir / "cache.json"
        cache_path.write_text(
            json.dumps(
                {
                    "abc-123": {
                        "ts": time.time(),
                        "fetched_at_iso": "2026-07-30T00:00:00Z",
                        "state": "In Progress",
                        "state_type": "started",
                        "assignee_name": "Ned",
                        "linear_issue_identifier": "GRO-99",
                        "linear_issue_url": "",
                        "error": None,
                    }
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status.SUBMISSION_LOG_DIR",
            log_dir,
        )
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status.CACHE_PATH",
            cache_path,
        )
        ls = get_status_for_site("ezshare")
        assert ls is not None
        assert ls.slug == "ezshare"
        assert ls.state == "In Progress"


# ── render_index integration ─────────────────────────────────────────────
class TestRenderIndexIntegration:
    def test_render_index_includes_linear_status_pill(self, tmp_path, monkeypatch):
        # Pretend ezshare has a Backlog status.
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "ezshare.json").write_text(
            json.dumps(
                {
                    "linear_issue_id": "abc-123",
                    "linear_issue_identifier": "GRO-99",
                    "submitted_at": "2026-07-30T00:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        cache_path = log_dir / "cache.json"
        cache_path.write_text(
            json.dumps(
                {
                    "abc-123": {
                        "ts": time.time(),
                        "fetched_at_iso": "2026-07-30T00:00:00Z",
                        "state": "Backlog",
                        "state_type": "backlog",
                        "assignee_name": "",
                        "linear_issue_identifier": "GRO-99",
                        "linear_issue_url": "",
                        "error": None,
                    }
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status.SUBMISSION_LOG_DIR",
            log_dir,
        )
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.linear_status.CACHE_PATH",
            cache_path,
        )

        from prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.publish_kpi_tracker import (
            render_index,
        )

        agg = {
            "window": "last24h",
            "sites": [
                {
                    "slug": "ezshare",
                    "name": "ezshare",
                    "domain": "ezshare.systems",
                    "owner": "ned",
                    "metric_count": 0,
                    "extends": None,
                    "front_of_card": [],
                }
            ],
        }
        out = render_index(agg, csrf_token="stable-csrf")
        assert "GRO-99" in out
        assert "Backlog" in out
        assert "pwp-kpi-linear-status" in out
