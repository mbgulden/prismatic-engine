(function () {
  const sdk = window.__HERMES_PLUGIN_SDK__;
  const registry = window.__HERMES_PLUGINS__;
  if (!sdk || !registry) return;

  const React = sdk.React;
  const h = React.createElement;

  // --------------------------------------------------
  // 1. Inject Stylesheet into Document Head
  // --------------------------------------------------
  const styleEl = document.createElement("style");
  styleEl.innerHTML = `
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
      --color-accent: #9B59B6;
      --color-accent-glow: rgba(155, 89, 182, 0.4);
      --color-amber: #F39C12;

      --font-sans: 'Inter', system-ui, -apple-system, sans-serif;
      --font-mono: 'JetBrains Mono', monospace;

      --radius-sm: 4px;
      --radius-md: 8px;
      --radius-lg: 12px;
      --transition-speed: 0.2s;
    }

    .mcp-container {
      max-width: 1440px;
      width: 100%;
      margin: 0 auto;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: 20px;
      font-family: var(--font-sans);
      color: var(--text-primary);
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
      width: 24px;
      height: 24px;
      background: linear-gradient(135deg, var(--color-accent), #3498DB);
      clip-path: polygon(0% 20%, 100% 20%, 100% 80%, 0% 80%);
    }

    .mcp-header-title {
      font-size: 18px;
      font-weight: 800;
      letter-spacing: 0.5px;
      text-transform: uppercase;
      font-family: var(--font-mono);
      background: linear-gradient(90deg, #fff, var(--text-secondary));
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }

    .mcp-header-tag {
      font-size: 10px;
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

    @keyframes mcp-pulse {
      0% { transform: scale(0.95); opacity: 0.5; }
      50% { transform: scale(1.2); opacity: 1; }
      100% { transform: scale(0.95); opacity: 0.5; }
    }

    .mcp-nav-tabs {
      display: flex;
      gap: 8px;
      background: var(--bg-industrial);
      padding: 4px;
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-md);
    }

    .mcp-nav-tab {
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
    }

    .mcp-nav-tab:hover {
      color: #fff;
      background: rgba(255, 255, 255, 0.03);
    }

    .mcp-nav-tab.active {
      color: #fff;
      background: var(--color-accent);
      box-shadow: 0 2px 10px rgba(155, 89, 182, 0.2);
    }

    .mcp-dashboard-layout {
      display: grid;
      grid-template-columns: 380px 1fr;
      gap: 20px;
      align-items: start;
    }

    .mcp-panel-card {
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
      font-size: 15px;
      font-weight: 700;
      color: #fff;
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid var(--border-subtle);
      padding-bottom: 12px;
    }

    .mcp-server-list {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    .mcp-server-card {
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

    .mcp-server-card:hover {
      background: var(--bg-card-hover);
      border-color: var(--border-active);
    }

    .mcp-server-card.selected {
      border-color: var(--color-accent);
      box-shadow: inset 0 0 0 1px var(--color-accent), 0 0 15px rgba(155, 89, 182, 0.15);
    }

    .mcp-server-card::before {
      content: '';
      position: absolute;
      left: 0; top: 0; bottom: 0;
      width: 3px;
      background-color: transparent;
      transition: background-color var(--transition-speed) ease;
    }

    .mcp-server-card.connected::before { background-color: var(--color-connected); }
    .mcp-server-card.reconnecting::before { background-color: var(--color-reconnecting); }
    .mcp-server-card.disconnected::before { background-color: var(--color-disconnected); }

    .mcp-server-card-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
    }

    .mcp-server-card-info h3 {
      font-size: 14px;
      font-weight: 700;
      color: #fff;
      margin: 0 0 2px 0;
    }

    .mcp-server-card-info span {
      font-size: 11px;
      color: var(--text-secondary);
      font-family: var(--font-mono);
    }

    .mcp-status-badge {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      padding: 2px 8px;
      border-radius: 12px;
      border: 1px solid transparent;
    }

    .mcp-status-badge.connected {
      background: rgba(46, 204, 113, 0.1);
      border-color: rgba(46, 204, 113, 0.2);
      color: var(--color-connected);
    }

    .mcp-status-badge.reconnecting {
      background: rgba(241, 196, 15, 0.1);
      border-color: rgba(241, 196, 15, 0.2);
      color: var(--color-reconnecting);
    }

    .mcp-status-badge.disconnected {
      background: rgba(231, 76, 60, 0.1);
      border-color: rgba(231, 76, 60, 0.2);
      color: var(--color-disconnected);
    }

    .mcp-status-dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
    }

    .mcp-status-badge.connected .mcp-status-dot { background-color: var(--color-connected); box-shadow: 0 0 6px var(--color-connected); }
    .mcp-status-badge.reconnecting .mcp-status-dot { background-color: var(--color-reconnecting); box-shadow: 0 0 6px var(--color-reconnecting); }
    .mcp-status-badge.disconnected .mcp-status-dot { background-color: var(--color-disconnected); }

    .mcp-server-card-metrics {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      font-size: 11px;
      border-top: 1px solid rgba(255, 255, 255, 0.03);
      padding-top: 10px;
    }

    .mcp-metric-item {
      display: flex;
      flex-direction: column;
      gap: 2px;
    }

    .mcp-metric-label {
      color: var(--text-secondary);
    }

    .mcp-metric-value {
      font-weight: 600;
      color: #fff;
      font-family: var(--font-mono);
    }

    .mcp-server-actions {
      display: flex;
      gap: 6px;
    }

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
      gap: 4px;
      font-family: var(--font-sans);
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

    .mcp-btn-connect {
      color: var(--color-connected);
      border-color: rgba(46, 204, 113, 0.3);
    }
    .mcp-btn-connect:hover:not(:disabled) {
      background: rgba(46, 204, 113, 0.08) !important;
      border-color: var(--color-connected) !important;
    }

    .mcp-btn-disconnect {
      color: var(--color-disconnected);
      border-color: rgba(231, 76, 60, 0.3);
    }
    .mcp-btn-disconnect:hover:not(:disabled) {
      background: rgba(231, 76, 60, 0.08) !important;
      border-color: var(--color-disconnected) !important;
    }

    .mcp-btn-reconnect {
      color: var(--color-reconnecting);
      border-color: rgba(241, 196, 15, 0.3);
    }
    .mcp-btn-reconnect:hover:not(:disabled) {
      background: rgba(241, 196, 15, 0.08) !important;
      border-color: var(--color-reconnecting) !important;
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

    .mcp-detail-panel {
      min-height: 500px;
    }

    .mcp-detail-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid var(--border-subtle);
      padding-bottom: 16px;
      margin-bottom: 16px;
    }

    .mcp-detail-title h2 {
      font-size: 18px;
      font-weight: 800;
      color: #fff;
      margin: 0;
    }

    .mcp-detail-title p {
      font-size: 12px;
      color: var(--text-secondary);
      margin: 4px 0 0 0;
      font-family: var(--font-mono);
    }

    .mcp-detail-section {
      display: flex;
      flex-direction: column;
      gap: 12px;
      margin-bottom: 16px;
    }

    .mcp-section-subtitle {
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.8px;
      color: var(--text-secondary);
      font-weight: 700;
      border-bottom: 1px solid rgba(255, 255, 255, 0.03);
      padding-bottom: 6px;
    }

    .mcp-config-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
      font-family: var(--font-mono);
    }

    .mcp-config-table td {
      padding: 6px 0;
      vertical-align: top;
    }

    .mcp-config-table td.label {
      color: var(--text-secondary);
      width: 120px;
    }

    .mcp-config-table td.value {
      color: #fff;
    }

    .mcp-tools-list {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .mcp-tool-item {
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-md);
      overflow: hidden;
      transition: all var(--transition-speed) ease;
    }

    .mcp-tool-item-summary {
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

    .mcp-tool-item-summary:hover {
      background: var(--bg-card-hover);
    }

    .mcp-tool-name-container {
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .mcp-tool-icon {
      font-size: 11px;
      background: rgba(155, 89, 182, 0.1);
      color: var(--color-accent);
      padding: 2px 6px;
      border-radius: var(--radius-sm);
      font-family: var(--font-mono);
      font-weight: 700;
    }

    .mcp-tool-arrow {
      font-size: 10px;
      color: var(--text-secondary);
      transition: transform var(--transition-speed) ease;
    }

    .mcp-tool-item.expanded .mcp-tool-arrow {
      transform: rotate(180deg);
    }

    .mcp-tool-item-details {
      padding: 0 16px 16px 16px;
      border-top: 1px solid rgba(255, 255, 255, 0.03);
      display: flex;
      flex-direction: column;
      gap: 12px;
      background: rgba(0, 0, 0, 0.1);
    }

    .mcp-tool-description {
      font-size: 12px;
      color: var(--text-secondary);
      line-height: 1.5;
      padding-top: 12px;
    }

    .mcp-schema-header {
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-secondary);
      font-weight: 700;
      margin-bottom: 4px;
    }

    .mcp-schema-pre {
      background: var(--bg-input);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-sm);
      padding: 10px;
      font-family: var(--font-mono);
      font-size: 11px;
      color: #a29bfe;
      overflow-x: auto;
      max-height: 200px;
    }

    .mcp-console-panel {
      flex: 1;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }

    .mcp-console-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
    }

    .mcp-console-filters {
      display: flex;
      gap: 6px;
    }

    .mcp-console-filter-btn {
      background: transparent;
      border: 1px solid var(--border-subtle);
      color: var(--text-secondary);
      font-size: 10px;
      padding: 3px 8px;
      border-radius: 4px;
      cursor: pointer;
      font-family: var(--font-mono);
    }

    .mcp-console-filter-btn.active {
      background: var(--border-active);
      color: #fff;
    }

    .mcp-console-viewport {
      background: #050608;
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-md);
      padding: 16px;
      font-family: var(--font-mono);
      font-size: 11px;
      line-height: 1.6;
      height: 320px;
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
    }

    .mcp-log-time {
      color: var(--text-muted);
      margin-right: 8px;
    }

    .mcp-log-level {
      font-weight: 700;
      margin-right: 6px;
    }

    .mcp-log-line.info { border-color: #3498DB; }
    .mcp-log-line.info .mcp-log-level { color: #3498DB; }
    
    .mcp-log-line.success { border-color: var(--color-connected); }
    .mcp-log-line.success .mcp-log-level { color: var(--color-connected); }

    .mcp-log-line.warn { border-color: var(--color-reconnecting); }
    .mcp-log-line.warn .mcp-log-level { color: var(--color-reconnecting); }

    .mcp-log-line.error { border-color: var(--color-disconnected); }
    .mcp-log-line.error .mcp-log-level { color: var(--color-disconnected); }

    .mcp-log-line.debug { border-color: #7f8c8d; }
    .mcp-log-line.debug .mcp-log-level { color: #7f8c8d; }

    .mcp-form-group {
      display: flex;
      flex-direction: column;
      gap: 6px;
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

    .mcp-tools-grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 16px;
    }

    .mcp-tool-card {
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-md);
      padding: 20px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    .mcp-tool-card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid rgba(255, 255, 255, 0.03);
      padding-bottom: 10px;
    }

    .mcp-tool-card-name {
      font-size: 15px;
      font-weight: 700;
      color: #fff;
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .mcp-tool-card-server {
      font-size: 11px;
      color: var(--text-secondary);
      font-family: var(--font-mono);
      background: rgba(255,255,255,0.05);
      padding: 2px 8px;
      border-radius: 4px;
      border: 1px solid rgba(255,255,255,0.03);
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
      max-width: 90%;
      padding: 24px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
      display: flex;
      flex-direction: column;
      gap: 16px;
      position: relative;
    }

    .mcp-modal-close {
      position: absolute;
      top: 16px;
      right: 16px;
      background: transparent;
      border: none;
      color: var(--text-secondary);
      font-size: 20px;
      cursor: pointer;
      line-height: 1;
    }
    .mcp-modal-close:hover {
      color: #fff;
    }

    @media (max-width: 1024px) {
      .mcp-dashboard-layout {
        grid-template-columns: 1fr;
      }
    }

    @media (max-width: 768px) {
      .mcp-container {
        padding: 0;
        gap: 12px;
      }
      
      .mcp-header {
        flex-direction: column;
        align-items: flex-start;
        gap: 12px;
        padding: 16px;
      }

      .mcp-header-meta {
        width: 100%;
        justify-content: space-between;
      }

      .mcp-nav-tabs {
        width: 100%;
        justify-content: space-between;
      }

      .mcp-nav-tab {
        flex: 1;
        justify-content: center;
        padding: 10px;
        font-size: 12px;
      }

      .mcp-server-card {
        padding: 14px;
      }

      .mcp-server-card-metrics {
        grid-template-columns: 1fr;
        gap: 6px;
      }

      .mcp-btn {
        flex: 1;
        justify-content: center;
      }

      .mcp-server-actions {
        width: 100%;
      }
    }
  `;
  document.head.appendChild(styleEl);

  // --------------------------------------------------
  // 2. React Dashboard Component
  // --------------------------------------------------
  function McpController() {
    // Current Active Tab State
    const getTabFromPath = () => {
      const path = window.location.pathname;
      if (path.endsWith('/tools')) return 'tools';
      if (path.endsWith('/logs')) return 'logs';
      return 'servers';
    };

    const [currentTab, setCurrentTab] = React.useState(getTabFromPath());
    const [selectedServerName, setSelectedServerName] = React.useState('gdrive');
    const [expandedTools, setExpandedTools] = React.useState({});
    const [modalOpen, setModalOpen] = React.useState(false);
    const [toolSearch, setToolSearch] = React.useState('');
    const [selectedLogServer, setSelectedLogServer] = React.useState('all');
    const [autoScroll, setAutoScroll] = React.useState(true);
    const [timeStr, setTimeStr] = React.useState('00:00:00');

    // Add Server Form State
    const [formName, setFormName] = React.useState('');
    const [formLoader, setFormLoader] = React.useState('Lazy');
    const [formCmd, setFormCmd] = React.useState('');
    const [formArgs, setFormArgs] = React.useState('');
    const [formEnv, setFormEnv] = React.useState('');

    // Core state: list of servers and their details
    const [servers, setServers] = React.useState([
      {
        name: 'gdrive',
        status: 'CONNECTED',
        type: 'Lazy',
        version: '1.2.0-local',
        lastPing: '14ms',
        command: 'mcp-server-gdrive',
        args: '--auth-token-cache=~/.hermes/cache/gdrive.json',
        env: 'GDRIVE_SCOPES=https://www.googleapis.com/auth/drive.readonly',
        tools: [
          {
            name: 'drive_about',
            description: 'Retrieve metadata about the current user\'s Google Drive storage limits, file count, and sync state.',
            schema: { type: "object", properties: {}, required: [] }
          },
          {
            name: 'drive_search',
            description: 'Search for files and folders using standard Google Drive queries. Supports filtering by name, size, type, and parent folder.',
            schema: {
              type: "object",
              properties: {
                query: { type: "string", description: "Search query matching file names or contents" },
                limit: { type: "integer", description: "Maximum results count (default 10)" }
              },
              required: ["query"]
            }
          },
          {
            name: 'drive_read_file',
            description: 'Read file contents by file ID. Automatically converts Google Docs/Slides/Sheets to readable text formats.',
            schema: {
              type: "object",
              properties: {
                fileId: { type: "string", description: "Google Drive alphanumeric unique file ID" }
              },
              required: ["fileId"]
            }
          },
          {
            name: 'sheets_read',
            description: 'Direct spreadsheet cells reader. Select specific sheets and cell coordinates to return raw JSON values.',
            schema: {
              type: "object",
              properties: {
                spreadsheetId: { type: "string", description: "Spreadsheet ID" },
                range: { type: "string", description: "A1 notation range (e.g. Sheet1!A1:D20)" }
              },
              required: ["spreadsheetId", "range"]
            }
          }
        ],
        logs: [
          { time: '22:45:10', level: 'INFO', text: '[INFO] Initializing Google Drive OAuth secure connection client...' },
          { time: '22:45:11', level: 'SUCCESS', text: '[SUCCESS] OAuth handshake complete. Token cached to ~/.hermes/cache/gdrive.json.' },
          { time: '22:45:11', level: 'INFO', text: '[INFO] Mapping file system directory metadata index (3,402 items checked).' },
          { time: '22:45:12', level: 'SUCCESS', text: '[SUCCESS] Schema registration ready. Bound 4 lazy tools successfully.' },
          { time: '22:50:30', level: 'INFO', text: '[INFO] Received ping check from orchestrator.' },
          { time: '22:50:30', level: 'SUCCESS', text: '[SUCCESS] Ping callback resolved in 14ms.' }
        ]
      },
      {
        name: 'sqlite-connector',
        status: 'CONNECTED',
        type: 'Eager',
        version: '0.4.1',
        lastPing: '8ms',
        command: 'python3 -m mcp_sqlite',
        args: '--db-path=~/.hermes/db/orchestrator_dedup.db',
        env: 'SQLITE_MAX_TIMEOUT_MS=2000, SQLITE_PRAGMA_JOURNAL=WAL',
        tools: [
          {
            name: 'query_db',
            description: 'Execute read-only SQL SELECT queries on the event dedup database. Validates statements for SQL injection markers.',
            schema: {
              type: "object",
              properties: {
                sql: { type: "string", description: "SQL query statement" }
              },
              required: ["sql"]
            }
          },
          {
            name: 'insert_hash',
            description: 'Write a new event hash to prevent duplicates in the execution queue. Returns boolean flag indicating uniqueness.',
            schema: {
              type: "object",
              properties: {
                hash: { type: "string", description: "SHA256 signature key hash" },
                reference: { type: "string", description: "Associated Linear issue reference ID (e.g. GRO-671)" }
              },
              required: ["hash", "reference"]
            }
          },
          {
            name: 'list_tables',
            description: 'Enumerate table definitions, column types, indices, and constraints within the active database connection.',
            schema: { type: "object", properties: {}, required: [] }
          }
        ],
        logs: [
          { time: '22:46:01', level: 'INFO', text: '[INFO] Opening SQLite file handle: ~/.hermes/db/orchestrator_dedup.db' },
          { time: '22:46:01', level: 'SUCCESS', text: '[SUCCESS] Database state loaded. Active journal mode: WAL. Connection pool initialized.' },
          { time: '22:46:02', level: 'INFO', text: '[INFO] Pre-scanning database constraints. Tables: [event_hashes, execution_logs, configuration_state]' },
          { time: '22:46:02', level: 'SUCCESS', text: '[SUCCESS] Schema bound eager: registered 3 tools to registry.' },
          { time: '22:49:15', level: 'INFO', text: '[INFO] SELECT count(*) from event_hashes evaluated: count=1,424.' },
          { time: '22:50:31', level: 'INFO', text: '[INFO] Received ping check from orchestrator.' },
          { time: '22:50:31', level: 'SUCCESS', text: '[SUCCESS] Ping callback resolved in 8ms.' }
        ]
      },
      {
        name: 'local-ollama',
        status: 'RECONNECTING',
        type: 'Eager',
        version: '0.1.0',
        lastPing: 'Timeout (3000ms)',
        command: 'ollama run qwen2.5:32b',
        args: '--mcp --host=http://127.0.0.1:31434',
        env: 'OLLAMA_NUM_PARALLEL=4, OLLAMA_FLASH_ATTENTION=1',
        tools: [
          {
            name: 'generate_completion',
            description: 'Run inference completions directly on locally loaded model weights. Bypasses orchestrator wrapper.',
            schema: {
              type: "object",
              properties: {
                prompt: { type: "string" },
                temperature: { type: "number" }
              },
              required: ["prompt"]
            }
          },
          {
            name: 'get_model_info',
            description: 'Fetch detailed parameter metrics, layer offload status, and system VRAM consumption stats.',
            schema: { type: "object", properties: {}, required: [] }
          }
        ],
        logs: [
          { time: '22:48:30', level: 'INFO', text: '[INFO] Initializing connection to local Ollama API on port 31434...' },
          { time: '22:48:33', level: 'WARN', text: '[WARN] Timeout reached (3000ms) waiting for http://127.0.0.1:31434/api/tags. Endpoint may be loading weights.' },
          { time: '22:49:00', level: 'INFO', text: '[INFO] Re-attempting connection handshake (Try #2)...' },
          { time: '22:49:03', level: 'WARN', text: '[WARN] Timeout reached (3000ms). Connection refused. Retrying in 30 seconds.' },
          { time: '22:49:33', level: 'INFO', text: '[INFO] Re-attempting connection handshake (Try #3)...' },
          { time: '22:49:36', level: 'WARN', text: '[WARN] Timeout reached (3000ms). Connection refused. Retrying in 60 seconds.' }
        ]
      },
      {
        name: 'github-mcp',
        status: 'DISCONNECTED',
        type: 'Lazy',
        version: '2.0.1',
        lastPing: 'N/A',
        command: 'npx -y @modelcontextprotocol/server-github',
        args: '--allowed-orgs=mbgulden',
        env: 'GITHUB_TOKEN= (MISSING)',
        tools: [
          {
            name: 'get_pr',
            description: 'Read detailed description, files changes, and conversation history of a specific repository Pull Request.',
            schema: {
              type: "object",
              properties: {
                owner: { type: "string" },
                repo: { type: "string" },
                pullNumber: { type: "integer" }
              },
              required: ["owner", "repo", "pullNumber"]
            }
          },
          {
            name: 'create_branch',
            description: 'Initialize a new repository branch from a specified target reference branch.',
            schema: {
              type: "object",
              properties: {
                owner: { type: "string" },
                repo: { type: "string" },
                branch: { type: "string" },
                refBranch: { type: "string" }
              },
              required: ["owner", "repo", "branch"]
            }
          },
          {
            name: 'post_comment',
            description: 'Post an issue comment on an open or closed repository issue ticket.',
            schema: {
              type: "object",
              properties: {
                owner: { type: "string" },
                repo: { type: "string" },
                issueNumber: { type: "integer" },
                body: { type: "string" }
              },
              required: ["owner", "repo", "issueNumber", "body"]
            }
          }
        ],
        logs: [
          { time: '22:40:00', level: 'INFO', text: '[INFO] Launching GitHub MCP Lazy loader...' },
          { time: '22:40:01', level: 'ERROR', text: '[ERROR] Environment variable validation failed: GITHUB_TOKEN is undefined.' },
          { time: '22:40:01', level: 'INFO', text: '[INFO] MCP process exited with status code 1. Service state changed to DISCONNECTED.' }
        ]
      }
    ]);

    // Live clock and sub-path syncing effect
    React.useEffect(() => {
      const handleLocationChange = () => {
        setCurrentTab(getTabFromPath());
      };
      window.addEventListener('popstate', handleLocationChange);
      const locInterval = setInterval(handleLocationChange, 500);

      const updateClock = () => {
        setTimeStr(new Date().toTimeString().split(' ')[0]);
      };
      updateClock();
      const clockInterval = setInterval(updateClock, 1000);

      return () => {
        window.removeEventListener('popstate', handleLocationChange);
        clearInterval(locInterval);
        clearInterval(clockInterval);
      };
    }, []);

    // Scroll logs viewport to bottom
    const logsViewportRef = React.useRef(null);
    const detailLogsViewportRef = React.useRef(null);
    React.useEffect(() => {
      if (autoScroll) {
        if (logsViewportRef.current) {
          logsViewportRef.current.scrollTop = logsViewportRef.current.scrollHeight;
        }
        if (detailLogsViewportRef.current) {
          detailLogsViewportRef.current.scrollTop = detailLogsViewportRef.current.scrollHeight;
        }
      }
    });

    // Rebuild global unified logs list sorted chronologically
    const globalLogs = React.useMemo(() => {
      const list = [];
      servers.forEach(s => {
        s.logs.forEach(l => {
          list.push({
            server: s.name,
            time: l.time,
            level: l.level,
            text: l.text
          });
        });
      });
      list.sort((a, b) => a.time.localeCompare(b.time));
      return list;
    }, [servers]);

    // Simulator helpers
    const addLogEntry = (serverName, level, text) => {
      const timeStrNow = new Date().toTimeString().split(' ')[0];
      setServers(prev => prev.map(s => {
        if (s.name === serverName) {
          return {
            ...s,
            logs: [...s.logs, { time: timeStrNow, level: level, text: `[${level}] ${text}` }]
          };
        }
        return s;
      }));
    };

    const triggerConnect = (serverName) => {
      setServers(prev => prev.map(s => {
        if (s.name === serverName) {
          return {
            ...s,
            status: 'CONNECTED',
            lastPing: `${Math.floor(Math.random() * 20) + 5}ms`
          };
        }
        return s;
      }));
      addLogEntry(serverName, 'INFO', `Triggered connect command line...`);
      addLogEntry(serverName, 'SUCCESS', `MCP daemon process running. Schemas successfully bound.`);
    };

    const triggerDisconnect = (serverName) => {
      setServers(prev => prev.map(s => {
        if (s.name === serverName) {
          return {
            ...s,
            status: 'DISCONNECTED',
            lastPing: 'N/A'
          };
        }
        return s;
      }));
      addLogEntry(serverName, 'WARN', `Disconnect command received. Sending SIGTERM to process...`);
      addLogEntry(serverName, 'INFO', `Process terminated. Resources cleaned up.`);
    };

    const triggerReconnect = (serverName) => {
      setServers(prev => prev.map(s => {
        if (s.name === serverName) {
          return {
            ...s,
            status: 'RECONNECTING',
            lastPing: 'Timeout (3000ms)'
          };
        }
        return s;
      }));
      addLogEntry(serverName, 'WARN', `Initiating process reconnect cycle...`);

      setTimeout(() => {
        setServers(prev => prev.map(s => {
          if (s.name === serverName && s.status === 'RECONNECTING') {
            const timeStrNow = new Date().toTimeString().split(' ')[0];
            return {
              ...s,
              status: 'CONNECTED',
              lastPing: `${Math.floor(Math.random() * 15) + 4}ms`,
              logs: [...s.logs, { time: timeStrNow, level: 'SUCCESS', text: `[SUCCESS] Reconnection handshake completed successfully.` }]
            };
          }
          return s;
        }));
      }, 2000);
    };

    const handleAddServer = (e) => {
      e.preventDefault();
      if (servers.some(s => s.name.toLowerCase() === formName.toLowerCase())) {
        alert(`MCP Server "${formName}" is already registered.`);
        return;
      }

      const timeStrNow = new Date().toTimeString().split(' ')[0];
      const newS = {
        name: formName,
        status: 'CONNECTED',
        type: formLoader,
        version: '1.0.0-local',
        lastPing: '4ms',
        command: formCmd,
        args: formArgs,
        env: formEnv,
        tools: [
          {
            name: `${formName}_health_ping`,
            description: `Check health latency check for the ${formName} daemon connection.`,
            schema: { type: "object", properties: {}, required: [] }
          }
        ],
        logs: [
          { time: timeStrNow, level: 'INFO', text: `[INFO] Initializing newly registered daemon: ${formName}...` },
          { time: timeStrNow, level: 'SUCCESS', text: `[SUCCESS] Daemon execution parameters registered successfully.` }
        ]
      };

      setServers(prev => [...prev, newS]);
      setSelectedServerName(formName);
      setModalOpen(false);

      // Reset fields
      setFormName('');
      setFormCmd('');
      setFormArgs('');
      setFormEnv('');
      setCurrentTab('servers');
    };

    const clearAllLogs = () => {
      setServers(prev => prev.map(s => {
        if (selectedLogServer === 'all' || s.name === selectedLogServer) {
          return { ...s, logs: [] };
        }
        return s;
      }));
    };

    // Helper: Escapes level markers inside raw simulated logs
    const formatLogText = (text) => {
      return text.replace(/\[(INFO|SUCCESS|WARN|ERROR)\]\s*/, '');
    };

    // --------------------------------------------------
    // Render: Navigation Header
    // --------------------------------------------------
    const renderHeader = () => {
      return h('div', { className: 'mcp-header' }, [
        h('div', { className: 'mcp-header-brand' }, [
          h('div', { className: 'mcp-header-logo' }),
          h('h1', { className: 'mcp-header-title' }, 'MCP CONTROLLER'),
          h('span', { className: 'mcp-header-tag' }, 'Ned-Spectrum')
        ]),
        h('div', { className: 'mcp-header-meta' }, [
          h('div', { className: 'mcp-live-indicator' }, [
            h('div', { className: 'mcp-live-dot' }),
            'DAEMON ONLINE'
          ]),
          h('div', null, [
            h('span', null, 'Local Time: '),
            h('span', { style: { fontFamily: 'var(--font-mono)', fontWeight: 'bold', color: '#fff' } }, timeStr)
          ])
        ])
      ]);
    };

    const renderNavTabs = () => {
      return h('div', { className: 'mcp-nav-tabs' }, [
        h('button', { className: `mcp-nav-tab ${currentTab === 'servers' ? 'active' : ''}`, onClick: () => setCurrentTab('servers') }, [
          h('span', null, '🔌 '), 'Servers'
        ]),
        h('button', { className: `mcp-nav-tab ${currentTab === 'tools' ? 'active' : ''}`, onClick: () => setCurrentTab('tools') }, [
          h('span', null, '🛠️ '), 'Tools Registry'
        ]),
        h('button', { className: `mcp-nav-tab ${currentTab === 'logs' ? 'active' : ''}`, onClick: () => setCurrentTab('logs') }, [
          h('span', null, '📜 '), 'Daemon Logs'
        ])
      ]);
    };

    // --------------------------------------------------
    // Render: Servers Tab View
    // --------------------------------------------------
    const renderServersView = () => {
      const selectedServer = servers.find(s => s.name === selectedServerName);

      return h('div', { className: 'mcp-dashboard-layout' }, [
        // Left Column: Server Cards List
        h('div', { className: 'mcp-panel-card' }, [
          h('div', { className: 'mcp-panel-title' }, [
            h('span', null, 'Active MCP Servers'),
            h('button', { className: 'mcp-btn mcp-btn-primary', onClick: () => setModalOpen(true), style: { padding: '4px 10px', fontSize: '10px' } }, '+ Add Server')
          ]),
          h('div', { className: 'mcp-server-list' }, servers.map(s => {
            const isSelected = s.name === selectedServerName;
            const statusClass = s.status.toLowerCase();
            return h('div', {
              key: s.name,
              className: `mcp-server-card ${statusClass} ${isSelected ? 'selected' : ''}`,
              onClick: () => setSelectedServerName(s.name)
            }, [
              h('div', { className: 'mcp-server-card-header' }, [
                h('div', { className: 'mcp-server-card-info' }, [
                  h('h3', null, s.name),
                  h('span', null, `v${s.version} · ${s.type} loader`)
                ]),
                h('span', { className: `mcp-status-badge ${statusClass}` }, [
                  h('span', { className: 'mcp-status-dot' }),
                  s.status
                ])
              ]),
              h('div', { className: 'mcp-server-card-metrics' }, [
                h('div', { className: 'mcp-metric-item' }, [
                  h('span', { className: 'mcp-metric-label' }, 'Last Ping:'),
                  h('span', {
                    className: 'mcp-metric-value',
                    style: { color: s.status === 'CONNECTED' ? '#2ECC71' : s.status === 'RECONNECTING' ? '#F1C40F' : '#E74C3C' }
                  }, s.lastPing)
                ]),
                h('div', { className: 'mcp-metric-item' }, [
                  h('span', { className: 'mcp-metric-label' }, 'Tools Registered:'),
                  h('span', { className: 'mcp-metric-value' }, s.tools.length)
                ])
              ])
            ]);
          }))
        ]),

        // Right Column: Detail Panel
        h('div', { className: 'mcp-panel-card mcp-detail-panel' }, selectedServer ? [
          h('div', { className: 'mcp-detail-header' }, [
            h('div', { className: 'mcp-detail-title' }, [
              h('h2', null, selectedServer.name),
              h('p', null, `v${selectedServer.version} · Loader Type: ${selectedServer.type}`)
            ]),
            h('div', { className: 'mcp-server-actions' }, [
              h('button', {
                className: 'mcp-btn mcp-btn-connect',
                onClick: () => triggerConnect(selectedServer.name),
                disabled: selectedServer.status === 'CONNECTED' || selectedServer.status === 'RECONNECTING'
              }, 'Connect'),
              h('button', {
                className: 'mcp-btn mcp-btn-disconnect',
                onClick: () => triggerDisconnect(selectedServer.name),
                disabled: selectedServer.status === 'DISCONNECTED'
              }, 'Disconnect'),
              h('button', {
                className: 'mcp-btn mcp-btn-reconnect',
                onClick: () => triggerReconnect(selectedServer.name),
                disabled: selectedServer.status === 'DISCONNECTED'
              }, 'Reconnect')
            ])
          ]),

          h('div', { className: 'mcp-detail-section' }, [
            h('div', { className: 'mcp-section-subtitle' }, 'Process Configuration'),
            h('table', { className: 'mcp-config-table' }, [
              h('tbody', null, [
                h('tr', null, [
                  h('td', { className: 'label' }, 'Startup Command:'),
                  h('td', { className: 'value' }, h('code', null, selectedServer.command))
                ]),
                h('tr', null, [
                  h('td', { className: 'label' }, 'Args Array:'),
                  h('td', { className: 'value' }, h('code', null, selectedServer.args || 'None'))
                ]),
                h('tr', null, [
                  h('td', { className: 'label' }, 'Env Config:'),
                  h('td', { className: 'value' }, h('code', null, selectedServer.env || 'None'))
                ])
              ])
            ])
          ]),

          h('div', { className: 'mcp-detail-section' }, [
            h('div', { className: 'mcp-section-subtitle' }, `Registered Tools (${selectedServer.tools.length})`),
            h('div', { className: 'mcp-tools-list' }, selectedServer.tools.map(tool => {
              const toolKey = selectedServer.name + '_' + tool.name;
              const isExpanded = expandedTools[toolKey];
              return h('div', { key: tool.name, className: `mcp-tool-item ${isExpanded ? 'expanded' : ''}` }, [
                h('div', {
                  className: 'mcp-tool-item-summary',
                  onClick: () => setExpandedTools(prev => ({ ...prev, [toolKey]: !isExpanded }))
                }, [
                  h('div', { className: 'mcp-tool-name-container' }, [
                    h('span', { className: 'mcp-tool-icon' }, 'tool'),
                    h('span', null, tool.name)
                  ]),
                  h('span', { className: 'mcp-tool-arrow' }, isExpanded ? '▲' : '▼')
                ]),
                isExpanded && h('div', { className: 'mcp-tool-item-details' }, [
                  h('p', { className: 'mcp-tool-description' }, tool.description),
                  h('div', { className: 'mcp-schema-header' }, 'Parameter Schema'),
                  h('pre', { className: 'mcp-schema-pre' }, JSON.stringify(tool.schema, null, 2))
                ])
              ]);
            }))
          ]),

          h('div', { className: 'mcp-detail-section', style: { flex: 1, minHeight: '200px' } }, [
            h('div', { className: 'mcp-section-subtitle' }, 'Server Log Tail (Last 30 lines)'),
            h('div', { className: 'mcp-console-viewport', ref: detailLogsViewportRef, style: { height: '220px' } },
              selectedServer.logs.slice(-30).map((l, index) => {
                return h('div', { key: index, className: `mcp-log-line ${l.level.toLowerCase()}` }, [
                  h('span', { className: 'mcp-log-time' }, l.time),
                  h('span', { className: 'mcp-log-level' }, `[${l.level}]`),
                  h('span', null, formatLogText(l.text))
                ]);
              })
            )
          ])
        ] : [
          h('div', { style: { height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: 'var(--text-secondary)', textAlign: 'center', padding: '40px 0' } }, [
            h('span', { style: { fontSize: '32px', marginBottom: '12px' } }, '🔌'),
            h('h3', null, 'No Server Selected'),
            h('p', { style: { fontSize: '12px', marginTop: '4px', maxWidth: '280px' } }, 'Select an MCP server from the list on the left to examine its parameter schema, details, and logs tail.')
          ])
        ])
      ]);
    };

    // --------------------------------------------------
    // Render: Tools Tab View
    // --------------------------------------------------
    const renderToolsView = () => {
      const filteredTools = [];
      servers.forEach(s => {
        s.tools.forEach(tool => {
          if (tool.name.toLowerCase().includes(toolSearch.toLowerCase()) || tool.description.toLowerCase().includes(toolSearch.toLowerCase())) {
            filteredTools.push({ serverName: s.name, ...tool });
          }
        });
      });

      return h('div', { className: 'mcp-panel-card' }, [
        h('div', { className: 'mcp-panel-title' }, [
          h('span', null, 'Global Tools Registry'),
          h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center' } }, [
            h('input', {
              type: 'text',
              className: 'mcp-form-control',
              placeholder: 'Filter tools...',
              value: toolSearch,
              onChange: (e) => setToolSearch(e.target.value),
              style: { padding: '4px 10px', fontSize: '11px', width: '200px' }
            })
          ])
        ]),
        h('div', { className: 'mcp-tools-grid' },
          filteredTools.length > 0 ? filteredTools.map((t, index) => {
            return h('div', { key: index, className: 'mcp-tool-card' }, [
              h('div', { className: 'mcp-tool-card-header' }, [
                h('div', { className: 'mcp-tool-card-name' }, [
                  h('span', { className: 'mcp-tool-icon' }, 'tool'),
                  h('span', null, t.name)
                ]),
                h('span', { className: 'mcp-tool-card-server' }, `Server: ${t.serverName}`)
              ]),
              h('p', { style: { fontSize: '12px', color: 'var(--text-secondary)', lineHeight: '1.5' } }, t.description),
              h('div', null, [
                h('div', { className: 'mcp-schema-header' }, 'Schema Parameters'),
                h('pre', { className: 'mcp-schema-pre' }, JSON.stringify(t.schema, null, 2))
              ])
            ]);
          }) : [
            h('div', { key: 'empty', style: { textAlign: 'center', color: 'var(--text-secondary)', padding: '40px 0' } }, 'No tools matching search filters.')
          ]
        )
      ]);
    };

    // --------------------------------------------------
    // Render: Unified Logs Tab View
    // --------------------------------------------------
    const renderLogsView = () => {
      const filteredLogs = selectedLogServer === 'all'
        ? globalLogs
        : globalLogs.filter(l => l.server === selectedLogServer);

      return h('div', { className: 'mcp-panel-card' }, [
        h('div', { className: 'mcp-panel-title' }, [
          h('span', null, 'Unified Daemon Logs'),
          h('div', { style: { display: 'flex', gap: '8px' } }, [
            h('button', { className: 'mcp-btn', onClick: clearAllLogs, style: { padding: '4px 10px', fontSize: '11px' } }, 'Clear Screen')
          ])
        ]),
        h('div', { className: 'mcp-console-panel' }, [
          h('div', { className: 'mcp-console-header' }, [
            h('div', { className: 'mcp-console-filters' }, [
              h('button', {
                className: `mcp-console-filter-btn ${selectedLogServer === 'all' ? 'active' : ''}`,
                onClick: () => setSelectedLogServer('all')
              }, 'ALL LOGS'),
              ...servers.map(s => h('button', {
                key: s.name,
                className: `mcp-console-filter-btn ${selectedLogServer === s.name ? 'active' : ''}`,
                onClick: () => setSelectedLogServer(s.name)
              }, s.name.toUpperCase()))
            ]),
            h('div', { style: { display: 'flex', gap: '10px', fontSize: '11px', alignItems: 'center' } }, [
              h('input', {
                type: 'checkbox',
                id: 'react-auto-scroll',
                checked: autoScroll,
                onChange: (e) => setAutoScroll(e.target.checked)
              }),
              h('label', { htmlFor: 'react-auto-scroll', style: { color: 'var(--text-secondary)', cursor: 'pointer' } }, 'Auto Scroll')
            ])
          ]),
          h('div', { className: 'mcp-console-viewport', ref: logsViewportRef },
            filteredLogs.length > 0 ? filteredLogs.map((l, index) => {
              return h('div', { key: index, className: `mcp-log-line ${l.level.toLowerCase()}` }, [
                h('span', { className: 'mcp-log-time' }, l.time),
                h('span', { className: 'mcp-log-level', style: { fontFamily: 'var(--font-mono)' } }, `[${l.server}] [${l.level}]`),
                h('span', null, formatLogText(l.text))
              ]);
            }) : [
              h('div', { key: 'empty', className: 'mcp-log-line debug' }, [
                h('span', { className: 'mcp-log-text' }, '[system] Logs trace buffer empty. Ready.')
              ])
            ]
          ])
        ])
      ]);
    };

    // --------------------------------------------------
    // Render: Add Server Modal Overlay
    // --------------------------------------------------
    const renderAddServerModal = () => {
      if (!modalOpen) return null;
      return h('div', { className: 'mcp-modal-overlay' }, [
        h('div', { className: 'mcp-modal-content' }, [
          h('button', { className: 'mcp-modal-close', onClick: () => setModalOpen(false) }, '×'),
          h('h3', { style: { color: '#fff', fontSize: '16px', borderBottom: '1px solid var(--border-subtle)', paddingBottom: '12px', marginBottom: '4px' } }, 'Register New MCP Server'),
          h('form', { onSubmit: handleAddServer, style: { display: 'flex', flexDirection: 'column', gap: '12px' } }, [
            h('div', { className: 'mcp-form-group' }, [
              h('label', null, 'Server Name'),
              h('input', {
                type: 'text',
                className: 'mcp-form-control',
                placeholder: 'e.g. synology-mcp',
                required: true,
                value: formName,
                onChange: (e) => setFormName(e.target.value)
              })
            ]),
            h('div', { className: 'mcp-form-group' }, [
              h('label', null, 'Loader Type'),
              h('select', {
                className: 'mcp-form-control',
                style: { background: 'var(--bg-input)' },
                value: formLoader,
                onChange: (e) => setFormLoader(e.target.value)
              }, [
                h('option', { value: 'Lazy' }, 'Lazy (Loads schemas on demand)'),
                h('option', { value: 'Eager' }, 'Eager (Pre-binds schemas on launch)')
              ])
            ]),
            h('div', { className: 'mcp-form-group' }, [
              h('label', null, 'Startup Command'),
              h('input', {
                type: 'text',
                className: 'mcp-form-control',
                placeholder: 'e.g. npx -y @modelcontextprotocol/server-postgres',
                required: true,
                value: formCmd,
                onChange: (e) => setFormCmd(e.target.value)
              })
            ]),
            h('div', { className: 'mcp-form-group' }, [
              h('label', null, 'Arguments (comma-separated)'),
              h('input', {
                type: 'text',
                className: 'mcp-form-control',
                placeholder: 'e.g. postgres://localhost/db, --readonly',
                value: formArgs,
                onChange: (e) => setFormArgs(e.target.value)
              })
            ]),
            h('div', { className: 'mcp-form-group' }, [
              h('label', null, 'Environment Variables (KEY=VAL, comma-separated)'),
              h('textarea', {
                className: 'mcp-form-control mcp-form-control-textarea',
                placeholder: 'e.g. DATABASE_URL=..., SCHEMA=public',
                value: formEnv,
                onChange: (e) => setFormEnv(e.target.value)
              })
            ]),
            h('div', { style: { display: 'flex', gap: '8px', marginTop: '12px', justifyContent: 'flex-end' } }, [
              h('button', { type: 'button', className: 'mcp-btn', onClick: () => setModalOpen(false) }, 'Cancel'),
              h('button', { type: 'submit', className: 'mcp-btn mcp-btn-primary' }, 'Save Config')
            ])
          ])
        ])
      ]);
    };

    // --------------------------------------------------
    // Main Component Return
    // --------------------------------------------------
    return h('div', { className: 'mcp-container' }, [
      renderHeader(),
      renderNavTabs(),
      currentTab === 'servers' && renderServersView(),
      currentTab === 'tools' && renderToolsView(),
      currentTab === 'logs' && renderLogsView(),
      renderAddServerModal()
    ]);
  }

  // Register plugin in global registry
  registry.register('hermes-plugin-mcp-controller', McpController);
  console.log('[mcp-controller] React plugin registered successfully.');
})();
