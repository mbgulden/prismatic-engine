---
name: prismatic-plugin-development
description: >-
  Develop Hermes dashboard plugins for the Prismatic Engine — plugin anatomy,
  manifest format, dashboard widget patterns, API integration, build pipeline,
  and installation workflow. Covers the 8-plugin ecosystem: lock dashboard,
  MCP controller, command deck, Prismatic Hub, activity stream, swarm manager,
  VRAM observability, and workspace navigator.
---

# Prismatic Plugin Development

## Trigger
Load this skill when developing a new Hermes dashboard plugin for the
Prismatic Engine, debugging an existing plugin, or adding a new widget
type to the plugin ecosystem.

## Overview

Prismatic Engine plugins are Hermes dashboard extensions that provide
operational visibility and control panels for agent orchestration. Each
plugin is a self-contained directory under `plugins/` with a manifest,
HTML/CSS/JS dashboard, and optional Python API backend.

## Plugin Anatomy

```
plugins/hermes-plugin-<name>/
├── manifest.json          ← Plugin metadata + Hermes registration
├── README.md              ← Usage and development docs
├── dashboard/
│   ├── index.html         ← Dashboard widget HTML
│   ├── index.js           ← Widget logic (vanilla JS)
│   ├── style.css          ← Widget styles
│   └── dist/              ← Built output (committed for CF Pages deploy)
│       ├── index.html
│       └── index.js
└── plugin_api.py          ← Optional: Python backend API for data
```

## manifest.json Format

```json
{
  "name": "hermes-plugin-<name>",
  "version": "1.0.0",
  "display_name": "Human-Readable Name",
  "description": "What this plugin does in one sentence.",
  "author": "Prismatic Engine",
  "license": "MIT",
  "hermes": {
    "min_version": "2.0.0",
    "dashboard": {
      "widget": true,
      "route": "/plugins/<name>",
      "title": "Widget Title",
      "icon": "🔧",
      "width": "full",
      "refresh_interval_seconds": 30
    },
    "api": {
      "enabled": false,
      "port": 0,
      "routes": []
    }
  },
  "dependencies": {
    "hermes": ">=2.0.0"
  }
}
```

### Manifest Field Reference

| Field | Required | Description |
|---|---|---|
| `name` | Yes | Plugin directory name (kebab-case, `hermes-plugin-` prefix) |
| `version` | Yes | Semver |
| `display_name` | Yes | Human-readable name for the dashboard |
| `description` | Yes | One-sentence description |
| `hermes.min_version` | Yes | Minimum Hermes Agent version required |
| `hermes.dashboard.widget` | Yes | Set to `true` to register as a dashboard widget |
| `hermes.dashboard.route` | Yes | URL route for the widget (e.g., `/plugins/lock-dashboard`) |
| `hermes.dashboard.title` | Yes | Widget title shown in the dashboard nav |
| `hermes.dashboard.icon` | No | Emoji icon for the nav |
| `hermes.dashboard.width` | No | `full`, `half`, or `third` |
| `hermes.dashboard.refresh_interval_seconds` | No | Auto-refresh interval (0 = no auto-refresh) |
| `hermes.api.enabled` | No | Set to `true` if the plugin has a Python API backend |
| `hermes.api.port` | No | Port for the API backend (0 = auto-assign) |
| `hermes.api.routes` | No | Array of API route definitions |

## Dashboard Widget Patterns

### Pattern 1: Status Table

For plugins that display operational data in a table (lock dashboard, swarm
manager, activity stream):

```javascript
// dashboard/index.js
class StatusTableWidget {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    this.data = [];
    this.refreshInterval = null;
  }

  async fetchData() {
    const response = await fetch('/api/plugins/<name>/status');
    this.data = await response.json();
    this.render();
  }

  render() {
    this.container.innerHTML = `
      <table class="prismatic-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Status</th>
            <th>Last Seen</th>
          </tr>
        </thead>
        <tbody>
          ${this.data.map(item => `
            <tr>
              <td>${item.name}</td>
              <td><span class="status-${item.status}">${item.status}</span></td>
              <td>${item.last_seen}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  }

  start(refreshMs = 30000) {
    this.fetchData();
    if (refreshMs > 0) {
      this.refreshInterval = setInterval(() => this.fetchData(), refreshMs);
    }
  }

  stop() {
    if (this.refreshInterval) {
      clearInterval(this.refreshInterval);
    }
  }
}

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => {
  const widget = new StatusTableWidget('widget-root');
  widget.start(30000);
});
```

### Pattern 2: Control Panel

For plugins with interactive controls (command deck, MCP controller):

```javascript
class ControlPanelWidget {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    this.buttons = [];
  }

  async sendCommand(action, payload = {}) {
    const response = await fetch('/api/plugins/<name>/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, ...payload })
    });
    const result = await response.json();
    this.showResult(result);
  }

  render() {
    this.container.innerHTML = `
      <div class="control-panel">
        <div class="control-group">
          <h3>Agent Controls</h3>
          <button onclick="widget.sendCommand('pause', {agent: 'ned'})">
            Pause Ned
          </button>
          <button onclick="widget.sendCommand('resume', {agent: 'ned'})">
            Resume Ned
          </button>
        </div>
        <div id="result-panel" class="result-panel"></div>
      </div>
    `;
  }

  showResult(result) {
    document.getElementById('result-panel').innerHTML = `
      <pre>${JSON.stringify(result, null, 2)}</pre>
    `;
  }
}

const widget = new ControlPanelWidget('widget-root');
document.addEventListener('DOMContentLoaded', () => widget.render());
```

### Pattern 3: Real-Time Stream

For plugins with live data feeds (activity stream):

```javascript
class StreamWidget {
  constructor(containerId) {
    this.container = document.getElementById(containerId);
    this.eventSource = null;
    this.events = [];
    this.maxEvents = 50;
  }

  connect(streamUrl) {
    this.eventSource = new EventSource(streamUrl);
    this.eventSource.onmessage = (event) => {
      const data = JSON.parse(event.data);
      this.events.unshift(data);
      if (this.events.length > this.maxEvents) {
        this.events = this.events.slice(0, this.maxEvents);
      }
      this.render();
    };
    this.eventSource.onerror = () => {
      this.container.innerHTML +=
        '<div class="stream-error">Connection lost. Retrying...</div>';
    };
  }

  render() {
    this.container.innerHTML = this.events.map(e => `
      <div class="stream-event stream-event-${e.level}">
        <span class="event-time">${e.timestamp}</span>
        <span class="event-agent">[${e.agent}]</span>
        <span class="event-message">${e.message}</span>
      </div>
    `).join('');
  }
}
```

## Optional: Python API Backend

For plugins that need server-side data:

```python
# plugin_api.py
from flask import Flask, jsonify, request

app = Flask(__name__)

@app.route('/api/plugins/<name>/status')
def status():
    # Query data source (Linear, systemd, swarm_locks.json, etc.)
    return jsonify({"status": "ok", "data": [...]})

@app.route('/api/plugins/<name>/command', methods=['POST'])
def command():
    data = request.json
    # Execute command
    return jsonify({"success": True, "action": data.get('action')})

if __name__ == '__main__':
    app.run(port=0)  # 0 = auto-assign
```

## Building for Deployment

```bash
# In plugin directory
cd plugins/hermes-plugin-<name>

# Build (minify JS, optimize CSS)
python3 ../../scripts/build-plugin.py

# Output goes to dashboard/dist/
# Commit dist/ for Cloudflare Pages deployment
git add dashboard/dist/
git commit -m "[Ned] Build plugin <name> for deployment"
```

## Installation

```bash
# From the Prismatic Hub
prismatic-engine plugin install hermes-plugin-<name>

# Or manually
cp -r plugins/hermes-plugin-<name> ~/.hermes/profiles/orchestrator/plugins/
hermes dashboard reload
```

## Existing Plugin Ecosystem

| Plugin | Description | Status |
|---|---|---|
| `hermes-plugin-lock-dashboard` | File locking status and conflict resolution | Active |
| `hermes-plugin-mcp-controller` | MCP server management and health | Active |
| `hermes-plugin-orchestrator-command-deck` | Agent controls (pause, resume, halt) | Active |
| `hermes-plugin-prismatic-hub` | Plugin marketplace and discovery | Active |
| `hermes-plugin-realtime-activity-stream` | Live agent event feed | Active |
| `hermes-plugin-swarm-manager` | Multi-agent orchestration overview | Active |
| `hermes-plugin-vram-observability` | GPU VRAM usage monitoring | Active |
| `hermes-plugin-workspace-tree-navigator` | Visual workspace file tree | Active |

## Pitfalls

- ❌ **Missing dist/ directory:** Cloudflare Pages serves committed files.
  Build before pushing — missing `dist/` means the plugin won't render.
- ❌ **Hardcoded API ports:** Use `port: 0` in the manifest for auto-assign.
  Hardcoded ports collide across plugins.
- ❌ **No error handling in fetch:** Every `fetch()` call should have a
  `.catch()` that renders an error state, not a blank widget.
- ❌ **CSS leaking:** Widget styles should be scoped to a container class
  (e.g., `.prismatic-widget-<name>`) to avoid bleeding into the dashboard.
- ❌ **Refresh without cleanup:** If a widget starts a `setInterval`, it
  MUST clean up on `window.unload` to prevent memory leaks.
- ❌ **Manifest not updated:** When adding new fields to `manifest.json`,
  increment the `version` field and test with `hermes dashboard reload`.

See also: `lane-governance` skill, `agent-soul-template` skill.
