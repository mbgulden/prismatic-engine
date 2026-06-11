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
    }

    .deck-queue-btn:hover {
      color: var(--text-primary);
      background: var(--bg-card);
    }

    .deck-queue-btn.btn-cancel:hover {
      color: var(--color-critical);
      background: rgba(231, 76, 60, 0.1);
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
    }

    .deck-log-line.info { color: #c5cdd8; }
    .deck-log-line.warn { color: #f39c12; }
    .deck-log-line.error { color: #e74c3c; font-weight: bold; }
    .deck-log-line.success { color: #2ecc71; }

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

  // 4. Primary Command Deck visual component
  function OrchestratorApp() {
    // --------------------------------------------------
    // State management
    // --------------------------------------------------
    const [currentTab, setCurrentTab] = React.useState('control'); // control, queue, logs
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

    // Swarm Co-Processors State
    const [agents, setAgents] = React.useState({
      Antigravity: { name: 'Antigravity', role: 'Main Spoke CLI', status: 'Running', task: 'Resolving Linear Issue GRO-1222', color: 'var(--color-antigravity)', glow: 'rgba(243, 156, 18, 0.15)' },
      Jules: { name: 'Jules', role: 'Async Git & PR Agent', status: 'Idle', task: 'Idle · Listening to Linear webhooks', color: 'var(--color-jules)', glow: 'rgba(230, 126, 34, 0.15)' },
      Codex: { name: 'Codex', role: 'Code reviewer specialist', status: 'Paused', task: 'Awaiting safe-directory approvals', color: 'var(--color-codex)', glow: 'rgba(231, 76, 60, 0.15)' },
      Kai: { name: 'Kai', role: 'K3s cluster balancer', status: 'Running', task: 'Monitoring Sovereign Sentinel states', color: 'var(--color-kai)', glow: 'rgba(46, 204, 113, 0.15)' },
      Fred: { name: 'Fred', role: 'Deployment staging gatekeeper', status: 'Idle', task: 'Idle · Awaiting staging run signals', color: 'var(--color-fred)', glow: 'rgba(52, 152, 219, 0.15)' },
      Ned: { name: 'Ned', role: 'Swarm research synthesizer', status: 'Idle', task: 'Idle · Compiling logs backup', color: 'var(--color-ned)', glow: 'rgba(155, 89, 182, 0.15)' }
    });

    // Task Queue State
    const [tasks, setTasks] = React.useState([
      { id: 1, agent: 'Antigravity', desc: 'Implement high-fidelity HTML command deck for command-deck plugin', priority: 'Critical', age: 2 },
      { id: 2, agent: 'Jules', desc: 'Sync local git safe.directory configurations with swarm profiles', priority: 'High', age: 8 },
      { id: 3, agent: 'Kai', desc: 'Check K3s namespaces for stale agent-worker containers', priority: 'Medium', age: 15 },
      { id: 4, agent: 'Codex', desc: 'Verify changes in PR #42 against security policies', priority: 'Low', age: 34 }
    ]);

    // Logs Console State
    const [logs, setLogs] = React.useState({
      Antigravity: [], Jules: [], Codex: [], Kai: [], Fred: [], Ned: []
    });

    const consoleRef = React.useRef(null);

    // Seed logs on mount
    React.useEffect(() => {
      const levels = ['INFO', 'SUCCESS', 'WARN'];
      const actions = {
        Antigravity: ['Command resolved successfully', 'Permission safe check: ok', 'Broadcasted tab-sync composer state change', 'Reading manifest.json under orchestrator-command-deck'],
        Jules: ['Checking commits on dev-branch', 'Linear event match detected: GRO-123', 'Rebasing workspace logs to local backup', 'Git fetch complete: origin'],
        Codex: ['Parsing security abstract syntax trees', 'Safe directory audit: no violations found', 'Review completed for diff PR #12', 'VRAM watchdog check: 24% capacity'],
        Kai: ['Autonomously balancing resource threads', 'Sentinel check: heartbeat response in 12ms', 'Flushing SQLite cached event log keys', 'K3s cluster telemetry matched baseline'],
        Fred: ['Listening for local package release hook', 'Staging container setup: verified safe', 'Pty keepalive timeout updated to 120m', 'Safe gate checks passed'],
        Ned: ['Research query finalized: Safe directory exceptions', 'Synthesizing report: agent-runs output', 'Linear ticket GRO-1222 re-labelled to agent:ned', 'Archived legacy configs']
      };

      const initialLogs = {};
      Object.keys(actions).forEach(agent => {
        initialLogs[agent] = [];
        let baseTime = new Date();
        baseTime.setMinutes(baseTime.getMinutes() - 40);
        for(let i = 0; i < 35; i++) {
          baseTime.setSeconds(baseTime.getSeconds() + 45);
          const timeStr = baseTime.toTimeString().split(' ')[0];
          const lvl = levels[Math.floor(Math.random() * levels.length)];
          const act = actions[agent][i % actions[agent].length] + ' (' + i + ')';
          initialLogs[agent].push({ time: timeStr, level: lvl, text: `[${lvl}] ${act}` });
        }
      });
      setLogs(initialLogs);
    }, []);

    // Periodic live log appender
    React.useEffect(() => {
      const interval = setInterval(() => {
        // Find a running agent
        const runningKeys = Object.keys(agents).filter(k => agents[k].status === 'Running');
        if (runningKeys.length === 0) return;
        const randomKey = runningKeys[Math.floor(Math.random() * runningKeys.length)];

        const liveMessages = [
          'Checking memory allocation profiles... OK',
          'Syncing BroadcastChannel state across local storage tabs',
          'Validating signature hashes on active tree namespaces',
          'Executing safe subprocess execution context',
          'Telemetry verified: K3s node state within limits',
          'Polling webhook event buffer queue',
          'Applying layout rendering updates to DOM frame'
        ];
        const msg = liveMessages[Math.floor(Math.random() * liveMessages.length)];
        const level = Math.random() > 0.8 ? 'WARN' : 'INFO';
        const timeStr = new Date().toTimeString().split(' ')[0];

        setLogs(prev => {
          const updated = { ...prev };
          if (!updated[randomKey]) updated[randomKey] = [];
          updated[randomKey] = [...updated[randomKey], { time: timeStr, level: level, text: `[${level}] ${msg}` }].slice(-50);
          return updated;
        });
      }, 3000);

      return () => clearInterval(interval);
    }, [agents]);

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
      setCurrentMode(mode);
      showToast(`Orchestrator Mode switched to: ${mode.toUpperCase()}`, mode === 'autonomous' ? 'warn' : 'info');
    };

    const handleFormSubmit = (e) => {
      e.preventDefault();
      if (!dispatchDesc) return;

      const newTask = {
        id: Date.now(),
        agent: dispatchAgent,
        desc: dispatchDesc,
        priority: dispatchPriority,
        age: 0
      };

      setTasks(prev => [...prev, newTask]);
      setDispatchDesc('');
      showToast(`Dispatched task to ${dispatchAgent}: "${dispatchDesc}"`, 'success');

      // Add to log
      const timeStr = new Date().toTimeString().split(' ')[0];
      setLogs(prev => {
        const updated = { ...prev };
        updated[dispatchAgent] = [...(updated[dispatchAgent] || []), {
          time: timeStr,
          level: 'INFO',
          text: `[INFO] Received new Sandbox Dispatch command: "${dispatchDesc}"`
        }].slice(-50);
        return updated;
      });
    };

    const triggerAction = (agentName, action) => {
      setModal({ isOpen: true, agentName, action });
    };

    const handleConfirmAction = () => {
      const { agentName, action } = modal;
      setAgents(prev => {
        const updated = { ...prev };
        const agent = updated[agentName];
        if (agent) {
          if (action === 'start') {
            agent.status = 'Running';
            agent.task = 'Initializing active loop context...';
          } else if (action === 'pause') {
            agent.status = 'Paused';
          } else if (action === 'resume') {
            agent.status = 'Running';
          } else if (action === 'kill') {
            agent.status = 'Terminated';
            agent.task = 'Terminated by operator signal';
          }
        }
        return updated;
      });

      // Log the lifecycle change
      const timeStr = new Date().toTimeString().split(' ')[0];
      const lvl = action === 'kill' ? 'ERROR' : action === 'pause' ? 'WARN' : 'SUCCESS';
      const txt = `[${lvl}] Lifecycle status transitioned to: ${action.toUpperCase()} via Command Deck`;
      setLogs(prev => {
        const updated = { ...prev };
        updated[agentName] = [...(updated[agentName] || []), { time: timeStr, level: lvl, text: txt }].slice(-50);
        return updated;
      });

      setModal({ isOpen: false, agentName: '', action: '' });
      showToast(`Agent ${agentName} lifecycle transitioned to ${action.toUpperCase()}`, 'success');
    };

    const handleCancelTask = (id) => {
      const task = tasks.find(t => t.id === id);
      setTasks(prev => prev.filter(t => t.id !== id));
      if (task) {
        showToast(`Canceled task: "${task.desc}"`, 'error');
        // Log cancel
        const timeStr = new Date().toTimeString().split(' ')[0];
        setLogs(prev => {
          const updated = { ...prev };
          updated[task.agent] = [...(updated[task.agent] || []), {
            time: timeStr,
            level: 'WARN',
            text: `[WARN] Canceled queue task manually by user request`
          }].slice(-50);
          return updated;
        });
      }
    };

    const handleShiftPriority = (id, direction) => {
      const idx = tasks.findIndex(t => t.id === id);
      if (idx === -1) return;
      const targetIdx = idx + direction;
      if (targetIdx < 0 || targetIdx >= tasks.length) return;

      const newTasks = [...tasks];
      const temp = newTasks[idx];
      newTasks[idx] = newTasks[targetIdx];
      newTasks[targetIdx] = temp;
      setTasks(newTasks);
      showToast('Task queue priority order shifted', 'info');
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
        h('button', { className: `deck-nav-tab ${currentTab === 'control' ? 'active' : ''}`, onClick: () => setCurrentTab('control') }, '🎮 Control Panel'),
        h('button', { className: `deck-nav-tab ${currentTab === 'queue' ? 'active' : ''}`, onClick: () => setCurrentTab('queue') }, '💼 Task Queue'),
        h('button', { className: `deck-nav-tab ${currentTab === 'logs' ? 'active' : ''}`, onClick: () => setCurrentTab('logs') }, '📜 Swarm Logs')
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
              h('span', { className: `priority-badge priority-${task.priority.toLowerCase()}` }, task.priority),
              h('span', { className: 'deck-agent-badge' }, [
                h('span', { className: 'agent-badge-dot', style: { backgroundColor: color } }),
                task.agent
              ])
            ]),
            h('span', { className: 'queue-time' }, `${task.age}m ago`)
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
      const activeColor = agents[activeLogAgent] ? agents[activeLogAgent].color : 'var(--text-muted)';
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
            h('span', null, line.text)
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
      // Desktop View
      gridChildren.push(h('div', { style: { display: 'flex', flexDirection: 'column', gap: 20 } }, controlSection));
      gridChildren.push(renderQueuePanel(false));
      gridChildren.push(renderLogsPanel());
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

  // Cross-tab sync injector component (copied from legacy code block)
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
