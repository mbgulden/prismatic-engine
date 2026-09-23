"""P0 #6: light is the no-preference theme default.

Static verification: the manual toggle and the ``?theme=light|dark`` query
override must keep working; only the first-visit default changes (was dark).
Visual verification of every view in light mode is deferred to Phase 0
(blocked on the Cloudflare credential reconnect for authenticated portal
access).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_JS = REPO_ROOT / "prismatic/gateway/dashboard_src/scripts/dashboard.js"
BUILT_TEMPLATE = REPO_ROOT / "prismatic/gateway/templates/dashboard.html"


def _js() -> str:
    return DASHBOARD_JS.read_text(encoding="utf-8")


def test_default_theme_is_light():
    assert 'const DEFAULT_THEME = "light";' in _js()


def test_no_preference_resolves_to_default_theme():
    js = _js()
    assert (
        'const theme = saved === "dark" || saved === "light" ? saved : DEFAULT_THEME;'
        in js
    )
    assert "document.body.classList.toggle(\"light-mode\", isLight);" in js


def test_explicit_dark_choice_still_honored():
    # An explicit saved "dark" must not be overridden by the new default.
    js = _js()
    assert 'saved === "dark"' in js


def test_query_param_override_preserved():
    js = _js()
    assert 'urlParams.get("theme")' in js
    assert 'if (queryTheme === "light" || queryTheme === "dark")' in js


def test_manual_toggle_preserved():
    js = _js()
    assert 'localStorage.setItem("theme", isLight ? "light" : "dark");' in js
    assert "function toggleTheme()" in js


def test_built_template_carries_light_default():
    html = BUILT_TEMPLATE.read_text(encoding="utf-8")
    assert 'const DEFAULT_THEME = "light";' in html
    assert (
        'const theme = saved === "dark" || saved === "light" ? saved : DEFAULT_THEME;'
        in html
    )
