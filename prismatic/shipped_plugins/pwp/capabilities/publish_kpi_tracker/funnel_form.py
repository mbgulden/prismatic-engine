"""funnel_form — PWP dashboard 'Configure website KPIs' modal (Phase 4.2).

This module renders the HTML + CSS + JS for the dashboard modal that lets
the user dispatch a funnel-config task to Linear without leaving the page.

The dashboard itself is static HTML (rendered by `publish_kpi_tracker.py`),
so the modal is also self-contained: vanilla JS, no framework, no
transpile step. The form fields match `funnel_config.FORM_SCHEMA_V1`:

    site_slug       (hidden; injected from the row's button)
    site_domain     (hidden; injected from the row's button)
    form_version    (hidden; always 1)
    kind            (hidden; "init" or "refinement"; chosen by which button was clicked)
    context.primary_goal       (required)
    context.funnel_ideas       (optional)
    context.traffic_patterns   (optional)
    context.data_sources       (multi-select checkbox group)
    context.platform           (optional; auto-detected if compute_platform=true)
    context.notes              (optional)

Submission:

    The form POSTs to a configured endpoint (default: `/pwp/api/funnel-config`).
    In the absence of a backend, the modal falls back to a "Download JSON"
    handler that emits the same payload as a JSON file the user can hand
    to the agent. This is the safe, no-server fallback for static hosting.

Edit-funnel pre-fill:

    When the user clicks "Edit funnel" on a site that already has a
    submission log (written by `funnel_config.FunnelConfigSubmission.save`),
    the modal pre-fills the form fields from the prior form. The modal
    also flips the hidden `kind` to "refinement" so the dispatcher
    creates a child task rather than overwriting the prior one.

CSRF:

    Simple per-page nonce (no session). The dashboard host embeds the
    nonce in the modal HTML and the server (or fallback handler) checks
    it. Static-only deployments skip the check (the JSON download path
    does not send the token).

This module is intentionally framework-free. It returns strings that
`render_index` injects into the existing HTML (CSS in <head>, JS+HTML
before </body>). All HTML is escaped via `_esc` (the same helper used
by `publish_kpi_tracker.py`).
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

# JSON Schema (form_version 1) — must match funnel_config.FORM_SCHEMA_V1.
# Replicated here because the dashboard host should not import provision_site
# (which would create a runtime dep).
FORM_VERSION = 1

# Data sources the modal exposes as checkboxes (subset of funnel_config's
# enum that the user can actually toggle on the dashboard).
DATA_SOURCE_OPTIONS: list[dict[str, str]] = [
    {"value": "stripe", "label": "Stripe payments"},
    {"value": "zapier", "label": "Zapier webhook"},
    {"value": "telegram", "label": "Telegram bot"},
    {"value": "internal", "label": "Internal / domain-specific"},
    {"value": "vercel", "label": "Vercel platform"},
    {"value": "cloudflare", "label": "Cloudflare platform"},
]

# Default endpoint the modal POSTs to. The wrapper script (or FastAPI
# host) chooses the real endpoint; the fallback handler serializes to
# a JSON download when no endpoint is configured.
DEFAULT_SUBMIT_ENDPOINT = "/pwp/api/funnel-config"
SUBMISSION_LOG_DIR = Path(
    os.environ.get("PWP_FUNNEL_CONFIG_DIR", "/tmp/pwp-provisioning/funnel-config")
)


# ── Helpers ────────────────────────────────────────────────────────────────
def _esc(s: Any) -> str:
    """Conservative HTML escape (matches publish_kpi_tracker._esc)."""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _csrf_token() -> str:
    """Per-page nonce. Use `secrets.token_urlsafe` so it's unguessable from
    the static HTML alone if the server enforces CSRF later."""
    return secrets.token_urlsafe(16)


# ── Pre-fill: read prior submission log (if any) ─────────────────────────
def load_prior_submission(site_slug: str) -> dict[str, Any] | None:
    """Return the prior submission form dict for a site, or None.

    Reads from `PWP_FUNNEL_CONFIG_DIR/<slug>.json`. The file is written
    by `funnel_config.FunnelConfigSubmission.save` so the format is
    stable: `{"form": {...}, "linear_issue_identifier": "...", ...}`.
    """
    path = SUBMISSION_LOG_DIR / f"{site_slug}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    form = data.get("form")
    return form if isinstance(form, dict) else None


# ── Modal HTML ────────────────────────────────────────────────────────────
def render_modal_html(
    *,
    submit_endpoint: str = DEFAULT_SUBMIT_ENDPOINT,
    csrf_token: str | None = None,
) -> str:
    """Return the modal HTML + JS, ready to inject before `</body>`.

    The modal is hidden by default and toggled via `pwpKpiModal.open(slug, domain, kind)`.
    The same modal serves init and refinement flows; the only difference
    is the hidden `kind` field, which is set by the button.
    """
    if csrf_token is None:
        csrf_token = _csrf_token()
    endpoint = _esc(submit_endpoint)
    token = _esc(csrf_token)

    # Data-source checkboxes (rendered once, populated via JS).
    source_checkboxes = "".join(
        '<label class="pwp-kpi-modal-source-row">'
        '<input type="checkbox" name="data_sources" value="' + _esc(o["value"]) + '">'
        "<span>" + _esc(o["label"]) + "</span>"
        "</label>"
        for o in DATA_SOURCE_OPTIONS
    )

    return (
        "<!-- pwp-kpi funnel-config modal (Phase 4.2) -->\n"
        '<div id="pwp-kpi-modal" class="pwp-kpi-modal" role="dialog" aria-modal="true" aria-labelledby="pwp-kpi-modal-title" hidden>\n'
        '  <div class="pwp-kpi-modal-backdrop" data-pwp-kpi-modal-close></div>\n'
        '  <div class="pwp-kpi-modal-panel">\n'
        '    <header class="pwp-kpi-modal-header">\n'
        '      <h2 id="pwp-kpi-modal-title">Configure website KPIs</h2>\n'
        '      <button type="button" class="pwp-kpi-modal-close" data-pwp-kpi-modal-close aria-label="Close">×</button>\n'
        "    </header>\n"
        '    <form id="pwp-kpi-modal-form" class="pwp-kpi-modal-form" data-endpoint="'
        + endpoint
        + '" data-csrf="'
        + token
        + '">\n'
        '      <input type="hidden" name="site_slug" id="pwp-kpi-modal-slug" value="">\n'
        '      <input type="hidden" name="site_domain" id="pwp-kpi-modal-domain" value="">\n'
        '      <input type="hidden" name="form_version" value="'
        + str(FORM_VERSION)
        + '">\n'
        '      <input type="hidden" name="kind" id="pwp-kpi-modal-kind" value="init">\n'
        '      <input type="hidden" name="csrf_token" value="' + token + '">\n'
        "\n"
        '      <p class="pwp-kpi-modal-context-banner">\n'
        '        <strong id="pwp-kpi-modal-context-name">—</strong>\n'
        '        <span class="muted">(<span id="pwp-kpi-modal-context-slug">—</span> · <span id="pwp-kpi-modal-context-domain">—</span>)</span>\n'
        '        <span class="pwp-kpi-modal-kind-badge" id="pwp-kpi-modal-kind-badge">init</span>\n'
        "      </p>\n"
        "\n"
        '      <div class="pwp-kpi-modal-field">\n'
        '        <label for="pwp-kpi-modal-primary-goal">Primary goal <span class="required">*</span></label>\n'
        '        <input type="text" name="primary_goal" id="pwp-kpi-modal-primary-goal" required maxlength="256" placeholder="e.g. Increase booking conversion rate to 8%">\n'
        "      </div>\n"
        "\n"
        '      <div class="pwp-kpi-modal-field">\n'
        '        <label for="pwp-kpi-modal-funnel-ideas">Funnel ideas <span class="muted">(optional)</span></label>\n'
        '        <textarea name="funnel_ideas" id="pwp-kpi-modal-funnel-ideas" rows="3" placeholder="What does the conversion path look like? (e.g. home → tours → booking → checkout)"></textarea>\n'
        "      </div>\n"
        "\n"
        '      <div class="pwp-kpi-modal-field">\n'
        '        <label for="pwp-kpi-modal-traffic-patterns">Traffic patterns <span class="muted">(optional)</span></label>\n'
        '        <textarea name="traffic_patterns" id="pwp-kpi-modal-traffic-patterns" rows="2" placeholder="Where does the site get traffic? (e.g. organic search, paid ads, Telegram channel)"></textarea>\n'
        "      </div>\n"
        "\n"
        '      <fieldset class="pwp-kpi-modal-fieldset">\n'
        "        <legend>Data sources to track</legend>\n"
        '        <div class="pwp-kpi-modal-sources">\n' + source_checkboxes + "\n"
        "        </div>\n"
        "      </fieldset>\n"
        "\n"
        '      <div class="pwp-kpi-modal-field">\n'
        '        <label for="pwp-kpi-modal-platform">Detected platform <span class="muted">(optional, leave blank if unsure)</span></label>\n'
        '        <input type="text" name="platform" id="pwp-kpi-modal-platform" placeholder="e.g. cloudflare-pages, vercel, static">\n'
        "      </div>\n"
        "\n"
        '      <div class="pwp-kpi-modal-field">\n'
        '        <label for="pwp-kpi-modal-notes">Notes <span class="muted">(optional)</span></label>\n'
        '        <textarea name="notes" id="pwp-kpi-modal-notes" rows="2" placeholder="Anything else the agent should know?"></textarea>\n'
        "      </div>\n"
        "\n"
        '      <div class="pwp-kpi-modal-actions">\n'
        '        <button type="button" class="pwp-kpi-modal-btn pwp-kpi-modal-btn-secondary" data-pwp-kpi-modal-close>Cancel</button>\n'
        '        <button type="submit" class="pwp-kpi-modal-btn pwp-kpi-modal-btn-primary" id="pwp-kpi-modal-submit">Submit to Linear</button>\n'
        "      </div>\n"
        "\n"
        '      <p class="pwp-kpi-modal-feedback" id="pwp-kpi-modal-feedback" role="status" aria-live="polite"></p>\n'
        "    </form>\n"
        "  </div>\n"
        "</div>\n"
        "<script>\n"
        "(function () {\n"
        "  // ── pwp-kpi funnel-config modal controller (Phase 4.2) ─────────────────\n"
        "  // Vanilla JS, no deps. Exposes window.pwpKpiModal.open(slug, domain, kind).\n"
        "  // The form post hooks into the configured endpoint; if that endpoint\n"
        "  // returns 404 (no backend), the modal falls back to a JSON download\n"
        "  // so the user can hand the form to the agent via the CLI.\n"
        "\n"
        "  function $ (id) { return document.getElementById(id); }\n"
        "\n"
        "  var modal = $('pwp-kpi-modal');\n"
        "  if (!modal) return;\n"
        "\n"
        "  var form = $('pwp-kpi-modal-form');\n"
        "  var feedback = $('pwp-kpi-modal-feedback');\n"
        "\n"
        "  function setText(id, val) {\n"
        "    var el = $(id);\n"
        "    if (el) el.textContent = val || '';\n"
        "  }\n"
        "\n"
        "  function setVal(id, val) {\n"
        "    var el = $(id);\n"
        "    if (el) el.value = val || '';\n"
        "  }\n"
        "\n"
        "  function prefillFromSubmission(slug) {\n"
        "    // Reads the prior submission log. Phase 4.3 (F4 Edit funnel UI):\n"
        "    // try the API endpoint first (when a backend is configured), then\n"
        "    // fall back to the static <slug>.prior.json file written by\n"
        "    // build_dashboard — this is the no-backend path that lets the modal\n"
        "    // pre-fill without a server. The shape is the same in both cases.\n"
        "    var endpoint = form.dataset.endpoint || '';\n"
        "    var urls = [];\n"
        "    if (endpoint) {\n"
        "      urls.push(endpoint + '/' + encodeURIComponent(slug) + '/prior');\n"
        "    }\n"
        "    urls.push('/pwp/kpi/' + encodeURIComponent(slug) + '.prior.json');\n"
        "    urls.push('/pwp/' + encodeURIComponent(slug) + '.prior.json');\n"
        "    urls.push(encodeURIComponent(slug) + '.prior.json');\n"
        "    return tryFetchSequence(urls, 0);\n"
        "  }\n"
        "\n"
        "  function tryFetchSequence(urls, idx) {\n"
        "    if (idx >= urls.length) return Promise.resolve(null);\n"
        "    return fetch(urls[idx], { credentials: 'same-origin' }).then(function (resp) {\n"
        "      if (!resp.ok) return tryFetchSequence(urls, idx + 1);\n"
        "      return resp.json();\n"
        "    }).then(function (data) {\n"
        "      if (!data || !data.form) return null;\n"
        "      var ctx = data.form.context || {};\n"
        "      setVal('pwp-kpi-modal-primary-goal', ctx.primary_goal || '');\n"
        "      setVal('pwp-kpi-modal-funnel-ideas', ctx.funnel_ideas || '');\n"
        "      setVal('pwp-kpi-modal-traffic-patterns', ctx.traffic_patterns || '');\n"
        "      setVal('pwp-kpi-modal-platform', ctx.platform || '');\n"
        "      setVal('pwp-kpi-modal-notes', ctx.notes || '');\n"
        "      var sources = Array.isArray(ctx.data_sources) ? ctx.data_sources : [];\n"
        "      var boxes = form.querySelectorAll('input[name=\"data_sources\"]');\n"
        "      for (var i = 0; i < boxes.length; i++) {\n"
        "        boxes[i].checked = sources.indexOf(boxes[i].value) !== -1;\n"
        "      }\n"
        "      return data;\n"
        "    }).catch(function () { return null; });\n"
        "  }\n"
        "\n"
        "  function open(slug, domain, kind) {\n"
        "    setVal('pwp-kpi-modal-slug', slug);\n"
        "    setVal('pwp-kpi-modal-domain', domain);\n"
        "    setVal('pwp-kpi-modal-kind', kind || 'init');\n"
        "    setText('pwp-kpi-modal-context-name', slug);\n"
        "    setText('pwp-kpi-modal-context-slug', slug);\n"
        "    setText('pwp-kpi-modal-context-domain', domain);\n"
        "    var badge = $('pwp-kpi-modal-kind-badge');\n"
        "    if (badge) badge.textContent = (kind || 'init');\n"
        "    // Reset fields (init flow).\n"
        "    setVal('pwp-kpi-modal-primary-goal', '');\n"
        "    setVal('pwp-kpi-modal-funnel-ideas', '');\n"
        "    setVal('pwp-kpi-modal-traffic-patterns', '');\n"
        "    setVal('pwp-kpi-modal-platform', '');\n"
        "    setVal('pwp-kpi-modal-notes', '');\n"
        "    var boxes = form.querySelectorAll('input[name=\"data_sources\"]');\n"
        "    for (var i = 0; i < boxes.length; i++) boxes[i].checked = false;\n"
        "    feedback.textContent = '';\n"
        "    feedback.className = 'pwp-kpi-modal-feedback';\n"
        "    modal.hidden = false;\n"
        "    // Refinement: pre-fill from prior submission.\n"
        "    if (kind === 'refinement') {\n"
        "      prefillFromSubmission(slug);\n"
        "    }\n"
        "    var first = $('pwp-kpi-modal-primary-goal');\n"
        "    if (first) first.focus();\n"
        "    document.addEventListener('keydown', escHandler);\n"
        "  }\n"
        "\n"
        "  function close() {\n"
        "    modal.hidden = true;\n"
        "    document.removeEventListener('keydown', escHandler);\n"
        "  }\n"
        "\n"
        "  function escHandler(e) {\n"
        "    if (e.key === 'Escape') close();\n"
        "  }\n"
        "\n"
        "  function buildPayload() {\n"
        "    var fd = new FormData(form);\n"
        "    var checked = [];\n"
        "    var boxes = form.querySelectorAll('input[name=\"data_sources\"]:checked');\n"
        "    for (var i = 0; i < boxes.length; i++) checked.push(boxes[i].value);\n"
        "    var payload = {\n"
        "      site_slug: fd.get('site_slug') || '',\n"
        "      site_domain: fd.get('site_domain') || '',\n"
        "      form_version: 1,\n"
        "      kind: fd.get('kind') || 'init',\n"
        "      csrf_token: fd.get('csrf_token') || '',\n"
        "      context: {\n"
        "        primary_goal: fd.get('primary_goal') || '',\n"
        "        funnel_ideas: fd.get('funnel_ideas') || '',\n"
        "        traffic_patterns: fd.get('traffic_patterns') || '',\n"
        "        platform: fd.get('platform') || '',\n"
        "        notes: fd.get('notes') || '',\n"
        "        data_sources: checked,\n"
        "      },\n"
        "    };\n"
        "    return payload;\n"
        "  }\n"
        "\n"
        "  function fallbackDownload(payload) {\n"
        "    // No backend — emit the payload as a JSON download the user can\n"
        "    // hand to the agent via the operator CLI.\n"
        "    var blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });\n"
        "    var url = URL.createObjectURL(blob);\n"
        "    var a = document.createElement('a');\n"
        "    a.href = url;\n"
        "    a.download = 'funnel-config-' + (payload.site_slug || 'unknown') + '-' + (payload.kind || 'init') + '.json';\n"
        "    document.body.appendChild(a);\n"
        "    a.click();\n"
        "    document.body.removeChild(a);\n"
        "    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);\n"
        "    return true;\n"
        "  }\n"
        "\n"
        "  function showFeedback(text, klass) {\n"
        "    feedback.textContent = text;\n"
        "    feedback.className = 'pwp-kpi-modal-feedback ' + (klass || '');\n"
        "  }\n"
        "\n"
        "  form.addEventListener('submit', function (e) {\n"
        "    e.preventDefault();\n"
        "    var primary = $('pwp-kpi-modal-primary-goal').value.trim();\n"
        "    if (!primary) {\n"
        "      showFeedback('Primary goal is required.', 'pwp-kpi-modal-feedback-error');\n"
        "      return;\n"
        "    }\n"
        "    var payload = buildPayload();\n"
        "    var submitBtn = $('pwp-kpi-modal-submit');\n"
        "    submitBtn.disabled = true;\n"
        "    showFeedback('Submitting…', 'pwp-kpi-modal-feedback-pending');\n"
        "    var endpoint = form.dataset.endpoint || '';\n"
        "    if (!endpoint) {\n"
        "      fallbackDownload(payload);\n"
        "      showFeedback('No backend endpoint configured. JSON downloaded — hand it to the agent.', 'pwp-kpi-modal-feedback-warn');\n"
        "      submitBtn.disabled = false;\n"
        "      return;\n"
        "    }\n"
        "    fetch(endpoint, {\n"
        "      method: 'POST',\n"
        "      headers: {\n"
        "        'Content-Type': 'application/json',\n"
        "        'X-CSRF-Token': payload.csrf_token,\n"
        "      },\n"
        "      credentials: 'same-origin',\n"
        "      body: JSON.stringify(payload),\n"
        "    }).then(function (resp) {\n"
        "      if (resp.status === 404) {\n"
        "        fallbackDownload(payload);\n"
        "        showFeedback('No backend at ' + endpoint + ' — JSON downloaded.', 'pwp-kpi-modal-feedback-warn');\n"
        "        return null;\n"
        "      }\n"
        "      if (!resp.ok) {\n"
        "        return resp.text().then(function (txt) {\n"
        "          throw new Error('HTTP ' + resp.status + ': ' + (txt || ''));\n"
        "        });\n"
        "      }\n"
        "      return resp.json();\n"
        "    }).then(function (data) {\n"
        "      if (!data) return;\n"
        "      var url = data.linear_issue_url || data.issue_url || '';\n"
        "      var ident = data.linear_issue_identifier || data.issue_identifier || '';\n"
        "      showFeedback('Dispatched to Linear · ' + (ident || url || 'submitted'), 'pwp-kpi-modal-feedback-ok');\n"
        "      if (url) {\n"
        "        setTimeout(function () { window.open(url, '_blank', 'noopener'); }, 200);\n"
        "      }\n"
        "    }).catch(function (err) {\n"
        "      showFeedback('Submit failed: ' + (err.message || err), 'pwp-kpi-modal-feedback-error');\n"
        "    }).then(function () {\n"
        "      submitBtn.disabled = false;\n"
        "    });\n"
        "  });\n"
        "\n"
        "  // Wire up close handlers.\n"
        "  var closers = modal.querySelectorAll('[data-pwp-kpi-modal-close]');\n"
        "  for (var i = 0; i < closers.length; i++) {\n"
        "    closers[i].addEventListener('click', close);\n"
        "  }\n"
        "  document.addEventListener('keydown', escHandler);\n"
        "\n"
        "  window.pwpKpiModal = { open: open, close: close };\n"
        "})();\n"
        "</script>\n"
    )


# ── Modal CSS (scoped to .pwp-kpi-modal selectors) ────────────────────────
def render_modal_css() -> str:
    """Return the modal CSS, scoped so it doesn't bleed into the rest of
    the dashboard. The dashboard already includes `pwp-publish-kpi.css`;
    this block is appended to that stylesheet.
    """
    return """
/* ── pwp-kpi funnel-config modal (Phase 4.2) ─────────────────────────── */
.pwp-kpi-modal {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding-top: 8vh;
  font-family: var(--pwp-font-base, system-ui, -apple-system, sans-serif);
  color: var(--pwp-fg, #1f2937);
}
.pwp-kpi-modal[hidden] { display: none; }
.pwp-kpi-modal-backdrop {
  position: absolute;
  inset: 0;
  background: rgba(15, 23, 42, 0.55);
  backdrop-filter: blur(2px);
}
.pwp-kpi-modal-panel {
  position: relative;
  width: min(640px, 92vw);
  max-height: 84vh;
  overflow-y: auto;
  background: var(--pwp-bg, #fff);
  border-radius: 12px;
  box-shadow: 0 24px 48px rgba(15, 23, 42, 0.25);
  padding: 1.5rem 1.75rem;
  box-sizing: border-box;
}
.pwp-kpi-modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 1rem;
  border-bottom: 1px solid rgba(15, 23, 42, 0.08);
  padding-bottom: 0.75rem;
}
.pwp-kpi-modal-header h2 {
  margin: 0;
  font-size: 1.2rem;
  font-weight: 600;
}
.pwp-kpi-modal-close {
  background: transparent;
  border: 0;
  font-size: 1.5rem;
  line-height: 1;
  cursor: pointer;
  color: inherit;
  padding: 0 0.4rem;
  border-radius: 4px;
}
.pwp-kpi-modal-close:hover { background: rgba(15, 23, 42, 0.08); }
.pwp-kpi-modal-form { display: flex; flex-direction: column; gap: 0.85rem; }
.pwp-kpi-modal-field { display: flex; flex-direction: column; gap: 0.25rem; }
.pwp-kpi-modal-field label { font-weight: 500; font-size: 0.9rem; }
.pwp-kpi-modal-field input,
.pwp-kpi-modal-field textarea {
  font: inherit;
  padding: 0.5rem 0.6rem;
  border: 1px solid rgba(15, 23, 42, 0.18);
  border-radius: 6px;
  background: var(--pwp-bg, #fff);
  color: inherit;
}
.pwp-kpi-modal-field input:focus,
.pwp-kpi-modal-field textarea:focus {
  outline: 2px solid var(--pwp-accent, #2563eb);
  outline-offset: 1px;
}
.pwp-kpi-modal-field .required { color: #b91c1c; }
.pwp-kpi-modal-field .muted { color: #6b7280; font-weight: 400; }
.pwp-kpi-modal-fieldset {
  border: 1px solid rgba(15, 23, 42, 0.18);
  border-radius: 6px;
  padding: 0.6rem 0.8rem;
  margin: 0;
}
.pwp-kpi-modal-fieldset legend { padding: 0 0.4rem; font-weight: 500; font-size: 0.9rem; }
.pwp-kpi-modal-sources {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 0.35rem;
  margin-top: 0.4rem;
}
.pwp-kpi-modal-source-row {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.9rem;
  cursor: pointer;
}
.pwp-kpi-modal-context-banner {
  background: rgba(37, 99, 235, 0.07);
  border: 1px solid rgba(37, 99, 235, 0.25);
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  margin: 0;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.pwp-kpi-modal-kind-badge {
  margin-left: auto;
  font-size: 0.75rem;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.08);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #475569;
}
.pwp-kpi-modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
  margin-top: 0.5rem;
  border-top: 1px solid rgba(15, 23, 42, 0.08);
  padding-top: 0.75rem;
}
.pwp-kpi-modal-btn {
  font: inherit;
  padding: 0.5rem 1rem;
  border-radius: 6px;
  border: 1px solid transparent;
  cursor: pointer;
  font-weight: 500;
}
.pwp-kpi-modal-btn-primary {
  background: var(--pwp-accent, #2563eb);
  color: #fff;
  border-color: var(--pwp-accent, #2563eb);
}
.pwp-kpi-modal-btn-primary:hover { filter: brightness(1.05); }
.pwp-kpi-modal-btn-primary:disabled { opacity: 0.6; cursor: not-allowed; }
.pwp-kpi-modal-btn-secondary {
  background: transparent;
  color: inherit;
  border-color: rgba(15, 23, 42, 0.18);
}
.pwp-kpi-modal-btn-secondary:hover { background: rgba(15, 23, 42, 0.05); }
.pwp-kpi-modal-feedback {
  margin: 0;
  min-height: 1.2em;
  font-size: 0.85rem;
  color: #6b7280;
}
.pwp-kpi-modal-feedback-ok { color: #059669; }
.pwp-kpi-modal-feedback-pending { color: #2563eb; }
.pwp-kpi-modal-feedback-error { color: #b91c1c; }
.pwp-kpi-modal-feedback-warn { color: #b45309; }

/* ── pwp-kpi funnel-config site-row buttons ───────────────────────────── */
.pwp-kpi-site-actions {
  display: flex;
  gap: 0.5rem;
  margin-top: 0.5rem;
  flex-wrap: wrap;
}
.pwp-kpi-btn-configure,
.pwp-kpi-btn-edit-funnel {
  font: inherit;
  font-size: 0.85rem;
  padding: 0.35rem 0.8rem;
  border-radius: 6px;
  cursor: pointer;
  border: 1px solid rgba(15, 23, 42, 0.18);
  background: transparent;
  color: inherit;
  text-decoration: none;
  display: inline-block;
}
.pwp-kpi-btn-configure {
  background: var(--pwp-accent, #2563eb);
  color: #fff;
  border-color: var(--pwp-accent, #2563eb);
}
.pwp-kpi-btn-configure:hover { filter: brightness(1.05); }
.pwp-kpi-btn-edit-funnel {
  background: rgba(15, 23, 42, 0.04);
  border-color: rgba(15, 23, 42, 0.18);
}
.pwp-kpi-btn-edit-funnel:hover { background: rgba(15, 23, 42, 0.08); }
.pwp-kpi-site-has-submission::before {
  content: "✓";
  margin-right: 0.4rem;
  color: #059669;
  font-weight: bold;
}
"""


# ── Per-site rows: which button(s) to show ────────────────────────────────
def site_row_buttons(site: dict[str, Any]) -> str:
    """Return the HTML for the per-site buttons (Configure / Edit).

    Logic:
      - If a prior submission log exists for this site, show "Edit funnel".
      - Always show "Configure website KPIs" (the user can re-submit).
      - Wrap the buttons in a `<p class="pwp-kpi-site-actions">` block.
    """
    slug = site.get("slug", "")
    domain = site.get("domain", "")
    safe_slug = _esc(slug)
    safe_domain = _esc(domain)
    has_prior = load_prior_submission(slug) is not None
    primary_label = "Edit funnel" if has_prior else "Configure website KPIs"
    configure = (
        '<button type="button" class="pwp-kpi-btn-configure" '
        'data-pwp-kpi-modal-open="' + safe_slug + '" '
        'data-pwp-kpi-modal-domain="' + safe_domain + '" '
        'data-pwp-kpi-modal-kind="init">' + primary_label + "</button>"
    )
    buttons = [configure]
    if has_prior:
        buttons.append(
            '<button type="button" class="pwp-kpi-btn-edit-funnel" '
            'data-pwp-kpi-modal-open="' + safe_slug + '" '
            'data-pwp-kpi-modal-domain="' + safe_domain + '" '
            'data-pwp-kpi-modal-kind="refinement">'
            "Re-submit refinement</button>"
        )
    return '<p class="pwp-kpi-site-actions">' + "".join(buttons) + "</p>"


# ── Wire buttons to open the modal (JS) ──────────────────────────────────
def render_button_wiring_js() -> str:
    """Return a small JS shim that wires the per-site buttons to open
    the modal. Injected after the modal script so the controller exists.
    """
    return """
<script>
(function () {
  function openFromButton(btn) {
    var slug = btn.getAttribute('data-pwp-kpi-modal-open');
    var domain = btn.getAttribute('data-pwp-kpi-modal-domain');
    var kind = btn.getAttribute('data-pwp-kpi-modal-kind') || 'init';
    if (window.pwpKpiModal && window.pwpKpiModal.open) {
      window.pwpKpiModal.open(slug, domain, kind);
    } else {
      console.warn('pwp-kpi modal controller not loaded yet');
    }
  }
  function wire() {
    var btns = document.querySelectorAll('[data-pwp-kpi-modal-open]');
    for (var i = 0; i < btns.length; i++) {
      btns[i].addEventListener('click', function (e) {
        openFromButton(e.currentTarget);
      });
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', wire);
  } else {
    wire();
  }
})();
</script>
"""


__all__ = [
    "DATA_SOURCE_OPTIONS",
    "DEFAULT_SUBMIT_ENDPOINT",
    "FORM_VERSION",
    "SUBMISSION_LOG_DIR",
    "load_prior_submission",
    "render_button_wiring_js",
    "render_modal_css",
    "render_modal_html",
    "site_row_buttons",
    "write_prior_submission_json",
]


# ── Static prior-submission JSON (Phase 4.3) ────────────────────────────
def write_prior_submission_json(publish_root: Path) -> list[Path]:
    """Write one `<slug>.prior.json` file per site that has a prior submission.

    Phase 4.3 (F4 Edit funnel UI pre-fill): the modal's refinement flow
    pre-fills the form from `load_prior_submission(slug)`. The JS fetches
    `<form.dataset.endpoint>/<slug>/prior` — but the dashboard is static.
    So `build_dashboard` writes one prior-submission JSON per site into
    the publish root, and the modal's fetch picks the right one up from
    the same origin.

    Layout in publish_root::

        <publish_root>/<slug>.prior.json
            {"form": {...}, "linear_issue_identifier": "...", ...}

    Returns the list of files written (abs paths). Sites without a prior
    submission log are skipped silently.
    """
    publish_root = Path(publish_root)
    written: list[Path] = []
    if not SUBMISSION_LOG_DIR.exists():
        return written
    for log_path in SUBMISSION_LOG_DIR.glob("*.json"):
        slug = log_path.stem
        try:
            data = json.loads(log_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            not isinstance(data, dict)
            or "form" not in data
            or not isinstance(data["form"], dict)
        ):
            continue
        out_path = publish_root / f"{slug}.prior.json"
        out_path.write_text(
            json.dumps(data, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        written.append(out_path)
    return written
