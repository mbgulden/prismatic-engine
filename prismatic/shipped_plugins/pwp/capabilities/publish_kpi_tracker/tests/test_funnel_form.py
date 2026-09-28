"""Tests for the dashboard funnel-config modal (Phase 4.2)."""

from __future__ import annotations

import json
import sys
from pathlib import Path


# Repo root on sys.path so prismatic.shipped_plugins.pwp.* resolves
# regardless of how the test harness is invoked.
HERE = Path(__file__).resolve()
REPO_ROOT = HERE.parents[6]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.funnel_form import (  # noqa: E402
    DATA_SOURCE_OPTIONS,
    DEFAULT_SUBMIT_ENDPOINT,
    FORM_VERSION,
    SUBMISSION_LOG_DIR,
    render_button_wiring_js,
    render_modal_css,
    render_modal_html,
    site_row_buttons,
    write_prior_submission_json,
)
from prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.funnel_form import (  # noqa: E402,E501
    load_prior_submission,
)


# ── Smoke: constants ─────────────────────────────────────────────────────
class TestConstants:
    def test_form_version_is_one(self):
        assert FORM_VERSION == 1

    def test_data_source_options_has_six_entries(self):
        # Stripe, zapier, telegram, internal, vercel, cloudflare.
        assert len(DATA_SOURCE_OPTIONS) == 6

    def test_data_source_options_have_value_and_label(self):
        for opt in DATA_SOURCE_OPTIONS:
            assert "value" in opt
            assert "label" in opt
            assert isinstance(opt["value"], str)
            assert isinstance(opt["label"], str)

    def test_default_submit_endpoint_is_pwp_api(self):
        assert DEFAULT_SUBMIT_ENDPOINT.startswith("/pwp/api/")

    def test_submission_log_dir_path_exists(self):
        # Should be under /tmp/pwp-provisioning/funnel-config by default.
        assert SUBMISSION_LOG_DIR.name == "funnel-config"
        assert str(SUBMISSION_LOG_DIR).replace("\\", "/").startswith("/tmp/")


# ── render_modal_html structure ──────────────────────────────────────────
class TestRenderModalHtml:
    def test_returns_string(self):
        out = render_modal_html()
        assert isinstance(out, str)

    def test_min_length(self):
        out = render_modal_html()
        # The modal HTML+JS is > 4KB; this guards against accidental truncation.
        assert len(out) > 4_000

    def test_contains_modal_root(self):
        out = render_modal_html()
        assert 'id="pwp-kpi-modal"' in out
        assert 'role="dialog"' in out
        assert 'aria-modal="true"' in out

    def test_contains_form(self):
        out = render_modal_html()
        assert 'id="pwp-kpi-modal-form"' in out
        assert 'name="site_slug"' in out
        assert 'name="site_domain"' in out
        assert 'name="primary_goal"' in out
        assert 'name="data_sources"' in out

    def test_form_version_field_is_value_1(self):
        out = render_modal_html()
        # The hidden form_version field should be FORM_VERSION (1).
        assert f'value="{FORM_VERSION}"' in out

    def test_csrf_token_present_in_data_attr_and_hidden_field(self):
        out = render_modal_html(csrf_token="test-csrf-1234567890")
        assert 'data-csrf="test-csrf-1234567890"' in out
        assert 'name="csrf_token" value="test-csrf-1234567890"' in out

    def test_csrf_token_default_is_generated_and_unique(self):
        # Two calls should produce different CSRF tokens (from secrets.token_urlsafe).
        a = render_modal_html()
        b = render_modal_html()
        token_a = a.split('data-csrf="')[1].split('"')[0]
        token_b = b.split('data-csrf="')[1].split('"')[0]
        assert token_a != token_b
        # And the tokens should be >= 16 chars (token_urlsafe(16) → 22 chars).
        assert len(token_a) >= 16
        assert len(token_b) >= 16

    def test_submit_endpoint_is_in_data_attr(self):
        out = render_modal_html(submit_endpoint="/pwp/api/funnel-config")
        assert 'data-endpoint="/pwp/api/funnel-config"' in out

    def test_submit_endpoint_default(self):
        out = render_modal_html()
        assert f'data-endpoint="{DEFAULT_SUBMIT_ENDPOINT}"' in out

    def test_all_data_source_options_rendered(self):
        out = render_modal_html()
        for opt in DATA_SOURCE_OPTIONS:
            assert f'value="{opt["value"]}"' in out
            assert opt["label"] in out

    def test_required_field_marker_present(self):
        out = render_modal_html()
        assert 'class="required"' in out
        # primary_goal is the only required field.
        assert 'Primary goal <span class="required">*</span>' in out

    def test_includes_controller_js(self):
        out = render_modal_html()
        # The IIFE that defines window.pwpKpiModal.open / close.
        assert "window.pwpKpiModal" in out
        assert "function open(slug, domain, kind)" in out
        assert "function close()" in out

    def test_includes_fallback_download(self):
        out = render_modal_html()
        # The no-backend fallback path.
        assert "fallbackDownload" in out
        assert "funnel-config-" in out  # download filename template

    def test_includes_prefill_from_submission(self):
        out = render_modal_html()
        # The refinement flow pre-fills from /pwp/api/funnel-config/<slug>/prior
        assert "prefillFromSubmission" in out
        assert "/prior" in out

    def test_includes_close_button(self):
        out = render_modal_html()
        assert "data-pwp-kpi-modal-close" in out
        assert "&times;" in out or "×" in out

    def test_includes_escape_key_handler(self):
        out = render_modal_html()
        # Escape key closes the modal.
        assert "Escape" in out

    def test_includes_csrf_header_in_fetch(self):
        out = render_modal_html()
        assert "X-CSRF-Token" in out

    def test_includes_feedback_status(self):
        out = render_modal_html()
        assert 'aria-live="polite"' in out
        assert "pwp-kpi-modal-feedback" in out

    def test_includes_kind_badge(self):
        out = render_modal_html()
        assert "pwp-kpi-modal-kind-badge" in out

    def test_no_dangerous_javascript_patterns(self):
        # Sanity: the modal JS should not contain real eval() calls. (Commented
        # evals are fine. We want to make sure no real eval() calls.)
        out = render_modal_html()
        assert "eval(" not in out
        # The CSRF token is escaped via _esc (which escapes &<>").
        # A token containing <>" must end up as &lt; / &gt; / &quot; in attributes.
        token = 'evil-"<>'
        out = render_modal_html(csrf_token=token)
        assert "&quot;" in out
        assert "&lt;" in out
        assert "&gt;" in out
        # The raw chars must not appear in attribute values.
        assert 'data-csrf="evil-"' not in out


# ── render_modal_css structure ───────────────────────────────────────────
class TestRenderModalCss:
    def test_returns_string(self):
        css = render_modal_css()
        assert isinstance(css, str)

    def test_min_length(self):
        css = render_modal_css()
        assert len(css) > 1_000

    def test_key_selectors_present(self):
        css = render_modal_css()
        assert ".pwp-kpi-modal" in css
        assert ".pwp-kpi-modal-panel" in css
        assert ".pwp-kpi-modal-backdrop" in css
        assert ".pwp-kpi-modal-form" in css
        assert ".pwp-kpi-modal-field" in css
        assert ".pwp-kpi-modal-btn-primary" in css
        assert ".pwp-kpi-modal-btn-secondary" in css
        assert ".pwp-kpi-modal-feedback" in css
        assert ".pwp-kpi-modal-feedback-ok" in css
        assert ".pwp-kpi-modal-feedback-error" in css
        assert ".pwp-kpi-site-actions" in css
        assert ".pwp-kpi-btn-configure" in css
        assert ".pwp-kpi-btn-edit-funnel" in css

    def test_responsive_modal_panel(self):
        css = render_modal_css()
        # The modal panel is constrained to 92vw so it works on mobile.
        assert "92vw" in css
        assert "max-height" in css

    def test_modal_uses_css_variables(self):
        # Theming via CSS variables so the modal works with the host's palette.
        css = render_modal_css()
        assert "var(--pwp-accent" in css
        assert "var(--pwp-bg" in css
        assert "var(--pwp-fg" in css


# ── render_button_wiring_js ──────────────────────────────────────────────
class TestRenderButtonWiringJs:
    def test_returns_string(self):
        js = render_button_wiring_js()
        assert isinstance(js, str)

    def test_wires_open_attribute(self):
        js = render_button_wiring_js()
        assert "data-pwp-kpi-modal-open" in js
        assert "openFromButton" in js

    def test_handles_dom_ready_states(self):
        js = render_button_wiring_js()
        # Both "loading" and "interactive" / "complete" should be handled.
        assert "readyState" in js
        assert "DOMContentLoaded" in js

    def test_calls_pwpKpiModal_open(self):
        js = render_button_wiring_js()
        assert "window.pwpKpiModal.open" in js


# ── site_row_buttons ─────────────────────────────────────────────────────
class TestSiteRowButtons:
    def test_returns_string(self):
        out = site_row_buttons({"slug": "ezshare", "domain": "ezshare.systems"})
        assert isinstance(out, str)

    def test_no_prior_submission_shows_configure_label(self, tmp_path, monkeypatch):
        # Point the SUBMISSION_LOG_DIR at an empty tmp directory.
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.funnel_form.SUBMISSION_LOG_DIR",
            tmp_path,
        )
        out = site_row_buttons({"slug": "ezshare", "domain": "ezshare.systems"})
        assert "Configure website KPIs" in out
        assert "Re-submit refinement" not in out
        assert 'data-pwp-kpi-modal-kind="init"' in out

    def test_prior_submission_shows_edit_funnel_label(self, tmp_path, monkeypatch):
        # Write a prior submission log to the tmp directory.
        log_dir = tmp_path
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "ezshare.json").write_text(
            json.dumps({"form": {"primary_goal": "test"}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.funnel_form.SUBMISSION_LOG_DIR",
            log_dir,
        )
        out = site_row_buttons({"slug": "ezshare", "domain": "ezshare.systems"})
        assert "Edit funnel" in out
        assert "Re-submit refinement" in out
        assert 'data-pwp-kpi-modal-kind="refinement"' in out

    def test_buttons_carry_slug_and_domain(self):
        out = site_row_buttons({"slug": "active-oahu", "domain": "active-oahu.com"})
        assert "active-oahu" in out
        assert "active-oahu.com" in out

    def test_buttons_escape_html_in_slug(self):
        # If the slug contained a quote, it should be escaped.
        out = site_row_buttons({"slug": "evil<slug>", "domain": "evil.com"})
        assert "evil&lt;slug&gt;" in out
        # The raw chars should NOT appear in the attribute values.
        assert 'data-pwp-kpi-modal-open="evil<slug>"' not in out

    def test_buttons_escape_html_in_domain(self):
        out = site_row_buttons({"slug": "x", "domain": "<script>"})
        assert "&lt;script&gt;" in out

    def test_empty_slug_renders_button(self):
        out = site_row_buttons({"slug": "", "domain": ""})
        # Should still produce a button (empty attrs); not crash.
        assert "pwp-kpi-btn-configure" in out


# ── load_prior_submission ────────────────────────────────────────────────
class TestLoadPriorSubmission:
    def test_missing_file_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.funnel_form.SUBMISSION_LOG_DIR",
            tmp_path,
        )
        assert load_prior_submission("nope") is None

    def test_corrupt_json_returns_none(self, tmp_path, monkeypatch):
        (tmp_path / "x.json").write_text("not json", encoding="utf-8")
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.funnel_form.SUBMISSION_LOG_DIR",
            tmp_path,
        )
        assert load_prior_submission("x") is None

    def test_valid_form_returned(self, tmp_path, monkeypatch):
        form = {
            "site_slug": "ezshare",
            "site_domain": "ezshare.systems",
            "form_version": 1,
            "context": {"primary_goal": "goal"},
        }
        (tmp_path / "ezshare.json").write_text(
            json.dumps({"form": form, "linear_issue_identifier": "GRO-1"}),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.funnel_form.SUBMISSION_LOG_DIR",
            tmp_path,
        )
        result = load_prior_submission("ezshare")
        assert result == form

    def test_non_dict_form_returns_none(self, tmp_path, monkeypatch):
        (tmp_path / "x.json").write_text(json.dumps({"form": "not a dict"}))
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.funnel_form.SUBMISSION_LOG_DIR",
            tmp_path,
        )
        assert load_prior_submission("x") is None


# ── Integration: render_index uses the modal ─────────────────────────────
class TestRenderIndexIntegration:
    def test_render_index_contains_modal_html(self):
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
        out = render_index(agg)
        # Modal HTML is present.
        assert 'id="pwp-kpi-modal"' in out
        # Wire JS is present.
        assert "window.pwpKpiModal" in out
        # Per-site button is present.
        assert "pwp-kpi-btn-configure" in out
        assert "ezshare" in out

    def test_render_index_shows_edit_funnel_when_prior_exists(
        self, tmp_path, monkeypatch
    ):
        # Write a prior submission log so the button flips to "Edit funnel".
        log_dir = tmp_path
        (log_dir / "ezshare.json").write_text(
            json.dumps({"form": {"primary_goal": "x"}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.funnel_form.SUBMISSION_LOG_DIR",
            log_dir,
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
        out = render_index(agg)
        assert "Edit funnel" in out
        assert "Re-submit refinement" in out


# ── Phase 4.3: write_prior_submission_json (static prior-submission files) ──
class TestWritePriorSubmissionJson:
    def test_writes_one_file_per_prior_submission(self, tmp_path, monkeypatch):
        # Drop a fake SUBMISSION_LOG_DIR with two site entries.
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "ezshare.json").write_text(
            """{"form": {"primary_goal": "g"}, "linear_issue_identifier": "GRO-1"}""",
            encoding="utf-8",
        )
        (log_dir / "active-oahu.json").write_text(
            """{"form": {"primary_goal": "ao"}}""",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.funnel_form.SUBMISSION_LOG_DIR",
            log_dir,
        )
        # Publish root is a separate dir.
        pub = tmp_path / "publish"
        pub.mkdir()
        written = write_prior_submission_json(pub)
        names = sorted(w.name for w in written)
        assert names == ["active-oahu.prior.json", "ezshare.prior.json"]
        # File contents.
        ezshare_data = (pub / "ezshare.prior.json").read_text()
        assert "GRO-1" in ezshare_data
        assert "primary_goal" in ezshare_data

    def test_skips_corrupt_json(self, tmp_path, monkeypatch):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "broken.json").write_text("not json", encoding="utf-8")
        (log_dir / "good.json").write_text(
            """{"form": {"primary_goal": "g"}}""", encoding="utf-8"
        )
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.funnel_form.SUBMISSION_LOG_DIR",
            log_dir,
        )
        pub = tmp_path / "publish"
        pub.mkdir()
        written = write_prior_submission_json(pub)
        names = [w.name for w in written]
        assert "good.prior.json" in names
        assert "broken.prior.json" not in names

    def test_skips_when_log_dir_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.funnel_form.SUBMISSION_LOG_DIR",
            tmp_path / "does-not-exist",
        )
        pub = tmp_path / "publish"
        pub.mkdir()
        written = write_prior_submission_json(pub)
        assert written == []

    def test_skips_when_no_priors(self, tmp_path, monkeypatch):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.funnel_form.SUBMISSION_LOG_DIR",
            log_dir,
        )
        pub = tmp_path / "publish"
        pub.mkdir()
        written = write_prior_submission_json(pub)
        assert written == []

    def test_skips_non_dict_or_formless_files(self, tmp_path, monkeypatch):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "x.json").write_text("""{"form": "not a dict"}""")
        (log_dir / "y.json").write_text("""{"no_form": true}""")
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.funnel_form.SUBMISSION_LOG_DIR",
            log_dir,
        )
        pub = tmp_path / "publish"
        pub.mkdir()
        written = write_prior_submission_json(pub)
        assert written == []

    def test_prior_json_is_loadable(self, tmp_path, monkeypatch):
        import json

        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "ezshare.json").write_text(
            json.dumps(
                {
                    "form": {"primary_goal": "g", "context": {"primary_goal": "g2"}},
                    "linear_issue_identifier": "GRO-99",
                    "linear_issue_url": "https://linear.app/x/issue/GRO-99",
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.funnel_form.SUBMISSION_LOG_DIR",
            log_dir,
        )
        pub = tmp_path / "publish"
        pub.mkdir()
        written = write_prior_submission_json(pub)
        assert len(written) == 1
        # Re-load and verify shape.
        loaded = json.loads(written[0].read_text())
        assert loaded["form"]["primary_goal"] == "g"
        assert loaded["linear_issue_identifier"] == "GRO-99"
        assert loaded["linear_issue_url"].startswith("https://")


# ── Phase 4.3: modal JS fallback to <slug>.prior.json ─────────────────────
class TestModalJsStaticPriorFallback:
    def test_modal_js_includes_prior_json_urls(self):
        out = render_modal_html()
        # The Phase 4.3 fallback URLs.
        assert "/pwp/kpi/' + encodeURIComponent(slug) + '.prior.json" in out
        assert "/pwp/' + encodeURIComponent(slug) + '.prior.json" in out
        assert "encodeURIComponent(slug) + '.prior.json" in out

    def test_modal_js_includes_tryFetchSequence_helper(self):
        out = render_modal_html()
        assert "tryFetchSequence" in out

    def test_modal_js_preserves_endpoint_first(self):
        # When an endpoint is configured, the API endpoint is tried first.
        out = render_modal_html(submit_endpoint="/pwp/api/funnel-config")
        assert "urls.push(endpoint + '/' + encodeURIComponent(slug) + '/prior')" in out

    def test_modal_js_falls_back_when_no_endpoint(self):
        out = render_modal_html(submit_endpoint="")
        # The endpoint is empty, so the static URLs are the only options.
        # We assert that the static URLs are still listed.
        assert "'/pwp/kpi/' + encodeURIComponent(slug) + '.prior.json'" in out


# ── Phase 4.3: build_dashboard integration ────────────────────────────────
class TestBuildDashboardPriorSubmissions:
    def test_build_dashboard_writes_prior_json_files(self, tmp_path, monkeypatch):
        # Set up a submission log dir.
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "ezshare.json").write_text(
            """{"form": {"primary_goal": "g"}, "linear_issue_identifier": "GRO-1"}""",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.funnel_form.SUBMISSION_LOG_DIR",
            log_dir,
        )

        from prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker import build_dashboard

        out_dir = tmp_path / "publish"
        manifest = build_dashboard(publish_root=str(out_dir))
        # ezshare.prior.json should now exist in the publish root.
        prior = out_dir / "ezshare.prior.json"
        assert prior.exists()
        # Manifest should list it.
        assert "ezshare.prior.json" in manifest["prior_submission_files"][0]

    def test_build_dashboard_manifest_includes_prior_files_list(
        self, tmp_path, monkeypatch
    ):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "ezshare.json").write_text(
            """{"form": {"primary_goal": "g"}}""", encoding="utf-8"
        )
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.funnel_form.SUBMISSION_LOG_DIR",
            log_dir,
        )
        from prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker import build_dashboard

        out_dir = tmp_path / "publish"
        manifest = build_dashboard(publish_root=str(out_dir))
        assert "prior_submission_files" in manifest
        assert isinstance(manifest["prior_submission_files"], list)
        assert len(manifest["prior_submission_files"]) == 1

    def test_build_dashboard_no_priors(self, tmp_path, monkeypatch):
        # Empty log dir.
        monkeypatch.setattr(
            "prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.funnel_form.SUBMISSION_LOG_DIR",
            tmp_path / "missing",
        )
        from prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker import build_dashboard

        out_dir = tmp_path / "publish"
        manifest = build_dashboard(publish_root=str(out_dir))
        assert manifest["prior_submission_files"] == []

    def test_render_index_works_when_funnel_form_missing(self, monkeypatch):
        # Simulate funnel_form being uninstalled (the lazy import inside
        # render_index should swallow the ImportError).
        import prismatic.shipped_plugins.pwp.capabilities.publish_kpi_tracker.publish_kpi_tracker as m

        # Block the funnel_form import by making it fail.
        real_import = __import__

        def fake_import(name, *args, **kwargs):
            if name.endswith("funnel_form"):
                raise ImportError("simulated missing funnel_form")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", fake_import)
        # The dashboard should still render (without the modal).
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
        out = m.render_index(agg)
        # No modal block.
        assert 'id="pwp-kpi-modal"' not in out
        # But the site row still renders.
        assert "ezshare" in out
