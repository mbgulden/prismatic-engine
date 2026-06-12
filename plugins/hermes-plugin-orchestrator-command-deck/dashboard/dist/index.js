(function () {
  const sdk = window.__HERMES_PLUGIN_SDK__;
  const registry = window.__HERMES_PLUGINS__;
  if (!sdk || !registry) {
    console.warn('[orchestrator-command-deck] Hermes plugin SDK not available');
    return;
  }

  const React = sdk.React;
  const h = React.createElement;
  const api = sdk.fetchJSON || sdk.api;
  const C = sdk.components || {};

  // Inject Stylesheet into Document Head
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

      --color-antigravity: #F39C12;
      --color-jules: #E67E22;
      --color-codex: #E74C3C;
      --color-kai: #2ECC71;
      --color-fred: #3498DB;
      --color-ned: #9B59B6;

      --color-low: #536279;
      --color-medium: #3498DB;
      --color-high: #E67E22;
      --color-critical: #E74C3C;

      --radius-sm: 4px;
      --radius-md: 8px;
      --radius-lg: 12px;
      --transition-speed: 0.2s;
    }

    /* Deck Layout Grid */
    .deck-container {
      display: flex;
      flex-direction: column;
      gap: 20px;
      width: 100%;
      font-family: 'Inter', system-ui, sans-serif;
      color: var(--text-primary);
    }

    .deck-header {
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

    .deck-header::before {
      content: '';
      position: absolute;
      top: 0; left: 0; right: 0;
      height: 3px;
      background: linear-gradient(90deg, 
        var(--color-antigravity), var(--color-jules), var(--color-codex), 
        var(--color-kai), var(--color-fred), var(--color-ned)
      );
    }

    .deck-brand {
      display: flex;
      align-items: center;
      gap: 12px;
    }

    .deck-brand .logo {
      width: 24px;
      height: 24px;
      background: linear-gradient(135deg, var(--color-antigravity), var(--color-ned));
      clip-path: polygon(50% 0%, 100% 100%, 0% 100%);
      filter: drop-shadow(0 0 6px rgba(243, 156, 18, 0.4));
    }

    .deck-brand-title {
      font-size: 18px;
      font-weight: 800;
      letter-spacing: 0.5px;
      text-transform: uppercase;
      font-family: 'JetBrains Mono', monospace;
      background: linear-gradient(90deg, #fff, var(--text-secondary));
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }

    .deck-brand-subtitle {
      font-size: 11px;
      color: var(--text-secondary);
      text-transform: uppercase;
      letter-spacing: 1px;
      margin-top: 2px;
    }

    .deck-header-info {
      display: flex;
      align-items: center;
      gap: 24px;
    }

    .deck-telemetry-item {
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
    }

    .deck-telemetry-label {
      color: var(--text-muted);
      text-transform: uppercase;
      font-size: 10px;
    }

    .deck-telemetry-value {
      color: var(--text-primary);
      font-weight: bold;
    }

    .deck-nav-tabs {
      display: flex;
      background: var(--bg-industrial);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-md);
      padding: 4px;
      gap: 4px;
    }

    .deck-nav-tab {
      background: transparent;
      border: none;
      color: var(--text-secondary);
      font-family: inherit;
      font-size: 14px;
      font-weight: 600;
      padding: 10px 20px;
      border-radius: var(--radius-sm);
      cursor: pointer;
      transition: all var(--transition-speed) ease;
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .deck-nav-tab:hover {
      background: rgba(255, 255, 255, 0.04);
      color: var(--text-primary);
    }

    .deck-nav-tab.active {
      background: var(--bg-deep-space);
      color: var(--text-primary);
      box-shadow: inset 0 0 10px rgba(0,0,0,0.5);
      border-bottom: 2px solid var(--color-antigravity);
    }

    .deck-dashboard-grid {
      display: grid;
      grid-template-columns: 8fr 4fr;
      gap: 20px;
    }

    .deck-panel-card {
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-lg);
      padding: 24px;
      display: flex;
      flex-direction: column;
      gap: 16px;
      box-shadow: 0 4px 15px rgba(0, 0, 0, 0.15);
      position: relative;
    }

    .deck-panel-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid var(--border-subtle);
      padding-bottom: 12px;
      margin-bottom: 4px;
    }

    .deck-panel-title {
      font-size: 16px;
      font-weight: 700;
      color: var(--text-primary);
      display: flex;
      align-items: center;
      gap: 8px;
      text-transform: uppercase;
      font-family: 'JetBrains Mono', monospace;
      letter-spacing: 0.5px;
    }

    .deck-panel-title span.pulse-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background-color: var(--color-kai);
      display: inline-block;
      box-shadow: 0 0 8px var(--color-kai);
      animation: deck-status-pulse 1.5s infinite;
    }

    @keyframes deck-status-pulse {
      0%, 100% { opacity: 0.6; transform: scale(0.9); }
      50% { opacity: 1; transform: scale(1.1); }
    }

    /* Mode switcher */
    .deck-mode-toggle-bar {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      background: var(--bg-input);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-md);
      padding: 4px;
      position: relative;
    }

    .deck-mode-btn {
      background: transparent;
      border: none;
      color: var(--text-secondary);
      font-family: inherit;
      font-size: 13px;
      font-weight: 700;
      padding: 12px;
      border-radius: var(--radius-sm);
      cursor: pointer;
      transition: all var(--transition-speed) ease;
      text-align: center;
      text-transform: uppercase;
      z-index: 2;
    }

    .deck-mode-btn.active {
      color: var(--text-primary);
      background: var(--bg-card);
      box-shadow: 0 2px 8px rgba(0,0,0,0.5), inset 0 0 1px rgba(255,255,255,0.1);
    }

    .deck-mode-toggle-bar.interactive .deck-mode-btn.active { border-bottom: 2px solid var(--color-fred); }
    .deck-mode-toggle-bar.collaborative .deck-mode-btn.active { border-bottom: 2px solid var(--color-antigravity); }
    .deck-mode-toggle-bar.autonomous .deck-mode-btn.active { border-bottom: 2px solid var(--color-kai); }

    .deck-mode-description {
      font-size: 12px;
      color: var(--text-secondary);
      background: var(--bg-deep-space);
      border: 1px dashed var(--border-subtle);
      padding: 12px;
      border-radius: var(--radius-md);
      line-height: 1.5;
    }

    /* Agent Controls layout */
    .deck-agent-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
      gap: 16px;
    }

    .deck-agent-card {
      background: var(--bg-deep-space);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-md);
      padding: 16px;
      position: relative;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      gap: 12px;
      transition: all var(--transition-speed) ease;
    }

    .deck-agent-card::before {
      content: '';
      position: absolute;
      top: 0; left: 0; bottom: 0;
      width: 4px;
      background: var(--agent-color);
    }

    .deck-agent-card:hover {
      border-color: var(--agent-color);
      box-shadow: 0 4px 15px var(--agent-color-glow);
    }

    .deck-agent-info {
      display: flex;
      justify-content: space-between;
      align-items: center;
    }

    .deck-agent-title {
      font-size: 15px;
      font-weight: 700;
      color: var(--text-primary);
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .deck-agent-role {
      font-size: 11px;
      color: var(--text-secondary);
      font-family: 'JetBrains Mono', monospace;
      margin-top: 2px;
    }

    .deck-agent-status-badge {
      font-size: 10px;
      font-weight: 800;
      text-transform: uppercase;
      padding: 4px 8px;
      border-radius: 4px;
      font-family: 'JetBrains Mono', monospace;
    }

    .deck-agent-status-badge.status-running { background: rgba(46, 204, 113, 0.1); color: var(--color-kai); border: 1px solid rgba(46, 204, 113, 0.2); }
    .deck-agent-status-badge.status-paused { background: rgba(243, 156, 18, 0.1); color: var(--color-antigravity); border: 1px solid rgba(243, 156, 18, 0.2); }
    .deck-agent-status-badge.status-idle { background: rgba(139, 155, 180, 0.1); color: var(--text-secondary); border: 1px solid rgba(139, 155, 180, 0.2); }
    .deck-agent-status-badge.status-terminated { background: rgba(231, 76, 60, 0.1); color: var(--color-codex); border: 1px solid rgba(231, 76, 60, 0.2); }

    .deck-agent-task {
      background: rgba(0,0,0,0.3);
      border: 1px solid rgba(255,255,255,0.02);
      border-radius: var(--radius-sm);
      padding: 8px 10px;
      font-size: 12px;
      line-height: 1.4;
      font-family: 'JetBrains Mono', monospace;
      color: rgba(255, 255, 255, 0.7);
      white-space: nowrap;
      text-overflow: ellipsis;
      overflow: hidden;
    }

    .deck-agent-telemetry {
      display: flex;
      gap: 12px;
      font-size: 11px;
      color: var(--text-secondary);
      font-family: 'JetBrains Mono', monospace;
      background: rgba(255,255,255,0.01);
      padding: 4px 8px;
      border-radius: var(--radius-sm);
      border: 1px solid var(--border-subtle);
    }

    .deck-agent-actions {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 6px;
    }

    .deck-agent-btn {
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      padding: 8px 0;
      border-radius: 4px;
      border: 1px solid var(--border-subtle);
      background: var(--bg-card);
      color: var(--text-primary);
      cursor: pointer;
      transition: all var(--transition-speed) ease;
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .deck-agent-btn:hover:not(:disabled) {
      background: var(--bg-card-hover);
      border-color: var(--text-secondary);
    }

    .deck-agent-btn:disabled {
      opacity: 0.2;
      cursor: not-allowed;
    }

    .deck-agent-btn.btn-kill:hover:not(:disabled) {
      background: rgba(231, 76, 60, 0.2);
      border-color: var(--color-codex);
      color: #ff6b6b;
    }

    /* Dispatch Form */
    .deck-dispatch-form {
      display: grid;
      grid-template-columns: 3fr 4fr 2fr auto;
      gap: 12px;
      align-items: flex-end;
      background: var(--bg-deep-space);
      border: 1px solid var(--border-subtle);
      padding: 16px;
      border-radius: var(--radius-md);
    }

    .deck-form-group {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .deck-form-label {
      font-size: 10px;
      text-transform: uppercase;
      font-family: 'JetBrains Mono', monospace;
      color: var(--text-secondary);
      letter-spacing: 0.5px;
    }

    .deck-form-select, .deck-form-input {
      background: var(--bg-input);
      border: 1px solid var(--border-subtle);
      border-radius: 4px;
      color: var(--text-primary);
      font-family: inherit;
      font-size: 13px;
      padding: 10px 12px;
      outline: none;
      transition: border-color var(--transition-speed) ease;
      width: 100%;
    }

    .deck-dispatch-btn {
      background: var(--color-antigravity);
      border: none;
      color: #000;
      font-family: inherit;
      font-size: 12px;
      font-weight: 800;
      text-transform: uppercase;
      padding: 11px 20px;
      border-radius: 4px;
      cursor: pointer;
      transition: all var(--transition-speed) ease;
      box-shadow: 0 4px 10px rgba(243, 156, 18, 0.3);
      height: 38px;
    }

    /* Task Queue */
    .deck-queue-container {
      display: flex;
      flex-direction: column;
      gap: 10px;
      overflow-y: auto;
      max-height: 520px;
    }

    .deck-queue-item {
      background: var(--bg-deep-space);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-md);
      padding: 12px 14px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .deck-queue-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
    }

    .deck-queue-desc {
      font-size: 13px;
      font-weight: 500;
      color: var(--text-primary);
      line-height: 1.4;
    }

    .deck-queue-badges {
      display: flex;
      align-items: center;
      gap: 6px;
    }

    .deck-priority-badge {
      font-size: 9px;
      font-weight: 800;
      text-transform: uppercase;
      padding: 2px 6px;
      border-radius: 3px;
      font-family: 'JetBrains Mono', monospace;
    }
    
    .deck-priority-badge.priority-low { background: rgba(83, 98, 121, 0.15); color: var(--color-low); border: 1px solid rgba(83, 98, 121, 0.3); }
    .deck-priority-badge.priority-medium { background: rgba(52, 152, 219, 0.15); color: var(--color-medium); border: 1px solid rgba(52, 152, 219, 0.3); }
    .deck-priority-badge.priority-high { background: rgba(230, 126, 34, 0.15); color: var(--color-high); border: 1px solid rgba(230, 126, 34, 0.3); }
    .deck-priority-badge.priority-critical { 
      background: rgba(231, 76, 60, 0.15); 
      color: var(--color-critical); 
      border: 1px solid rgba(231, 76, 60, 0.3);
      animation: deck-critical-pulse 1.5s infinite alternate;
    }

    @keyframes deck-critical-pulse {
      0% { box-shadow: 0 0 2px rgba(231, 76, 60, 0.1); }
      100% { box-shadow: 0 0 8px rgba(231, 76, 60, 0.5); }
    }

    .deck-agent-badge {
      font-size: 10px;
      font-weight: 700;
      color: var(--text-primary);
      padding: 2px 6px;
      border-radius: 3px;
      font-family: 'JetBrains Mono', monospace;
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--border-subtle);
      display: flex;
      align-items: center;
      gap: 4px;
    }

    .deck-queue-actions {
      display: flex;
      gap: 8px;
    }

    .deck-queue-btn {
      background: transparent;
      border: none;
      color: var(--text-secondary);
      cursor: pointer;
      font-size: 11px;
      font-family: 'JetBrains Mono', monospace;
      padding: 4px 8px;
      border-radius: 4px;
      transition: all var(--transition-speed) ease;
      border: 1px solid transparent;
    }

    .deck-queue-btn:hover {
      color: var(--text-primary);
      background: var(--bg-card);
      border-color: var(--border-subtle);
    }

    .deck-queue-btn.btn-cancel:hover {
      color: var(--color-critical);
      background: rgba(231, 76, 60, 0.1);
      border-color: rgba(231, 76, 60, 0.2);
    }

    /* Logs Console */
    .deck-log-nav {
      display: flex;
      border-bottom: 1px solid var(--border-subtle);
      gap: 4px;
      overflow-x: auto;
      margin-bottom: 10px;
    }

    .deck-log-tab {
      background: transparent;
      border: none;
      color: var(--text-secondary);
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      padding: 10px 16px;
      cursor: pointer;
      border-bottom: 2px solid transparent;
      transition: all var(--transition-speed) ease;
      display: flex;
      align-items: center;
      gap: 6px;
      white-space: nowrap;
    }

    .deck-log-tab.active {
      color: var(--agent-tab-color);
      border-bottom-color: var(--agent-tab-color);
      background: rgba(255, 255, 255, 0.03);
    }

    .deck-console {
      background: #020305;
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-md);
      padding: 16px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      line-height: 1.6;
      height: 320px;
      overflow-y: auto;
      color: #c5cdd8;
      box-shadow: inset 0 4px 15px rgba(0,0,0,0.8);
    }

    .deck-log-line {
      display: flex;
      gap: 12px;
      margin-bottom: 4px;
    }

    .deck-log-timestamp {
      color: var(--text-muted);
      user-select: none;
      flex-shrink: 0;
    }

    .deck-log-content {
      white-space: pre-wrap;
      word-break: break-all;
    }

    .deck-log-line.info .deck-log-content { color: #c5cdd8; }
    .deck-log-line.warn .deck-log-content { color: #f39c12; }
    .deck-log-line.error .deck-log-content { color: #e74c3c; font-weight: bold; }
    .deck-log-line.success .deck-log-content { color: #2ecc71; }

    /* Modals & Toasts */
    .deck-modal-overlay {
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0, 0, 0, 0.8);
      backdrop-filter: blur(4px);
      z-index: 1000;
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .deck-modal-card {
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      border-radius: var(--radius-lg);
      padding: 28px;
      width: 440px;
      max-width: 90%;
      box-shadow: 0 10px 30px rgba(0,0,0,0.6);
      display: flex;
      flex-direction: column;
      gap: 20px;
    }

    .deck-toast {
      position: fixed;
      bottom: 24px;
      right: 24px;
      background: var(--bg-card);
      border-left: 4px solid var(--color-kai);
      border-top: 1px solid var(--border-subtle);
      border-right: 1px solid var(--border-subtle);
      border-bottom: 1px solid var(--border-subtle);
      padding: 14px 20px;
      border-radius: var(--radius-md);
      box-shadow: 0 10px 25px rgba(0,0,0,0.5);
      z-index: 1000;
      font-size: 13px;
      display: flex;
      align-items: center;
      gap: 12px;
      animation: deck-toast-slide 0.2s cubic-bezier(0.175, 0.885, 0.32, 1.275);
    }

    @keyframes deck-toast-slide {
      from { transform: translateY(100px); opacity: 0; }
      to { transform: translateY(0); opacity: 1; }
    }

    /* Mobile drawer and media queries */
    .deck-mobile-toggle {
      position: fixed;
      bottom: 24px;
      right: 24px;
      z-index: 99;
      background: var(--color-antigravity);
      color: #000;
      border: none;
      width: 54px;
      height: 54px;
      border-radius: 50%;
      box-shadow: 0 4px 15px rgba(243, 156, 18, 0.4);
      cursor: pointer;
      display: none;
      align-items: center;
      justify-content: center;
      font-size: 20px;
    }

    @media (max-width: 768px) {
      .deck-dashboard-grid {
        grid-template-columns: 1fr;
      }

      .deck-agent-actions {
        grid-template-columns: 1fr 1fr;
        gap: 8px;
      }

      .deck-dispatch-form {
        grid-template-columns: 1fr;
        gap: 14px;
      }

      .deck-dispatch-btn, .deck-nav-tab, .deck-mode-btn, .deck-agent-btn {
        min-height: 48px; /* Large mobile tap targets */
      }

      .deck-mobile-toggle {
        display: flex;
      }

      .deck-drawer-panel {
        position: fixed;
        top: 0; right: 0; bottom: 0;
        width: 320px;
        background: var(--bg-card);
        border-left: 1px solid var(--border-subtle);
        box-shadow: -5px 0 25px rgba(0,0,0,0.5);
        z-index: 500;
        transform: translateX(100%);
        transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        display: flex;
        flex-direction: column;
      }

      .deck-drawer-panel.open {
        transform: translateX(0);
      }
    }
  `;
  document.head.appendChild(styleEl);

  const getTabFromUrl = () => {
    const path = window.location.pathname;
    const hash = window.location.hash;
    
    if (path.endsWith('/queue') || hash.includes('queue')) {
      return 'queue';
    }
    if (path.endsWith('/logs') || hash.includes('logs')) {
      return 'logs';
    }
    return 'control';
  };

  // 4. Primary Command Deck visual component
  function OrchestratorApp() {
    // --------------------------------------------------
    // State management
    // --------------------------------------------------
    const [currentTab, setCurrentTab] = React.useState(getTabFromUrl); // control, queue, logs
    
    // Sync tab from history popstate and hash change
    React.useEffect(() => {
      const syncTab = () => {
        setCurrentTab(getTabFromUrl());
      };
      window.addEventListener('popstate', syncTab);
      window.addEventListener('hashchange', syncTab);
      syncTab(); // initial sync
      return () => {
        window.removeEventListener('popstate', syncTab);
        window.removeEventListener('hashchange', syncTab);
      };
    }, []);

    const handleTabClick = (tabId) => {
      setCurrentTab(tabId);
      const isMockup = window.location.pathname.endsWith('.html');
      if (isMockup) {
        window.location.hash = `orchestrator/${tabId === 'control' ? '' : tabId}`;
      } else {
        const newPath = `/orchestrator${tabId === 'control' ? '' : '/' + tabId}`;
        if (window.location.pathname !== newPath) {
          window.history.pushState({ tab: tabId }, '', newPath);
        }
      }
      showToast(`Navigated to ${tabId.toUpperCase()}`, 'info');
    };
    const [currentMode, setCurrentMode] = React.useState('interactive'); // interactive, collaborative, autonomous
    const [activeLogAgent, setActiveLogAgent] = React.useState('Antigravity');
    const [mobileQueueOpen, setMobileQueueOpen] = React.useState(false);
    const [toast, setToast] = React.useState(null);

    // Dynamic Form States
    const [dispatchAgent, setDispatchAgent] = React.useState('Antigravity');
    const [dispatchDesc, setDispatchDesc] = React.useState('');
    const [dispatchPriority, setDispatchPriority] = React.useState('Medium');

    // Modal States
    const [modal, setModal] = React.useState({ isOpen: false, agentName: '', action: '' });

    // Swarm Co-Processors State (populated by API)
    const [agents, setAgents] = React.useState({
      Antigravity: { name: 'Antigravity', role: 'Main Spoke CLI', status: 'Running', task: 'Resolving Linear Issue GRO-1222', color: 'var(--color-antigravity)', glow: 'rgba(243, 156, 18, 0.15)', cpu_pct: 0, mem_mb: 0 },
      Jules: { name: 'Jules', role: 'Async Git & PR Agent', status: 'Idle', task: 'Idle · Listening to Linear webhooks', color: 'var(--color-jules)', glow: 'rgba(230, 126, 34, 0.15)', cpu_pct: 0, mem_mb: 0 },
      Codex: { name: 'Codex', role: 'Code reviewer specialist', status: 'Paused', task: 'Awaiting safe-directory approvals', color: 'var(--color-codex)', glow: 'rgba(231, 76, 60, 0.15)', cpu_pct: 0, mem_mb: 0 },
      Kai: { name: 'Kai', role: 'K3s cluster balancer', status: 'Running', task: 'Monitoring Sovereign Sentinel states', color: 'var(--color-kai)', glow: 'rgba(46, 204, 113, 0.15)', cpu_pct: 0, mem_mb: 0 },
      Fred: { name: 'Fred', role: 'Deployment staging gatekeeper', status: 'Idle', task: 'Idle · Awaiting staging run signals', color: 'var(--color-fred)', glow: 'rgba(52, 152, 219, 0.15)', cpu_pct: 0, mem_mb: 0 },
      Ned: { name: 'Ned', role: 'Swarm research synthesizer', status: 'Idle', task: 'Idle · Compiling logs backup', color: 'var(--color-ned)', glow: 'rgba(155, 89, 182, 0.15)', cpu_pct: 0, mem_mb: 0 }
    });

    // Task Queue State
    const [tasks, setTasks] = React.useState([]);

    // Logs Console State
    const [logs, setLogs] = React.useState({
      Antigravity: [], Jules: [], Codex: [], Kai: [], Fred: [], Ned: []
    });

    const consoleRef = React.useRef(null);

    // Fetch full state on mount and poll
    React.useEffect(() => {
      const fetchStatus = () => {
        api('/api/plugins/hermes-plugin-orchestrator-command-deck/status')
          .then(data => {
            if (data) {
              if (data.mode) setCurrentMode(data.mode);
              if (data.ui_agents) setAgents(data.ui_agents);
              if (data.tasks) setTasks(data.tasks);
            }
          })
          .catch(err => console.error("[command-deck] Failed to fetch status:", err));
      };

      fetchStatus();
      const interval = setInterval(fetchStatus, 3000);
      return () => clearInterval(interval);
    }, []);

    // Fetch active agent logs and poll
    React.useEffect(() => {
      const fetchLogs = () => {
        api(`/api/plugins/hermes-plugin-orchestrator-command-deck/agent/${activeLogAgent}/logs`)
          .then(data => {
            if (data && Array.isArray(data)) {
              setLogs(prev => ({
                ...prev,
                [activeLogAgent]: data
              }));
            }
          })
          .catch(err => console.error(`[command-deck] Failed to fetch logs for ${activeLogAgent}:`, err));
      };

      fetchLogs();
      const interval = setInterval(fetchLogs, 3000);
      return () => clearInterval(interval);
    }, [activeLogAgent]);

    // Autoscroll log console
    React.useEffect(() => {
      if (consoleRef.current) {
        consoleRef.current.scrollTop = consoleRef.current.scrollHeight;
      }
    }, [logs, activeLogAgent]);

    // Toast Timer
    React.useEffect(() => {
      if (toast) {
        const timer = setTimeout(() => setToast(null), 3000);
        return () => clearTimeout(timer);
      }
    }, [toast]);

    const showToast = (message, type = 'success') => {
      setToast({ message, type });
    };

    // --------------------------------------------------
    // Event Handlers
    // --------------------------------------------------
    const handleModeChange = (mode) => {
      api('/api/plugins/hermes-plugin-orchestrator-command-deck/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode })
      })
      .then(res => {
        if (res && res.success) {
          setCurrentMode(mode);
          showToast(`Orchestrator Mode switched to: ${mode.toUpperCase()}`, mode === 'autonomous' ? 'warn' : 'info');
        }
      })
      .catch(err => console.error("[command-deck] Failed to change mode:", err));
    };

    const handleFormSubmit = (e) => {
      e.preventDefault();
      if (!dispatchDesc) return;

      api('/api/plugins/hermes-plugin-orchestrator-command-deck/tasks/dispatch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          agent: dispatchAgent,
          description: dispatchDesc,
          priority: dispatchPriority
        })
      })
      .then(res => {
        if (res && res.success) {
          setDispatchDesc('');
          showToast(`Dispatched task to ${dispatchAgent}: "${dispatchDesc}"`, 'success');
          // Refresh tasks
          if (res.task) {
            setTasks(prev => [...prev, res.task]);
          }
        }
      })
      .catch(err => console.error("[command-deck] Failed to dispatch task:", err));
    };

    const triggerAction = (agentName, action) => {
      setModal({ isOpen: true, agentName, action });
    };

    const handleConfirmAction = () => {
      const { agentName, action } = modal;
      api(`/api/plugins/hermes-plugin-orchestrator-command-deck/agent/${agentName}/control`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action })
      })
      .then(res => {
        if (res && res.success) {
          setModal({ isOpen: false, agentName: '', action: '' });
          showToast(`Agent ${agentName} lifecycle transitioned to ${action.toUpperCase()}`, 'success');
          
          // Instantly update agent status locally to avoid visual lag
          setAgents(prev => {
            const updated = { ...prev };
            if (updated[agentName]) {
              updated[agentName].status = action === 'start' || action === 'resume' ? 'Running' : action === 'pause' ? 'Paused' : 'Terminated';
            }
            return updated;
          });
        }
      })
      .catch(err => console.error("[command-deck] Failed to control agent:", err));
    };

    const handleCancelTask = (id) => {
      api(`/api/plugins/hermes-plugin-orchestrator-command-deck/tasks/${id}/cancel`, {
        method: 'POST'
      })
      .then(res => {
        if (res && res.success) {
          showToast('Task canceled successfully', 'error');
          setTasks(prev => prev.filter(t => t.id !== id));
        }
      })
      .catch(err => console.error("[command-deck] Failed to cancel task:", err));
    };

    const handleShiftPriority = (id, directionVal) => {
      const direction = directionVal === -1 ? 'up' : 'down';
      api(`/api/plugins/hermes-plugin-orchestrator-command-deck/tasks/${id}/reorder`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ direction })
      })
      .then(res => {
        if (res && res.success && res.tasks) {
          setTasks(res.tasks);
          showToast('Task queue priority order shifted', 'info');
        }
      })
      .catch(err => console.error("[command-deck] Failed to reorder task:", err));
    };

    // Calculate dynamic state metrics
    const activeCount = Object.values(agents).filter(a => a.status === 'Running').length;

    // --------------------------------------------------
    // Rendering Sub-Structures
    // --------------------------------------------------
    const renderHeader = () => {
      return h('header', { className: 'deck-header' }, [
        h('div', { className: 'deck-brand' }, [
          h('div', { className: 'logo' }),
          h('div', null, [
            h('h1', { className: 'deck-brand-title' }, 'Orchestrator Control Deck'),
            h('p', { className: 'deck-brand-subtitle' }, 'Swarm Core · Agent Control Center')
          ])
        ]),
        h('div', { className: 'deck-header-info' }, [
          h('div', { className: 'deck-telemetry-item' }, [
            h('span', { className: 'deck-telemetry-label' }, 'System Mode'),
            h('span', { className: 'deck-telemetry-value', style: { color: currentMode === 'autonomous' ? 'var(--color-kai)' : currentMode === 'collaborative' ? 'var(--color-antigravity)' : 'var(--color-fred)' } }, currentMode.toUpperCase())
          ]),
          h('div', { className: 'deck-telemetry-item' }, [
            h('span', { className: 'deck-telemetry-label' }, 'Active Agents'),
            h('span', { className: 'deck-telemetry-value' }, `${activeCount} / 6`)
          ]),
          h('div', { className: 'deck-telemetry-item' }, [
            h('span', { className: 'deck-telemetry-label' }, 'Queue Backlog'),
            h('span', { className: 'deck-telemetry-value', style: { color: 'var(--color-antigravity)' } }, `${tasks.length} Tasks`)
          ]),
          h('div', { className: 'deck-telemetry-item' }, [
            h('span', { className: 'deck-telemetry-label' }, 'System Time'),
            h('span', { className: 'deck-telemetry-value' }, new Date().toTimeString().split(' ')[0])
          ])
        ])
      ]);
    };

    const renderNavTabs = () => {
      return h('div', { className: 'deck-nav-tabs' }, [
        h('button', { className: `deck-nav-tab ${currentTab === 'control' ? 'active' : ''}`, onClick: () => handleTabClick('control') }, '🎮 Control Panel'),
        h('button', { className: `deck-nav-tab ${currentTab === 'queue' ? 'active' : ''}`, onClick: () => handleTabClick('queue') }, '💼 Task Queue'),
        h('button', { className: `deck-nav-tab ${currentTab === 'logs' ? 'active' : ''}`, onClick: () => handleTabClick('logs') }, '📜 Swarm Logs')
      ]);
    };

    const renderModePanel = () => {
      return h('section', { className: 'deck-panel-card' }, [
        h('div', { className: 'deck-panel-header' }, [
          h('h2', { className: 'deck-panel-title' }, [
            h('span', { className: 'pulse-dot' }),
            'Orchestration Spectrum Mode'
          ])
        ]),
        h('div', { className: 'deck-mode-toggle-bar ' + currentMode }, [
          h('button', { className: `deck-mode-btn ${currentMode === 'interactive' ? 'active' : ''}`, onClick: () => handleModeChange('interactive') }, 'Interactive'),
          h('button', { className: `deck-mode-btn ${currentMode === 'collaborative' ? 'active' : ''}`, onClick: () => handleModeChange('collaborative') }, 'Collaborative'),
          h('button', { className: `deck-mode-btn ${currentMode === 'autonomous' ? 'active' : ''}`, onClick: () => handleModeChange('autonomous') }, 'Autonomous')
        ]),
        h('div', { className: 'deck-mode-description' }, 
          currentMode === 'interactive' ? 'Interactive Mode: High guardrail stance. Swarm agents run commands inside secure sandbox cages and must prompt for explicit user permission approvals before committing files, executing build commands, or running network tasks.' :
          currentMode === 'collaborative' ? 'Collaborative Mode: Semi-autonomous swarm. Agents coordinate on branches autonomously, but synchronize plans on Linear at critical pipeline junctions (such as pull request proposals or risk level classifications).' :
          'Autonomous Mode: Unrestricted agentic flow. The swarm autonomously plans, assigns subagents, resolves files, runs builds, and updates issue tracking logs, requesting human intervention only upon critical build failures.'
        )
      ]);
    };

    const renderDispatchPanel = () => {
      return h('section', { className: 'deck-panel-card' }, [
        h('div', { className: 'deck-panel-header' }, [
          h('h2', { className: 'deck-panel-title' }, '⚡ Command Dispatch Sandbox')
        ]),
        h('form', { className: 'deck-dispatch-form', onSubmit: handleFormSubmit }, [
          h('div', { className: 'deck-form-group' }, [
            h('label', { className: 'deck-form-label' }, 'Target Agent Wavelength'),
            h('select', { className: 'deck-form-select', value: dispatchAgent, onChange: e => setDispatchAgent(e.target.value) }, [
              h('option', { value: 'Antigravity' }, 'Antigravity (Amber)'),
              h('option', { value: 'Jules' }, 'Jules (Orange)'),
              h('option', { value: 'Codex' }, 'Codex (Red)'),
              h('option', { value: 'Kai' }, 'Kai (Green)'),
              h('option', { value: 'Fred' }, 'Fred (Blue)'),
              h('option', { value: 'Ned' }, 'Ned (Purple)')
            ])
          ]),
          h('div', { className: 'deck-form-group' }, [
            h('label', { className: 'deck-form-label' }, 'Task Description'),
            h('input', { className: 'deck-form-input', type: 'text', placeholder: 'e.g. Optimize resource configurations...', value: dispatchDesc, onChange: e => setDispatchDesc(e.target.value), required: true })
          ]),
          h('div', { className: 'deck-form-group' }, [
            h('label', { className: 'deck-form-label' }, 'Task Priority'),
            h('select', { className: 'deck-form-select', value: dispatchPriority, onChange: e => setDispatchPriority(e.target.value) }, [
              h('option', { value: 'Low' }, 'Low'),
              h('option', { value: 'Medium' }, 'Medium'),
              h('option', { value: 'High' }, 'High'),
              h('option', { value: 'Critical' }, 'Critical')
            ])
          ]),
          h('button', { type: 'submit', className: 'deck-dispatch-btn' }, 'Dispatch')
        ])
      ]);
    };

    const renderAgentControlsPanel = () => {
      return h('section', { className: 'deck-panel-card' }, [
        h('div', { className: 'deck-panel-header' }, [
          h('h2', { className: 'deck-panel-title' }, '📡 Agent Co-Processors')
        ]),
        h('div', { className: 'deck-agent-grid' }, Object.values(agents).map(agent => {
          const isRunning = agent.status === 'Running';
          const isPaused = agent.status === 'Paused';
          const isIdle = agent.status === 'Idle';
          const isTerminated = agent.status === 'Terminated';

          return h('div', { key: agent.name, className: 'deck-agent-card ' + agent.status.toLowerCase(), style: { '--agent-color': agent.color, '--agent-color-glow': agent.glow } }, [
            h('div', { className: 'deck-agent-info' }, [
              h('div', null, [
                h('span', { className: 'deck-agent-title' }, [
                  h('span', { style: { backgroundColor: agent.color, width: 8, height: 8, display: 'inline-block', borderRadius: '50%' } }),
                  agent.name
                ]),
                h('span', { className: 'deck-agent-role' }, agent.role)
              ]),
              h('span', { className: `deck-agent-status-badge status-${agent.status.toLowerCase()}` }, agent.status)
            ]),
            h('div', { className: 'deck-agent-task', title: agent.task }, `Task: ${agent.task}`),
            h('div', { className: 'deck-agent-telemetry' }, [
              h('span', null, `CPU: ${agent.cpu_pct || 0.0}%`),
              h('span', null, `Mem: ${agent.mem_mb || 0.0} MB`),
              h('span', null, `PIDs: ${agent.processes || 0}`)
            ]),
            h('div', { className: 'deck-agent-actions' }, [
              h('button', { className: 'deck-agent-btn', onClick: () => triggerAction(agent.name, 'start'), disabled: isRunning || isPaused }, 'Start'),
              h('button', { className: 'deck-agent-btn', onClick: () => triggerAction(agent.name, 'pause'), disabled: !isRunning }, 'Pause'),
              h('button', { className: 'deck-agent-btn', onClick: () => triggerAction(agent.name, 'resume'), disabled: !isPaused }, 'Resume'),
              h('button', { className: 'deck-agent-btn btn-kill', onClick: () => triggerAction(agent.name, 'kill'), disabled: isTerminated || isIdle }, 'Kill')
            ])
          ]);
        }))
      ]);
    };

    const renderQueuePanel = (isMobileView) => {
      const items = tasks.map((task, index) => {
        const color = agents[task.agent] ? agents[task.agent].color : 'var(--text-muted)';
        return h('div', { key: task.id, className: 'deck-queue-item' }, [
          h('div', { className: 'deck-queue-row' }, [
            h('span', { className: 'deck-queue-desc' }, task.desc)
          ]),
          h('div', { className: 'deck-queue-row' }, [
            h('div', { className: 'deck-queue-badges' }, [
              h('span', { className: `deck-priority-badge priority-${task.priority.toLowerCase()}` }, task.priority),
              h('span', { className: 'deck-agent-badge' }, [
                h('span', { className: 'agent-badge-dot', style: { backgroundColor: color } }),
                task.agent
              ])
            ]),
            h('span', { className: 'queue-time' }, `${task.age || 0}m ago`)
          ]),
          h('div', { className: 'deck-queue-row', style: { marginTop: 4, borderTop: '1px solid rgba(255,255,255,0.03)', paddingTop: 6 } }, [
            h('div', { className: 'deck-queue-actions' }, [
              h('button', { className: 'deck-queue-btn', onClick: () => handleShiftPriority(task.id, -1), disabled: index === 0 }, '▲ Up'),
              h('button', { className: 'deck-queue-btn', onClick: () => handleShiftPriority(task.id, 1), disabled: index === tasks.length - 1 }, '▼ Down')
            ]),
            h('button', { className: 'deck-queue-btn btn-cancel', onClick: () => handleCancelTask(task.id) }, 'Cancel')
          ])
        ]);
      });

      return h('div', { className: `deck-panel-card deck-drawer-panel ${mobileQueueOpen ? 'open' : ''}`, style: isMobileView ? {} : { flex: '1' } }, [
        h('div', { className: 'deck-panel-header' }, [
          h('h2', { className: 'deck-panel-title' }, '💼 Swarm Task Queue'),
          isMobileView && h('button', { style: { background: 'transparent', border: 'none', color: '#fff', fontSize: 20 }, onClick: () => setMobileQueueOpen(false) }, '×')
        ]),
        h('div', { className: 'deck-queue-container' }, 
          tasks.length === 0 ? h('div', { className: 'queue-empty-msg' }, 'No pending tasks in queue.') : items
        )
      ]);
    };

    const renderLogsPanel = () => {
      const lines = logs[activeLogAgent] || [];

      return h('section', { className: 'deck-panel-card log-viewer-panel' }, [
        h('div', { className: 'deck-panel-header' }, [
          h('h2', { className: 'deck-panel-title' }, '📜 Agent Logs Console')
        ]),
        h('div', { className: 'deck-log-nav' }, Object.keys(agents).map(key => {
          return h('button', {
            key,
            className: `deck-log-tab ${activeLogAgent === key ? 'active' : ''}`,
            style: { '--agent-tab-color': agents[key].color },
            onClick: () => setActiveLogAgent(key)
          }, [
            h('span', { className: 'log-agent-tab-dot' }),
            agents[key].name
          ]);
        })),
        h('div', { ref: consoleRef, className: 'deck-console' }, lines.map((line, idx) => {
          return h('div', { key: idx, className: `deck-log-line ${line.level.toLowerCase()}` }, [
            h('span', { className: 'deck-log-timestamp' }, line.time),
            h('span', { className: 'deck-log-content' }, line.text)
          ]);
        })),
        h('div', { style: { display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', marginTop: 8 } }, [
          h('span', null, `Viewing ${activeLogAgent} (last ${lines.length} lines)`),
          h('span', null, 'Monospace Output · Autoscroll Active')
        ])
      ]);
    };

    const renderConfirmationModal = () => {
      if (!modal.isOpen) return null;
      return h('div', { className: 'deck-modal-overlay' }, [
        h('div', { className: 'deck-modal-card' }, [
          h('h3', { className: 'modal-title' }, '⚠️ Confirm Swarm Intervention'),
          h('p', { className: 'modal-desc' }, [
            'Are you sure you want to perform the action ',
            h('strong', null, modal.action.toUpperCase()),
            ' on agent co-processor ',
            h('strong', null, modal.agentName),
            '?',
            h('br'), h('br'),
            'This will affect its active thread context and update its live execution lifecycle state.'
          ]),
          h('div', { className: 'modal-actions' }, [
            h('button', { className: 'modal-btn modal-btn-cancel', onClick: () => setModal({ isOpen: false, agentName: '', action: '' }) }, 'Cancel'),
            h('button', { className: `modal-btn modal-btn-confirm ${modal.action === 'kill' ? 'kill-style' : ''}`, onClick: handleConfirmAction }, 'Confirm')
          ])
        ])
      ]);
    };

    const renderToast = () => {
      if (!toast) return null;
      let col = 'var(--color-kai)';
      let sym = '✓';
      if (toast.type === 'error') { col = 'var(--color-codex)'; sym = '🛑'; }
      if (toast.type === 'warn') { col = 'var(--color-antigravity)'; sym = '⚠️'; }
      if (toast.type === 'info') { col = 'var(--color-fred)'; sym = '🔍'; }

      return h('div', { className: 'deck-toast', style: { borderLeftColor: col } }, [
        h('span', null, sym),
        h('span', null, toast.message)
      ]);
    };

    // Responsive helper checks
    const [isMobile, setIsMobile] = React.useState(false);
    React.useEffect(() => {
      const checkMobile = () => setIsMobile(window.innerWidth <= 768);
      checkMobile();
      window.addEventListener('resize', checkMobile);
      return () => window.removeEventListener('resize', checkMobile);
    }, []);

    // --------------------------------------------------
    // Main Component Assembler
    // --------------------------------------------------
    const controlSection = [
      renderModePanel(),
      renderDispatchPanel(),
      renderAgentControlsPanel()
    ];

    const gridChildren = [];
    if (isMobile) {
      if (currentTab === 'control') {
        gridChildren.push(h('div', { style: { display: 'flex', flexDirection: 'column', gap: 20 } }, controlSection));
      } else if (currentTab === 'queue') {
        gridChildren.push(renderQueuePanel(false));
      } else if (currentTab === 'logs') {
        gridChildren.push(renderLogsPanel());
      }
    } else {
      // Desktop View - Render ONLY the active tab's panel and span full 2 columns
      if (currentTab === 'control') {
        gridChildren.push(h('div', { style: { display: 'flex', flexDirection: 'column', gap: 20, gridColumn: 'span 2' } }, controlSection));
      } else if (currentTab === 'queue') {
        gridChildren.push(h('div', { style: { gridColumn: 'span 2' } }, [renderQueuePanel(false)]));
      } else if (currentTab === 'logs') {
        gridChildren.push(renderLogsPanel()); // Already has .log-viewer-panel class with grid-column: span 2
      }
    }

    return h('div', { className: 'deck-container' }, [
      renderHeader(),
      renderNavTabs(),
      h('div', { className: 'deck-dashboard-grid' }, gridChildren),
      isMobile && h('button', { className: 'deck-mobile-toggle', onClick: () => setMobileQueueOpen(true) }, '💼'),
      isMobile && renderQueuePanel(true),
      renderConfirmationModal(),
      renderToast(),
      h(SyncInjector)
    ]);
  }

  // Cross-tab sync injector component
  function SyncInjector() {
    React.useEffect(() => {
      const channel = new window.BroadcastChannel("hermes-tab-sync");
      let debounceTimeout = null;

      // 1. Fetch initial state on mount
      api('/api/dashboard/composer-state')
        .then(data => {
          if (data && data.state && data.state.draft) {
            applyDraft(data.state.draft);
          }
        })
        .catch(err => console.error("[tab-sync] Failed to load composer state:", err));

      function applyDraft(value) {
        const composer = findComposer();
        if (composer && composer.value !== value) {
          try {
            const valueSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
            valueSetter.call(composer, value);
            composer.dispatchEvent(new Event('input', { bubbles: true }));
          } catch (e) {
            composer.value = value;
          }
          localStorage.setItem('hermes-composer-draft', value);
        }
      }

      function findComposer() {
        return document.querySelector('textarea#chat-input') || 
               document.querySelector('textarea[placeholder*="Ask"]') || 
               document.querySelector('textarea[placeholder*="message"]') || 
               document.querySelector('textarea[placeholder*="Hermes"]') ||
               document.querySelector('textarea');
      }

      // 2. Listen to input events
      const handleInput = (e) => {
        const composer = findComposer();
        if (e.target === composer) {
          const val = composer.value;
          localStorage.setItem('hermes-composer-draft', val);
          channel.postMessage({ type: 'composer-draft', value: val });

          if (debounceTimeout) clearTimeout(debounceTimeout);
          debounceTimeout = setTimeout(() => {
            api('/api/dashboard/composer-state', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ state: { draft: val } })
            }).catch(err => console.error("[tab-sync] Failed to save composer state:", err));
          }, 1000);
        }
      };

      document.addEventListener('input', handleInput, true);

      // 3. Listen to channel broadcasts
      channel.onmessage = (event) => {
        if (event.data && event.data.type === 'composer-draft') {
          applyDraft(event.data.value);
        }
      };

      // Periodic check in case composer renders late
      const pollInterval = setInterval(() => {
        const composer = findComposer();
        if (composer) {
          const localVal = localStorage.getItem('hermes-composer-draft');
          if (localVal && composer.value !== localVal && document.activeElement !== composer) {
            applyDraft(localVal);
          }
        }
      }, 1000);

      return () => {
        document.removeEventListener('input', handleInput, true);
        channel.close();
        if (debounceTimeout) clearTimeout(debounceTimeout);
        clearInterval(pollInterval);
      };
    }, []);

    return null;
  }

  // Register slot & component
  if (window.registerSlot) {
    window.registerSlot("hermes-plugin-orchestrator-command-deck", "global-injector", SyncInjector);
  }

  registry.register('hermes-plugin-orchestrator-command-deck', OrchestratorApp);
  console.log('[orchestrator-command-deck] React plugin registered successfully.');
})();
