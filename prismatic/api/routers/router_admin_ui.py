"""prismatic/api/routers/router_admin_ui.py — Minimal HTML admin UI.

GRO-555: Provides a single-page HTML UI at ``/api/router/ui`` for
inspecting and editing the router config. Plain HTML + vanilla JS so
no new dependency is required — renders the live JSON snapshot,
lets an admin edit routes/models, and POSTs changes back through
the JSON API.

The page is intentionally light: no build step, no framework. It
expects to talk to the sibling router-config API and to receive a
bearer token via the same auth header. The simplest deploys serve
this from the same FastAPI app — no separate web tier needed.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["router-admin-ui"])

ADMIN_UI_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Prismatic Router Admin</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {
      --bg: #0e1117; --panel: #161b22; --border: #30363d;
      --text: #e6edf3; --muted: #8b949e; --accent: #2f81f7;
      --ok: #3fb950; --warn: #d29922; --err: #f85149;
    }
    body { font-family: ui-sans-serif, system-ui, sans-serif;
           background: var(--bg); color: var(--text);
           margin: 0; padding: 1.5rem; }
    h1 { font-size: 1.4rem; margin: 0 0 1rem; }
    h2 { font-size: 1.1rem; margin: 1.5rem 0 0.5rem; border-bottom: 1px solid var(--border); padding-bottom: 0.25rem; }
    .panel { background: var(--panel); border: 1px solid var(--border);
             border-radius: 6px; padding: 1rem; margin-bottom: 1rem; }
    label { display: block; font-size: 0.85rem; color: var(--muted); margin-top: 0.5rem; }
    input, select, textarea { width: 100%; background: #0d1117; color: var(--text);
                                border: 1px solid var(--border); border-radius: 4px;
                                padding: 0.4rem; box-sizing: border-box;
                                font-family: ui-monospace, monospace; font-size: 0.85rem; }
    button { background: var(--accent); color: white; border: 0;
             border-radius: 4px; padding: 0.4rem 0.8rem; margin-top: 0.5rem;
             cursor: pointer; font-size: 0.85rem; }
    button.danger { background: var(--err); }
    button.secondary { background: var(--border); color: var(--text); }
    table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
    th, td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--border); }
    th { color: var(--muted); font-weight: 500; }
    .muted { color: var(--muted); font-size: 0.8rem; }
    #status { padding: 0.5rem; margin-bottom: 1rem; border-radius: 4px;
              font-family: ui-monospace, monospace; font-size: 0.8rem; }
    #status.ok { background: rgba(63,185,80,0.15); color: var(--ok); }
    #status.err { background: rgba(248,81,73,0.15); color: var(--err); }
    .row { display: flex; gap: 0.5rem; align-items: flex-end; flex-wrap: wrap; }
    .row > div { flex: 1 1 200px; }
  </style>
</head>
<body>
  <h1>Prismatic Router Admin <span class="muted" id="version"></span></h1>

  <div id="status" class="muted">loading…</div>

  <div class="panel">
    <label for="apiToken">API bearer token (sent only from your browser)</label>
    <input id="apiToken" type="password" placeholder="sk-admin..." />
    <div class="row">
      <button onclick="loadAll()">Reload</button>
      <button class="danger" onclick="resetAll()">Reset to defaults</button>
    </div>
  </div>

  <h2>Routes</h2>
  <div class="panel">
    <table id="routesTable">
      <thead><tr>
        <th>Agent</th><th>Chain</th><th>Weight</th><th>Enabled</th><th>Cooldown (s)</th><th></th>
      </tr></thead>
      <tbody></tbody>
    </table>
    <h3>Upsert route</h3>
    <div class="row">
      <div><label>Label<input id="rLabel" placeholder="agy" /></label></div>
      <div><label>Priority chain (comma-separated)<input id="rChain" placeholder="claude-opus,claude-sonnet" /></label></div>
      <div><label>Weight<input id="rWeight" type="number" step="0.1" value="1.0" /></label></div>
      <div><label>Cooldown (s)<input id="rCooldown" type="number" value="300" /></label></div>
      <div><label>Enabled<select id="rEnabled"><option value="true">true</option><option value="false">false</option></select></label></div>
    </div>
    <button onclick="upsertRoute()">Save route</button>
    <button class="secondary" onclick="document.getElementById('rLabel').value='';document.getElementById('rChain').value='';">Clear</button>
  </div>

  <h2>Models</h2>
  <div class="panel">
    <table id="modelsTable">
      <thead><tr>
        <th>Canonical</th><th>AGY flag</th><th>Tier</th><th>Enabled</th><th></th>
      </tr></thead>
      <tbody></tbody>
    </table>
    <h3>Upsert model</h3>
    <div class="row">
      <div><label>Canonical<input id="mName" placeholder="claude-opus" /></label></div>
      <div><label>AGY flag<input id="mFlag" placeholder="Claude Opus 4.6 (Thinking)" /></label></div>
      <div><label>Tier<select id="mTier"><option>premium</option><option>fallback</option><option>free</option></select></label></div>
      <div><label>Enabled<select id="mEnabled"><option value="true">true</option><option value="false">false</option></select></label></div>
    </div>
    <button onclick="upsertModel()">Save model</button>
    <button class="secondary" onclick="document.getElementById('mName').value='';document.getElementById('mFlag').value='';">Clear</button>
  </div>

  <p class="muted">API base: <code>/api/v1/router</code>. Auth: Bearer token.</p>

<script>
const BASE = "/api/v1/router";
function tok() { return document.getElementById("apiToken").value.trim(); }
function headers() {
  const h = {"Content-Type": "application/json"};
  if (tok()) h["Authorization"] = "Bearer " + tok();
  return h;
}
function setStatus(msg, ok) {
  const el = document.getElementById("status");
  el.textContent = msg;
  el.className = ok ? "ok" : "err";
}
async function api(method, path, body) {
  const r = await fetch(BASE + path, {method, headers: headers(), body: body ? JSON.stringify(body) : undefined});
  const txt = await r.text();
  let data; try { data = txt ? JSON.parse(txt) : null; } catch { data = txt; }
  if (!r.ok) {
    setStatus("ERR " + r.status + ": " + (data && data.detail ? data.detail : txt), false);
    throw new Error(data);
  }
  return data;
}
async function loadAll() {
  try {
    const cfg = await api("GET", "/config");
    document.getElementById("version").textContent = "v" + cfg.version;
    const tbody = document.querySelector("#routesTable tbody");
    tbody.innerHTML = "";
    for (const [label, r] of Object.entries(cfg.routes || {})) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${label}</td><td>${(r.priority_chain||[]).join(" → ")}</td><td>${r.weight}</td><td>${r.enabled}</td><td>${r.cooldown_seconds}</td>`;
      const del = document.createElement("button");
      del.textContent = "Delete"; del.className = "danger";
      del.onclick = async () => { if (confirm("Delete route " + label + "?")) { await api("DELETE", "/routes/" + encodeURIComponent(label)); loadAll(); } };
      const td = document.createElement("td"); td.appendChild(del); tr.appendChild(td);
      tbody.appendChild(tr);
    }
    const mtbody = document.querySelector("#modelsTable tbody");
    mtbody.innerHTML = "";
    for (const [name, m] of Object.entries(cfg.models || {})) {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${name}</td><td>${m.agy_model_flag}</td><td>${m.tier}</td><td>${m.enabled}</td>`;
      const del = document.createElement("button");
      del.textContent = "Delete"; del.className = "danger";
      del.onclick = async () => { if (confirm("Delete model " + name + "?")) { await api("DELETE", "/models/" + encodeURIComponent(name)); loadAll(); } };
      const td = document.createElement("td"); td.appendChild(del); tr.appendChild(td);
      mtbody.appendChild(tr);
    }
    setStatus("Loaded v" + cfg.version + " — " + Object.keys(cfg.routes||{}).length + " routes, " + Object.keys(cfg.models||{}).length + " models", true);
  } catch (e) { /* setStatus already called */ }
}
async function upsertRoute() {
  const label = document.getElementById("rLabel").value.trim();
  const chain = document.getElementById("rChain").value.split(",").map(s => s.trim()).filter(Boolean);
  if (!label || !chain.length) { setStatus("Label and chain are required", false); return; }
  try {
    await api("PUT", "/routes/" + encodeURIComponent(label), {
      priority_chain: chain,
      weight: parseFloat(document.getElementById("rWeight").value),
      cooldown_seconds: parseInt(document.getElementById("rCooldown").value, 10),
      enabled: document.getElementById("rEnabled").value === "true",
    });
    setStatus("Route " + label + " saved", true);
    loadAll();
  } catch (e) {}
}
async function upsertModel() {
  const name = document.getElementById("mName").value.trim();
  const flag = document.getElementById("mFlag").value.trim();
  if (!name || !flag) { setStatus("Canonical name and AGY flag are required", false); return; }
  try {
    await api("PUT", "/models/" + encodeURIComponent(name), {
      canonical_name: name,
      agy_model_flag: flag,
      tier: document.getElementById("mTier").value,
      enabled: document.getElementById("mEnabled").value === "true",
    });
    setStatus("Model " + name + " saved", true);
    loadAll();
  } catch (e) {}
}
async function resetAll() {
  if (!confirm("Reset all router config to factory defaults?")) return;
  try { await api("PUT", "/config/reset"); setStatus("Reset to defaults", true); loadAll(); } catch (e) {}
}
loadAll();
</script>
</body>
</html>
"""


@router.get("/router/ui", response_class=HTMLResponse)
async def admin_ui() -> str:
    """Serve the single-page router admin UI."""
    return ADMIN_UI_HTML
