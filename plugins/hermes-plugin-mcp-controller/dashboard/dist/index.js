(function () {
  const sdk = window.__HERMES_PLUGIN_SDK__;
  const registry = window.__HERMES_PLUGINS__;
  if (!sdk || !registry) {
    console.warn('[mcp-controller] Hermes plugin SDK not available');
    return;
  }

  const React = sdk.React;
  const { useState, useEffect, useCallback, useMemo } = React;
  const h = React.createElement;
  const fetchJSON = sdk.fetchJSON || sdk.api;
  const API_BASE = '/api/plugins/hermes-plugin-mcp-controller';

  // ── CSS style injection ──────────────────────────────────────
  function injectStyles() {
    if (document.getElementById('mcp-controller-styles')) return;
    const s = document.createElement('style');
    s.id = 'mcp-controller-styles';
    s.textContent = `
      :root {
        --bg-deep-space: #06070a;
        --bg-industrial: #0e1117;
        --bg-card: #151a22;
        --bg-card-hover: #1c232e;
        --bg-input: #090c10;
        --border-subtle: #252e3d;
        --border-active: #3b495c;
        --text-primary: #f5f6f7;
        --text-secondary: #8b9bb4;
        --text-muted: #536279;

        --color-connected: #2ECC71;
        --color-reconnecting: #F1C40F;
        --color-disconnected: #E74C3C;
        --color-accent: #9B59B6; /* Purple accent matching Prismatic Hub */
        --color-accent-glow: rgba(155, 89, 182, 0.4);

        --font-sans: 'Outfit', 'Inter', system-ui, sans-serif;
        --font-mono: 'JetBrains Mono', monospace;
        --radius-sm: 4px;
        --radius-md: 8px;
        --radius-lg: 12px;
        --transition-speed: 0.2s;
      }

      .mcp-root {
        font-family: var(--font-sans);
        color: var(--text-primary);
        max-width: 1400px;
        margin: 0 auto;
        box-sizing: border-box;
      }
      .mcp-root * {
        box-sizing: border-box;
      }

      .mcp-header {
        background: var(--bg-industrial);
        border: 1px solid var(--border-subtle);
        border-radius: var(--radius-md);
        padding: 16px 24px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        position: relative;
        overflow: hidden;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
        margin-bottom: 20px;
      }
      .mcp-header::before {
        content: '';
        position: absolute;
        top: 0; left: 0; right: 0;
        height: 3px;
        background: linear-gradient(90deg, #F39C12, #E67E22, #E74C3C, var(--color-accent), #3498DB, #2ECC71);
      }
      .mcp-header-brand {
        display: flex;
        align-items: center;
        gap: 12px;
      }
      .mcp-header-logo {
        width: 20px;
        height: 20px;
        background: linear-gradient(135deg, var(--color-accent), #3498DB);
        clip-path: polygon(0% 20%, 100% 20%, 100% 80%, 0% 80%);
        filter: drop-shadow(0 0 6px var(--color-accent-glow));
      }
      .mcp-header-title {
        font-size: 16px;
        font-weight: 800;
        letter-spacing: 0.5px;
        text-transform: uppercase;
        font-family: var(--font-mono);
        background: linear-gradient(90deg, #fff, var(--text-secondary));
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin: 0;
      }
      .mcp-header-tag {
        font-size: 9px;
        background: rgba(155, 89, 182, 0.15);
        border: 1px solid var(--color-accent);
        color: var(--color-accent);
        padding: 2px 8px;
        border-radius: 20px;
        font-weight: 700;
        text-transform: uppercase;
      }
      .mcp-header-meta {
        display: flex;
        align-items: center;
        gap: 16px;
        font-size: 12px;
        color: var(--text-secondary);
      }
      .mcp-live-indicator {
        display: flex;
        align-items: center;
        gap: 6px;
        background: rgba(46, 204, 113, 0.08);
        border: 1px solid rgba(46, 204, 113, 0.2);
        color: var(--color-connected);
        padding: 4px 10px;
        border-radius: var(--radius-sm);
        font-weight: 600;
      }
      .mcp-live-dot {
        width: 6px;
        height: 6px;
        background-color: var(--color-connected);
        border-radius: 50%;
        box-shadow: 0 0 8px var(--color-connected);
        animation: mcp-pulse 1.5s infinite;
      }

      .mcp-tabs {
        display: flex;
        gap: 8px;
        background: var(--bg-industrial);
        padding: 4px;
        border: 1px solid var(--border-subtle);
        border-radius: var(--radius-md);
        margin-bottom: 20px;
      }
      .mcp-tab-btn {
        background: transparent;
        border: none;
        color: var(--text-secondary);
        font-family: var(--font-sans);
        font-size: 13px;
        font-weight: 600;
        padding: 8px 16px;
        border-radius: var(--radius-sm);
        cursor: pointer;
        display: flex;
        align-items: center;
        gap: 8px;
        transition: all var(--transition-speed) ease;
        outline: none;
      }
      .mcp-tab-btn:hover {
        color: #fff;
        background: rgba(255, 255, 255, 0.03);
      }
      .mcp-tab-btn.active {
        color: #fff;
        background: var(--color-accent);
        box-shadow: 0 2px 10px rgba(155, 89, 182, 0.2);
      }

      .mcp-layout {
        display: grid;
        grid-template-columns: 380px 1fr;
        gap: 20px;
        align-items: start;
      }
      .mcp-panel {
        background: var(--bg-industrial);
        border: 1px solid var(--border-subtle);
        border-radius: var(--radius-md);
        padding: 20px;
        display: flex;
        flex-direction: column;
        gap: 16px;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
      }
      .mcp-panel-title {
        font-size: 14px;
        font-weight: 700;
        color: #fff;
        display: flex;
        align-items: center;
        justify-content: space-between;
        border-bottom: 1px solid var(--border-subtle);
        padding-bottom: 12px;
        margin: 0;
      }

      .mcp-card {
        background: var(--bg-card);
        border: 1px solid var(--border-subtle);
        border-radius: var(--radius-md);
        padding: 16px;
        cursor: pointer;
        display: flex;
        flex-direction: column;
        gap: 12px;
        transition: all var(--transition-speed) ease;
        position: relative;
        overflow: hidden;
      }
      .mcp-card:hover {
        background: var(--bg-card-hover);
        border-color: var(--border-active);
        transform: translateY(-1px);
      }
      .mcp-card.selected {
        border-color: var(--color-accent);
        box-shadow: inset 0 0 0 1px var(--color-accent), 0 0 12px rgba(155, 89, 182, 0.15);
      }
      .mcp-card::before {
        content: '';
        position: absolute;
        left: 0; top: 0; bottom: 0;
        width: 3px;
        background-color: transparent;
        transition: background-color var(--transition-speed) ease;
      }
      .mcp-card.healthy::before { background-color: var(--color-connected); }
      .mcp-card.degraded::before { background-color: var(--color-reconnecting); }
      .mcp-card.offline::before { background-color: var(--color-disconnected); }
      .mcp-card.disabled::before { background-color: var(--text-muted); }

      .mcp-status-dot {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        display: inline-block;
      }
      .mcp-status-dot.healthy { background-color: var(--color-connected); box-shadow: 0 0 6px var(--color-connected); }
      .mcp-status-dot.degraded { background-color: var(--color-reconnecting); box-shadow: 0 0 6px var(--color-reconnecting); }
      .mcp-status-dot.offline { background-color: var(--color-disconnected); }
      .mcp-status-dot.disabled { background-color: var(--text-muted); }

      .mcp-badge {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        font-size: 10px;
        font-weight: 700;
        text-transform: uppercase;
        padding: 2px 8px;
        border-radius: 12px;
        border: 1px solid transparent;
      }
      .mcp-badge.healthy { background: rgba(46, 205, 113, 0.1); border-color: rgba(46, 205, 113, 0.2); color: var(--color-connected); }
      .mcp-badge.degraded { background: rgba(241, 196, 15, 0.1); border-color: rgba(241, 196, 15, 0.2); color: var(--color-reconnecting); }
      .mcp-badge.offline { background: rgba(231, 76, 60, 0.1); border-color: rgba(231, 76, 60, 0.2); color: var(--color-disconnected); }
      .mcp-badge.disabled { background: rgba(255, 255, 255, 0.05); border-color: rgba(255, 255, 255, 0.08); color: var(--text-secondary); }

      .mcp-btn {
        background: var(--bg-input);
        border: 1px solid var(--border-subtle);
        color: var(--text-primary);
        padding: 6px 12px;
        font-size: 11px;
        font-weight: 700;
        border-radius: var(--radius-sm);
        cursor: pointer;
        transition: all var(--transition-speed) ease;
        display: flex;
        align-items: center;
        gap: 6px;
        font-family: var(--font-sans);
        outline: none;
        user-select: none;
      }
      .mcp-btn:hover:not(:disabled) {
        background: var(--bg-card-hover);
        border-color: var(--border-active);
        color: #fff;
      }
      .mcp-btn:disabled {
        opacity: 0.4;
        cursor: not-allowed;
      }
      .mcp-btn-primary {
        background: var(--color-accent);
        border-color: var(--color-accent);
        color: #fff;
      }
      .mcp-btn-primary:hover:not(:disabled) {
        background: rgba(155, 89, 182, 0.85);
        border-color: rgba(155, 89, 182, 0.85);
        box-shadow: 0 0 10px rgba(155, 89, 182, 0.3);
      }
      .mcp-btn-connect { color: var(--color-connected); border-color: rgba(46, 204, 113, 0.3); }
      .mcp-btn-connect:hover:not(:disabled) { background: rgba(46, 204, 113, 0.08) !important; border-color: var(--color-connected) !important; }
      .mcp-btn-disconnect { color: var(--color-disconnected); border-color: rgba(231, 76, 60, 0.3); }
      .mcp-btn-disconnect:hover:not(:disabled) { background: rgba(231, 76, 60, 0.08) !important; border-color: var(--color-disconnected) !important; }
      .mcp-btn-reconnect { color: var(--color-reconnecting); border-color: rgba(241, 196, 15, 0.3); }
      .mcp-btn-reconnect:hover:not(:disabled) { background: rgba(241, 196, 15, 0.08) !important; border-color: var(--color-reconnecting) !important; }

      .mcp-stat-row {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 8px;
        font-size: 11px;
        border-top: 1px solid rgba(255, 255, 255, 0.03);
        padding-top: 10px;
      }
      .mcp-stat-item {
        display: flex;
        flex-direction: column;
        gap: 2px;
      }
      .mcp-stat-lbl { color: var(--text-secondary); }
      .mcp-stat-val { color: #fff; font-weight: 600; font-family: var(--font-mono); }

      .mcp-tools-list {
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      .mcp-tool-card {
        background: var(--bg-card);
        border: 1px solid var(--border-subtle);
        border-radius: var(--radius-md);
        overflow: hidden;
      }
      .mcp-tool-card-summary {
        padding: 12px 16px;
        cursor: pointer;
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: 13px;
        font-weight: 600;
        color: #fff;
        user-select: none;
      }
      .mcp-tool-card-summary:hover {
        background: var(--bg-card-hover);
      }
      .mcp-tool-card-details {
        padding: 16px;
        border-top: 1px solid rgba(255, 255, 255, 0.03);
        background: rgba(0, 0, 0, 0.1);
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      .mcp-tool-desc {
        font-size: 12px;
        color: var(--text-secondary);
        line-height: 1.5;
      }
      .mcp-code-pre {
        background: var(--bg-input);
        border: 1px solid var(--border-subtle);
        border-radius: var(--radius-sm);
        padding: 10px;
        font-family: var(--font-mono);
        font-size: 11px;
        color: #a29bfe;
        overflow-x: auto;
        margin: 0;
      }

      .mcp-console-viewport {
        background: #050608;
        border: 1px solid var(--border-subtle);
        border-radius: var(--radius-md);
        padding: 16px;
        font-family: var(--font-mono);
        font-size: 11px;
        line-height: 1.6;
        height: 380px;
        overflow-y: auto;
        display: flex;
        flex-direction: column;
        gap: 4px;
        box-shadow: inset 0 2px 10px rgba(0,0,0,0.5);
      }
      .mcp-log-line {
        white-space: pre-wrap;
        color: #b9c2ce;
        border-left: 2px solid transparent;
        padding-left: 8px;
        display: flex;
        gap: 8px;
      }
      .mcp-log-line.info { border-color: #3498DB; }
      .mcp-log-line.info .mcp-log-lvl { color: #3498DB; font-weight: bold; }
      .mcp-log-line.success { border-color: var(--color-connected); }
      .mcp-log-line.success .mcp-log-lvl { color: var(--color-connected); font-weight: bold; }
      .mcp-log-line.warn { border-color: var(--color-reconnecting); }
      .mcp-log-line.warn .mcp-log-lvl { color: var(--color-reconnecting); font-weight: bold; }
      .mcp-log-line.error { border-color: var(--color-disconnected); }
      .mcp-log-line.error .mcp-log-lvl { color: var(--color-disconnected); font-weight: bold; }

      .mcp-form-group {
        display: flex;
        flex-direction: column;
        gap: 6px;
        width: 100%;
      }
      .mcp-form-group label {
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        color: var(--text-secondary);
        font-weight: 700;
      }
      .mcp-form-control {
        background: var(--bg-input);
        border: 1px solid var(--border-subtle);
        border-radius: var(--radius-sm);
        padding: 8px 12px;
        font-size: 12px;
        color: #fff;
        font-family: var(--font-sans);
        outline: none;
        width: 100%;
        transition: border-color var(--transition-speed) ease;
      }
      .mcp-form-control:focus {
        border-color: var(--color-accent);
      }
      .mcp-form-control-textarea {
        resize: vertical;
        min-height: 60px;
        font-family: var(--font-mono);
      }

      .mcp-modal-overlay {
        position: fixed;
        top: 0; left: 0; right: 0; bottom: 0;
        background: rgba(0, 0, 0, 0.7);
        backdrop-filter: blur(4px);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 1000;
      }
      .mcp-modal-content {
        background: var(--bg-industrial);
        border: 1px solid var(--border-active);
        border-radius: var(--radius-lg);
        width: 500px;
        max-width: 95%;
        padding: 24px;
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
        display: flex;
        flex-direction: column;
        gap: 16px;
        position: relative;
      }

      @keyframes mcp-pulse {
        0% { transform: scale(0.95); opacity: 0.5; }
        50% { transform: scale(1.2); opacity: 1; }
        100% { transform: scale(0.95); opacity: 0.5; }
      }

      @media (max-width: 1024px) {
        .mcp-layout {
          grid-template-columns: 1fr;
        }
      }

      @media (max-width: 768px) {
        .mcp-header {
          flex-direction: column;
          align-items: flex-start;
          gap: 12px;
        }
        .mcp-header-meta {
          width: 100%;
          justify-content: space-between;
        }
        .mcp-tabs {
          width: 100%;
        }
        .mcp-tab-btn {
          flex: 1;
          justify-content: center;
          padding: 8px 4px;
          font-size: 11px;
        }
        .mcp-stat-row {
          grid-template-columns: 1fr;
        }
      }
    `;
    document.head.appendChild(s);
  }

  // ── Icons ──────────────────────────────────────────
  const Icons = {
    server: () => h('svg', { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' },
      h('rect', { x: 2, y: 2, width: 20, height: 8, rx: 2, ry: 2 }),
      h('rect', { x: 2, y: 14, width: 20, height: 8, rx: 2, ry: 2 }),
      h('line', { x1: 6, y1: 6, x2: 6.01, y2: 6 }),
      h('line', { x1: 6, y1: 18, x2: 6.01, y2: 18 })),
    tool: () => h('svg', { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' },
      h('path', { d: 'M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 0-7.94-7.94L11 6.36l-6.91 6.91a2.12 2.12 0 0 0 3 3L14 9.4l.7-3.1M3 21v-3' })),
    clock: () => h('svg', { width: 12, height: 12, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2 },
      h('circle', { cx: 12, cy: 12, r: 10 }),
      h('polyline', { points: '12 6 12 12 16 14' })),
    log: () => h('svg', { width: 14, height: 14, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' },
      h('path', { d: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z' }),
      h('polyline', { points: '14 2 14 8 20 8' }),
      h('line', { x1: 16, y1: 13, x2: 8, y2: 13 }),
      h('line', { x1: 16, y1: 17, x2: 8, y2: 17 })),
    chevron: () => h('svg', { width: 12, height: 12, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' },
      h('polyline', { points: '6 9 12 15 18 9' })),
    chevronUp: () => h('svg', { width: 12, height: 12, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' },
      h('polyline', { points: '18 15 12 9 6 15' })),
    refresh: () => h('svg', { width: 13, height: 13, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' },
      h('polyline', { points: '23 4 23 10 17 10' }),
      h('polyline', { points: '1 20 1 14 7 14' }),
      h('path', { d: 'M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15' })),
    plus: () => h('svg', { width: 12, height: 12, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' },
      h('line', { x1: 12, y1: 5, x2: 12, y2: 19 }),
      h('line', { x1: 5, y1: 12, x2: 19, y2: 12 })),
    connect: () => h('svg', { width: 12, height: 12, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' },
      h('polygon', { points: '6 4 20 12 6 20 6 4' })),
    disconnect: () => h('svg', { width: 12, height: 12, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round', strokeLinejoin: 'round' },
      h('rect', { x: 5, y: 5, width: 14, height: 14, rx: 2, ry: 2 }))
  };

  // ── Routing Helpers ─────────────────────────────────
  const getCurrentTab = () => {
    const path = window.location.pathname || '';
    if (path.endsWith('/tools')) return 'tools';
    if (path.endsWith('/logs')) return 'logs';
    const hash = window.location.hash || '';
    if (hash.includes('/tools')) return 'tools';
    if (hash.includes('/logs')) return 'logs';
    return 'servers';
  };

  // ── Main App Component ──────────────────────────────
  function McpController() {
    const [tab, setTab] = useState(getCurrentTab());
    const [statusData, setStatusData] = useState(null);
    const [statusLoading, setStatusLoading] = useState(true);
    const [statusError, setStatusError] = useState(null);
    const [selectedServerName, setSelectedServerName] = useState(null);
    const [logServerFilter, setLogServerFilter] = useState('all');
    const [logs, setLogs] = useState([]);
    const [logsAutoScroll, setLogsAutoScroll] = useState(true);
    const [toolSearch, setToolSearch] = useState('');
    const [allTools, setAllTools] = useState([]);
    const [toolsLoading, setToolsLoading] = useState(true);
    const [showAddModal, setShowAddModal] = useState(false);
    const [addLoading, setAddLoading] = useState(false);
    const [actionLoading, setActionLoading] = useState({});
    const [notifications, setNotifications] = useState([]);

    const [expandedTools, setExpandedTools] = useState({});
    const [expandedServerCards, setExpandedServerCards] = useState({});
    const [selectedServerLogs, setSelectedServerLogs] = useState([]);

    // Add Server form state
    const [srvName, setSrvName] = useState('');
    const [srvCmd, setSrvCmd] = useState('');
    const [srvArgs, setSrvArgs] = useState('');
    const [srvEnv, setSrvEnv] = useState('');

    useEffect(() => {
      injectStyles();
      const handler = () => setTab(getCurrentTab());
      window.addEventListener('popstate', handler);
      return () => window.removeEventListener('popstate', handler);
    }, []);

    const handleSwitchTab = (tabId) => {
      setTab(tabId);
      const newPath = tabId === 'servers' ? '/mcp-controller' : `/mcp-controller/${tabId}`;
      if (window.location.hash) {
        window.location.hash = newPath;
      } else {
        window.history.pushState({}, '', newPath);
      }
    };

    const addNotification = (msg) => {
      const id = Date.now();
      setNotifications(n => [...n, { id, msg }]);
      setTimeout(() => setNotifications(n => n.filter(x => x.id !== id)), 4000);
    };

    // ── Data Fetching ─────────────────────────────────
    const fetchStatus = useCallback(() => {
      fetchJSON(API_BASE + '/status')
        .then(d => {
          setStatusData(d);
          setStatusError(null);
          // Set first server selected by default if none selected
          if (d.servers && d.servers.length > 0 && !selectedServerName) {
            setSelectedServerName(d.servers[0].name);
          }
        })
        .catch(e => setStatusError(e.message))
        .finally(() => setStatusLoading(false));
    }, [selectedServerName]);

    const fetchLogs = useCallback(() => {
      const serverParam = logServerFilter !== 'all' ? '&server=' + logServerFilter : '';
      fetchJSON(API_BASE + '/logs?lines=60' + serverParam)
        .then(d => setLogs(d.entries || []))
        .catch(() => {});
    }, [logServerFilter]);

    const fetchSelectedServerLogs = useCallback(() => {
      if (!selectedServerName) return;
      fetchJSON(API_BASE + '/logs?lines=30&server=' + selectedServerName)
        .then(d => setSelectedServerLogs(d.entries || []))
        .catch(() => {});
    }, [selectedServerName]);

    const fetchTools = useCallback(() => {
      setToolsLoading(true);
      fetchJSON(API_BASE + '/tools')
        .then(d => {
          const list = [];
          const serversData = d.servers || {};
          Object.keys(serversData).forEach(srvName => {
            const srvTools = serversData[srvName].tool_list || [];
            srvTools.forEach(t => {
              list.push({ ...t, server: srvName });
            });
          });
          setAllTools(list);
        })
        .catch(() => setAllTools([]))
        .finally(() => setToolsLoading(false));
    }, []);

    // Status polling
    useEffect(() => {
      fetchStatus();
      const iv = setInterval(fetchStatus, 4000);
      return () => clearInterval(iv);
    }, [fetchStatus]);

    // Logs polling
    useEffect(() => {
      fetchLogs();
      const iv = setInterval(fetchLogs, 5000);
      return () => clearInterval(iv);
    }, [fetchLogs]);

    // Selected server logs polling
    useEffect(() => {
      fetchSelectedServerLogs();
      const iv = setInterval(fetchSelectedServerLogs, 4000);
      return () => clearInterval(iv);
    }, [fetchSelectedServerLogs]);

    // Tools loading on tab change
    useEffect(() => {
      if (tab === 'tools') {
        fetchTools();
      }
    }, [tab, fetchTools]);

    // Auto scroll logs viewport
    useEffect(() => {
      if (logsAutoScroll && tab === 'logs') {
        const vp = document.getElementById('mcp-logs-viewport');
        if (vp) {
          vp.scrollTop = vp.scrollHeight;
        }
      }
    }, [logs, logsAutoScroll, tab]);

    // ── Server Actions ────────────────────────────────
    const triggerServerAction = (serverName, action) => {
      setActionLoading(prev => ({ ...prev, [serverName + '-' + action]: true }));
      fetchJSON(API_BASE + '/action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server: serverName, action })
      })
        .then(res => {
          addNotification(`${action.toUpperCase()} action triggered for ${serverName}`);
          fetchStatus();
          fetchLogs();
        })
        .catch(e => {
          addNotification(`Failed to ${action} ${serverName}: ${e.message}`);
        })
        .finally(() => {
          setActionLoading(prev => ({ ...prev, [serverName + '-' + action]: false }));
        });
    };

    const handleAddServerSubmit = (e) => {
      e.preventDefault();
      setAddLoading(true);
      fetchJSON(API_BASE + '/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: srvName,
          command: srvCmd,
          args: srvArgs,
          env: srvEnv
        })
      })
        .then(res => {
          addNotification(`Successfully added server ${srvName}`);
          setShowAddModal(false);
          setSrvName('');
          setSrvCmd('');
          setSrvArgs('');
          setSrvEnv('');
          fetchStatus();
          fetchLogs();
        })
        .catch(e => {
          addNotification(`Failed to add server: ${e.message}`);
        })
        .finally(() => {
          setAddLoading(false);
        });
    };

    // Filter tools based on search text
    const filteredTools = useMemo(() => {
      const query = toolSearch.toLowerCase().strip ? toolSearch.toLowerCase().trim() : toolSearch.toLowerCase();
      if (!query) return allTools;
      return allTools.filter(t => 
        t.name.toLowerCase().includes(query) || 
        t.description.toLowerCase().includes(query) || 
        t.server.toLowerCase().includes(query)
      );
    }, [allTools, toolSearch]);

    // Selected server object
    const selectedServer = useMemo(() => {
      if (!statusData || !statusData.servers) return null;
      return statusData.servers.find(s => s.name === selectedServerName) || statusData.servers[0];
    }, [statusData, selectedServerName]);

    // Selected server logs are loaded into the selectedServerLogs state variable via API polling

    // Render stats/summary row
    const renderSummary = () => {
      if (!statusData) return null;
      return h('div', {
        style: {
          display: 'flex', gap: 16, flexWrap: 'wrap', padding: '10px 16px',
          borderRadius: 10, background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.05)',
          marginBottom: 20, fontSize: 12, alignItems: 'center'
        }
      },
        h('span', null, h('strong', null, statusData.total_servers), ' configured'),
        h('span', { style: { color: '#4ecdc4' } }, '● ', statusData.healthy_count, ' online'),
        h('span', { style: { color: '#ffd200' } }, '● ', statusData.degraded_count, ' degraded'),
        h('span', { style: { color: '#ff6b6b' } }, '● ', statusData.offline_count, ' offline'),
        h('span', { style: { opacity: 0.4, marginLeft: 'auto' } }, 'Sync: ', statusData.last_sync)
      );
    };

    return h('div', { className: 'mcp-root' },
      // Notifications Toast Stack
      notifications.length > 0 && h('div', {
        style: { position: 'fixed', top: 20, right: 20, zIndex: 10000, display: 'flex', flexDirection: 'column', gap: 8 }
      },
        notifications.map(n => h('div', {
          key: n.id,
          style: {
            padding: '10px 16px', borderRadius: 8, background: 'rgba(155, 89, 182, 0.95)',
            boxShadow: '0 4px 15px rgba(0,0,0,0.5)', color: '#fff', fontSize: 12, fontWeight: 600,
            borderLeft: '4px solid #fff'
          }
        }, n.msg))
      ),

      // Brand Header
      h('header', { className: 'mcp-header' },
        h('div', { className: 'mcp-header-brand' },
          h('div', { className: 'mcp-header-logo' }),
          h('h1', { className: 'mcp-header-title' }, 'MCP Controller'),
          h('span', { className: 'mcp-header-tag' }, 'Daemon-Swarm')
        ),
        h('div', { className: 'mcp-header-meta' },
          h('div', { className: 'mcp-live-indicator' },
            h('div', { className: 'mcp-live-dot' }),
            'DAEMON ACTIVE'
          )
        )
      ),

      // Tabs Navigation
      h('div', { className: 'mcp-tabs' },
        h('button', {
          className: `mcp-tab-btn ${tab === 'servers' ? 'active' : ''}`,
          onClick: () => handleSwitchTab('servers')
        }, '🔌 Servers'),
        h('button', {
          className: `mcp-tab-btn ${tab === 'tools' ? 'active' : ''}`,
          onClick: () => handleSwitchTab('tools')
        }, '🛠️ Tools Registry'),
        h('button', {
          className: `mcp-tab-btn ${tab === 'logs' ? 'active' : ''}`,
          onClick: () => handleSwitchTab('logs')
        }, '📜 Daemon Logs')
      ),

      statusLoading && h('div', { style: { textAlign: 'center', padding: '40px', opacity: 0.5 } }, 'Loading MCP Controller status...'),
      statusError && h('div', {
        style: { padding: 14, background: 'rgba(231, 76, 60, 0.1)', color: 'var(--color-disconnected)', border: '1px solid rgba(231, 76, 60, 0.2)', borderRadius: 8, fontSize: 13, marginBottom: 20 }
      }, `Error loading daemon state: ${statusError}`),

      !statusLoading && !statusError && h('div', null,
        renderSummary(),

        // ── VIEW 1: SERVERS ──────────────────────────────────────
        tab === 'servers' && h('div', { className: 'mcp-layout' },
          // Left column - Server Cards List
          h('div', { className: 'mcp-panel' },
            h('div', { className: 'mcp-panel-title' },
              h('span', null, 'Active MCP Servers'),
              h('button', {
                className: 'mcp-btn mcp-btn-primary',
                style: { padding: '4px 10px', fontSize: 10 },
                onClick: () => setShowAddModal(true)
              }, Icons.plus(), ' Add Server')
            ),
            h('div', { style: { display: 'flex', flexDirection: 'column', gap: 12 } },
              statusData?.servers && statusData.servers.map(s => {
                const isSelected = s.name === selectedServerName;
                const isCardExpanded = !!expandedServerCards[s.name];
                return h('div', {
                  key: s.name,
                  className: `mcp-card ${s.health} ${isSelected ? 'selected' : ''}`,
                  onClick: () => setSelectedServerName(s.name)
                },
                  h('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' } },
                    h('div', null,
                      h('h3', { style: { margin: 0, fontSize: 14, fontWeight: 700, color: '#fff' } }, s.name),
                      h('span', { style: { fontSize: 10, opacity: 0.5, fontFamily: 'var(--font-mono)' } },
                        s.transport === 'stdio' ? 'stdio transport' : 'http transport'
                      )
                    ),
                    h('span', { className: `mcp-badge ${s.health}` },
                      h('span', { className: `mcp-status-dot ${s.health}` }),
                      s.health.toUpperCase()
                    )
                  ),
                  h('div', { className: 'mcp-stat-row' },
                    h('div', { className: 'mcp-stat-item' },
                      h('span', { className: 'mcp-stat-lbl' }, 'Tools Count'),
                      h('span', { className: 'mcp-stat-val' }, s.tools_count)
                    ),
                    h('div', { className: 'mcp-stat-item' },
                      h('span', { className: 'mcp-stat-lbl' }, 'Latency'),
                      h('span', { className: 'mcp-stat-val' }, s.last_response_ms ? `${s.last_response_ms}ms` : 'N/A')
                    )
                  ),
                  // Expandable Tools Trigger inside card (Mobile stacked optimization)
                  h('div', {
                    style: {
                      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                      marginTop: 8, paddingTop: 8, borderTop: '1px solid rgba(255, 255, 255, 0.04)',
                      fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', cursor: 'pointer'
                    },
                    onClick: (e) => {
                      e.stopPropagation();
                      setExpandedServerCards(prev => ({ ...prev, [s.name]: !isCardExpanded }));
                    }
                  },
                    h('span', null, isCardExpanded ? 'Hide Tools List' : 'Expand Tools List'),
                    isCardExpanded ? Icons.chevronUp() : Icons.chevron()
                  ),
                  isCardExpanded && h('div', {
                    style: {
                      marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6,
                      maxHeight: 220, overflowY: 'auto', paddingRight: 4
                    },
                    onClick: (e) => e.stopPropagation()
                  },
                    s.tool_list && s.tool_list.length > 0 ? s.tool_list.map(t => {
                      const isToolExpanded = !!expandedTools[`card-${s.name}-${t.name}`];
                      return h('div', {
                        key: t.name,
                        style: {
                          background: 'rgba(0, 0, 0, 0.2)', border: '1px solid var(--border-subtle)',
                          borderRadius: 4, padding: 8, display: 'flex', flexDirection: 'column', gap: 4
                        }
                      },
                        h('div', {
                          style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', cursor: 'pointer' },
                          onClick: () => setExpandedTools(prev => ({ ...prev, [`card-${s.name}-${t.name}`]: !isToolExpanded }))
                        },
                          h('span', { style: { fontWeight: 700, color: '#fff', fontFamily: 'var(--font-mono)', fontSize: 10 } }, t.name),
                          isToolExpanded ? Icons.chevronUp() : Icons.chevron()
                        ),
                        h('p', { style: { color: 'var(--text-secondary)', fontSize: 10, margin: 0 } }, t.description || 'No description.'),
                        isToolExpanded && h('pre', {
                          className: 'mcp-code-pre',
                          style: { marginTop: 4, padding: 6, fontSize: 9, overflowX: 'auto', background: '#090c10' }
                        }, JSON.stringify(t.schema, null, 2))
                      );
                    }) : h('div', { style: { color: 'var(--text-muted)', fontStyle: 'italic', fontSize: 10 } }, 'No tools registered.')
                  )
                );
              })
            )
          ),

          // Right column - Server Detail & Config
          h('div', { className: 'mcp-panel' },
            selectedServer ? h('div', { style: { display: 'flex', flexDirection: 'column', gap: 20 } },
              // Header
              h('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--border-subtle)', paddingBottom: 16 } },
                h('div', null,
                  h('h2', { style: { margin: 0, fontSize: 18, color: '#fff' } }, selectedServer.name),
                  h('p', { style: { margin: '4px 0 0 0', fontSize: 11, color: 'var(--text-secondary)', fontFamily: 'var(--font-mono)' } },
                    selectedServer.pid ? `Process PID: ${selectedServer.pid}` : 'Process status: INACTIVE'
                  )
                ),
                h('div', { style: { display: 'flex', gap: 6 } },
                  h('button', {
                    className: 'mcp-btn mcp-btn-connect',
                    onClick: () => triggerServerAction(selectedServer.name, 'connect'),
                    disabled: selectedServer.health === 'healthy' || actionLoading[selectedServer.name + '-connect']
                  }, Icons.connect(), actionLoading[selectedServer.name + '-connect'] ? '...' : 'Connect'),
                  h('button', {
                    className: 'mcp-btn mcp-btn-disconnect',
                    onClick: () => triggerServerAction(selectedServer.name, 'disconnect'),
                    disabled: selectedServer.health === 'offline' || selectedServer.health === 'disabled' || actionLoading[selectedServer.name + '-disconnect']
                  }, Icons.disconnect(), actionLoading[selectedServer.name + '-disconnect'] ? '...' : 'Disconnect'),
                  h('button', {
                    className: 'mcp-btn mcp-btn-reconnect',
                    onClick: () => triggerServerAction(selectedServer.name, 'reconnect'),
                    disabled: actionLoading[selectedServer.name + '-reconnect']
                  }, Icons.refresh(), actionLoading[selectedServer.name + '-reconnect'] ? '...' : 'Reconnect')
                )
              ),

              // Server config details
              h('div', null,
                h('h4', { style: { margin: '0 0 10px 0', fontSize: 11, textTransform: 'uppercase', color: 'var(--text-secondary)' } }, 'Configuration Parameters'),
                h('table', { style: { width: '100%', borderCollapse: 'collapse', fontSize: 12, fontFamily: 'var(--font-mono)' } },
                  h('tbody', null,
                    h('tr', null,
                      h('td', { style: { padding: '6px 0', color: 'var(--text-secondary)', width: 140 } }, 'Command:'),
                      h('td', { style: { padding: '6px 0', color: '#fff' } }, selectedServer.command || 'N/A')
                    ),
                    selectedServer.args && selectedServer.args.length > 0 && h('tr', null,
                      h('td', { style: { padding: '6px 0', color: 'var(--text-secondary)' } }, 'Arguments:'),
                      h('td', { style: { padding: '6px 0', color: '#fff' } }, selectedServer.args.join(' '))
                    ),
                    h('tr', null,
                      h('td', { style: { padding: '6px 0', color: 'var(--text-secondary)' } }, 'Transport Type:'),
                      h('td', { style: { padding: '6px 0', color: '#fff' } }, selectedServer.transport.toUpperCase())
                    ),
                    selectedServer.pid && h('tr', null,
                      h('td', { style: { padding: '6px 0', color: 'var(--text-secondary)' } }, 'System Stats:'),
                      h('td', { style: { padding: '6px 0', color: '#fff' } }, `CPU: ${selectedServer.cpu_pct}%, Memory: ${selectedServer.mem_mb} MB`)
                    ),
                    h('tr', null,
                      h('td', { style: { padding: '6px 0', color: 'var(--text-secondary)' } }, 'Timeout Threshold:'),
                      h('td', { style: { padding: '6px 0', color: '#fff' } }, `${selectedServer.timeout}s`)
                    )
                  )
                )
              ),

              // Server specific tool list (with descriptions and schemas)
              h('div', null,
                h('h4', { style: { margin: '0 0 10px 0', fontSize: 11, textTransform: 'uppercase', color: 'var(--text-secondary)' } }, `Registered Tools (${selectedServer.tools_count})`),
                h('div', { style: { display: 'flex', flexDirection: 'column', gap: 8 } },
                  selectedServer.tool_list && selectedServer.tool_list.length > 0 ? selectedServer.tool_list.map(t => {
                    const isExpanded = !!expandedTools[selectedServer.name + '-' + t.name];
                    return h('div', {
                      key: t.name,
                      className: 'mcp-tool-card'
                    },
                      h('div', {
                        className: 'mcp-tool-card-summary',
                        onClick: () => setExpandedTools(prev => ({ ...prev, [selectedServer.name + '-' + t.name]: !isExpanded })),
                        style: { padding: '8px 12px', fontSize: 12 }
                      },
                        h('span', { style: { fontWeight: 700, fontFamily: 'var(--font-mono)' } }, t.name),
                        isExpanded ? Icons.chevronUp() : Icons.chevron()
                      ),
                      isExpanded && h('div', { className: 'mcp-tool-card-details', style: { padding: 12 } },
                        h('p', { className: 'mcp-tool-desc', style: { fontSize: 11, margin: '0 0 10px 0' } }, t.description || 'No description provided.'),
                        h('div', null,
                          h('div', { style: { fontSize: 9, fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-secondary)', marginBottom: 4 } }, 'Parameters Schema'),
                          h('pre', { className: 'mcp-code-pre', style: { padding: 8, fontSize: 10 } }, JSON.stringify(t.schema, null, 2))
                        )
                      )
                    );
                  }) : h('span', { style: { fontSize: 11, color: 'var(--text-muted)' } }, 'No tools bound to this server connection.')
                )
              ),

              // Local server logs
              h('div', { style: { borderTop: '1px solid var(--border-subtle)', paddingTop: 16 } },
                h('h4', { style: { margin: '0 0 10px 0', fontSize: 11, textTransform: 'uppercase', color: 'var(--text-secondary)' } }, 'Server Specific Log Stream'),
                h('div', { className: 'mcp-console-viewport', style: { height: 180 } },
                  selectedServerLogs.length > 0 ? selectedServerLogs.map((l, idx) => h('div', {
                    key: idx,
                    className: `mcp-log-line ${l.level}`
                  },
                    h('span', { style: { color: 'var(--text-muted)', minWidth: 50 } }, l.ts),
                    h('span', { className: 'mcp-log-lvl', style: { minWidth: 40 } }, l.level.toUpperCase()),
                    h('span', { style: { opacity: 0.8 } }, l.msg)
                  )) : h('div', { style: { opacity: 0.3, padding: 20, textAlign: 'center' } }, 'No server specific log entries.')
                )
              )
            ) : h('div', { style: { textAlign: 'center', padding: '40px', opacity: 0.4 } }, 'Select an MCP server from the list to view details.')
          ),

          // Modal Add Server Form
          showAddModal && h('div', { className: 'mcp-modal-overlay' },
            h('div', { className: 'mcp-modal-content' },
              h('h3', { style: { margin: 0, paddingBottom: 10, borderBottom: '1px solid var(--border-subtle)', color: '#fff' } }, 'Register New MCP Server'),
              h('form', { onSubmit: handleAddServerSubmit, style: { display: 'flex', flexDirection: 'column', gap: 14 } },
                h('div', { className: 'mcp-form-group' },
                  h('label', { htmlFor: 'add-srv-name' }, 'Server Name'),
                  h('input', {
                    id: 'add-srv-name', className: 'mcp-form-control', placeholder: 'e.g. filesystem-mcp',
                    value: srvName, onChange: e => setSrvName(e.target.value), required: true
                  })
                ),
                h('div', { className: 'mcp-form-group' },
                  h('label', { htmlFor: 'add-srv-cmd' }, 'Startup Command'),
                  h('input', {
                    id: 'add-srv-cmd', className: 'mcp-form-control', placeholder: 'e.g. npx -y @modelcontextprotocol/server-filesystem',
                    value: srvCmd, onChange: e => setSrvCmd(e.target.value), required: true
                  })
                ),
                h('div', { className: 'mcp-form-group' },
                  h('label', { htmlFor: 'add-srv-args' }, 'Arguments (comma-separated)'),
                  h('input', {
                    id: 'add-srv-args', className: 'mcp-form-control', placeholder: 'e.g. /home/ubuntu/work',
                    value: srvArgs, onChange: e => setSrvArgs(e.target.value)
                  })
                ),
                h('div', { className: 'mcp-form-group' },
                  h('label', { htmlFor: 'add-srv-env' }, 'Environment Variables (KEY=VAL, comma-separated)'),
                  h('textarea', {
                    id: 'add-srv-env', className: 'mcp-form-control mcp-form-control-textarea', placeholder: 'e.g. LOG_LEVEL=debug',
                    value: srvEnv, onChange: e => setSrvEnv(e.target.value)
                  })
                ),
                h('div', { style: { display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 10 } },
                  h('button', {
                    type: 'button', className: 'mcp-btn',
                    onClick: () => setShowAddModal(false), disabled: addLoading
                  }, 'Cancel'),
                  h('button', {
                    type: 'submit', className: 'mcp-btn mcp-btn-primary',
                    disabled: addLoading
                  }, addLoading ? 'Saving...' : 'Register Server')
                )
              )
            )
          )
        ),

        // ── VIEW 2: TOOLS REGISTRY ────────────────────────────────
        tab === 'tools' && h('div', { className: 'mcp-panel' },
          h('div', { className: 'mcp-panel-title' },
            h('span', null, 'Global Tools Registry'),
            h('input', {
              type: 'text', className: 'mcp-form-control', placeholder: 'Filter tools by name, description, server...',
              value: toolSearch, onChange: e => setToolSearch(e.target.value),
              style: { padding: '4px 10px', fontSize: 12, width: 280 }
            })
          ),

          toolsLoading && h('div', { style: { textAlign: 'center', padding: 30, opacity: 0.5 } }, 'Loading tools catalogue...'),

          !toolsLoading && h('div', { className: 'mcp-tools-list' },
            filteredTools.length > 0 ? filteredTools.map(t => {
              const isExpanded = !!expandedTools[t.server + '-' + t.name];
              return h('div', { key: t.server + '-' + t.name, className: 'mcp-tool-card' },
                h('div', {
                  className: 'mcp-tool-card-summary',
                  onClick: () => setExpandedTools(prev => ({ ...prev, [t.server + '-' + t.name]: !isExpanded }))
                },
                  h('div', { className: 'mcp-tool-name-container', style: { display: 'flex', alignItems: 'center', gap: 8 } },
                    h('span', {
                      style: {
                        fontSize: 10, background: 'rgba(155, 89, 182, 0.1)', color: 'var(--color-accent)',
                        padding: '2px 6px', borderRadius: 4, fontFamily: 'var(--font-mono)'
                      }
                    }, t.server),
                    h('span', { style: { fontWeight: 700 } }, t.name)
                  ),
                  isExpanded ? Icons.chevronUp() : Icons.chevron()
                ),
                isExpanded && h('div', { className: 'mcp-tool-card-details' },
                  h('p', { className: 'mcp-tool-desc' }, t.description),
                  h('div', null,
                    h('div', { style: { fontSize: 10, fontWeight: 700, textTransform: 'uppercase', color: 'var(--text-secondary)', marginBottom: 4 } }, 'Parameters Schema'),
                    h('pre', { className: 'mcp-code-pre' }, JSON.stringify(t.schema, null, 2))
                  )
                )
              );
            }) : h('div', { style: { textAlign: 'center', padding: '40px', opacity: 0.4 } }, 'No tools found matching search parameters.')
          )
        ),

        // ── VIEW 3: DAEMON LOGS ───────────────────────────────────
        tab === 'logs' && h('div', { className: 'mcp-panel' },
          h('div', { className: 'mcp-panel-title' },
            h('span', null, 'Unified Daemon Logs'),
            h('div', { style: { display: 'flex', gap: 12, alignItems: 'center' } },
              h('div', { style: { display: 'flex', gap: 4, alignItems: 'center', fontSize: 11 } },
                h('input', {
                  type: 'checkbox', id: 'logs-auto-scroll',
                  checked: logsAutoScroll, onChange: e => setLogsAutoScroll(e.target.checked)
                }),
                h('label', { htmlFor: 'logs-auto-scroll', style: { color: 'var(--text-secondary)', cursor: 'pointer' } }, 'Auto Scroll')
              )
            )
          ),

          // Filters row
          h('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap' } },
            h('div', { style: { display: 'flex', gap: 6, flexWrap: 'wrap' } },
              h('button', {
                className: `mcp-btn ${logServerFilter === 'all' ? 'mcp-btn-primary' : ''}`,
                style: { padding: '4px 10px', fontSize: 10 },
                onClick: () => setLogServerFilter('all')
              }, 'ALL SECTIONS'),
              statusData?.servers?.map(s => h('button', {
                key: s.name,
                className: `mcp-btn ${logServerFilter === s.name ? 'mcp-btn-primary' : ''}`,
                style: { padding: '4px 10px', fontSize: 10 },
                onClick: () => setLogServerFilter(s.name)
              }, s.name.toUpperCase())),
              h('button', {
                className: `mcp-btn ${logServerFilter === 'system' ? 'mcp-btn-primary' : ''}`,
                style: { padding: '4px 10px', fontSize: 10 },
                onClick: () => setLogServerFilter('system')
              }, 'SYSTEM')
            ),
            h('button', {
              className: 'mcp-btn',
              style: { padding: '4px 10px', fontSize: 10 },
              onClick: () => setLogs([])
            }, 'Clear Screen')
          ),

          // Log Viewport Console
          h('div', { id: 'mcp-logs-viewport', className: 'mcp-console-viewport' },
            logs.length > 0 ? logs.map((l, idx) => h('div', {
              key: idx,
              className: `mcp-log-line ${l.level}`
            },
              h('span', { style: { color: 'var(--text-muted)', minWidth: 50, userSelect: 'none' } }, l.ts || ''),
              h('span', { className: 'mcp-log-lvl', style: { minWidth: 40, userSelect: 'none' } }, l.level.toUpperCase()),
              h('span', { style: { color: 'var(--color-accent)', minWidth: 50, fontWeight: 700, userSelect: 'none' } }, l.server.toUpperCase()),
              h('span', { style: { opacity: 0.95 } }, l.msg)
            )) : h('div', { style: { padding: '40px', textAlign: 'center', opacity: 0.3 } }, 'Log console empty.')
          )
        )
      )
    );
  }

  // Register plugin React component
  registry.register('hermes-plugin-mcp-controller', McpController);
})();
