import { Terminal } from 'xterm';

(function () {
  const sdk = window.__HERMES_PLUGIN_SDK__;
  const registry = window.__HERMES_PLUGINS__;
  if (!sdk || !registry) {
    console.warn('[swarm-manager] Hermes plugin SDK not available');
    return;
  }

  const React = sdk.React;
  const h = React.createElement;
  const api = sdk.fetchJSON || sdk.api;

  // Autocomplete suggestions for slash commands
  const SLASH_COMMANDS = [
    { cmd: '/goal ', desc: 'Enforces persistent non-interactive execution' },
    { cmd: '/grill-me ', desc: 'Triggers interactive alignment mode' },
    { cmd: '/schedule ', desc: 'Sets up recurring cron schedules' },
    { cmd: '/browser ', desc: 'Launches isolated browser session' }
  ];

  // Dynamic injector component to sync composer drafts globally
  function GlobalSyncInjector() {
    React.useEffect(() => {
      const channel = new window.BroadcastChannel("hermes-tab-sync");
      let debounceTimeout = null;

      function findComposer() {
        return document.querySelector('textarea#chat-input') || 
               document.querySelector('textarea[placeholder*="Ask"]') || 
               document.querySelector('textarea[placeholder*="message"]') || 
               document.querySelector('textarea[placeholder*="Hermes"]') ||
               document.querySelector('textarea');
      }

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

      // Fetch initial draft
      api('/api/dashboard/composer-state')
        .then(data => {
          if (data && data.state && data.state.draft) {
            applyDraft(data.state.draft);
          }
        }).catch(err => console.error("[swarm-sync] Load failed:", err));

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
            }).catch(err => console.error("[swarm-sync] Save failed:", err));
          }, 1000);
        }
      };

      document.addEventListener('input', handleInput, true);

      channel.onmessage = (event) => {
        if (event.data && event.data.type === 'composer-draft') {
          applyDraft(event.data.value);
        }
      };

      return () => {
        document.removeEventListener('input', handleInput, true);
        channel.close();
        if (debounceTimeout) clearTimeout(debounceTimeout);
      };
    }, []);

    return null;
  }

  // Register slot
  if (window.registerSlot) {
    window.registerSlot("hermes-plugin-swarm-manager", "global-injector", GlobalSyncInjector);
  }

  // Main UI Component
  function SwarmManagerApp() {
    const [workspaces, setWorkspaces] = React.useState([
      { id: 'agentic-swarm-ops', name: 'Agentic Swarm Ops', path: '/home/ubuntu/work/agentic-swarm-ops', icon: '💼' },
      { id: 'local-gdrive-mcp', name: 'local-gdrive-mcp', path: '/home/ubuntu/work/local-gdrive-mcp', icon: '📂' },
      { id: 'sovereign-sentinel', name: 'Sovereign Sentinel', path: '/home/ubuntu/mounts/synology-agentic-context/sovereign-sentinel', icon: '🔒' },
      { id: 'sentinel-it-asset-logistics', name: 'Sentinel IT Asset Logistics', path: '/home/ubuntu/mounts/synology-agentic-context/sentinel-it-asset-logistics', icon: '📦' }
    ]);
    const [selectedWorkspace, setSelectedWorkspace] = React.useState('agentic-swarm-ops');
    const [sessions, setSessions] = React.useState([]);
    const [activeSessionId, setActiveSessionId] = React.useState(null);
    const [messages, setMessages] = React.useState([]);
    const [composerText, setComposerText] = React.useState('');
    const [showSuggestions, setShowSuggestions] = React.useState(false);
    const [suggestedCommands, setSuggestedCommands] = React.useState([]);
    const [wsConnected, setWsConnected] = React.useState(false);
    const [sidebarCollapsed, setSidebarCollapsed] = React.useState(window.innerWidth < 768);
    const [windowWidth, setWindowWidth] = React.useState(window.innerWidth);

    // Modal configuration overlay states
    const [showModal, setShowModal] = React.useState(false);
    const [modalTitle, setModalTitle] = React.useState('');
    const [modalProfile, setModalProfile] = React.useState('orchestrator');
    const [modalBranch, setModalBranch] = React.useState('main');
    const [modalIssue, setModalIssue] = React.useState('');

    // Dynamic dropdown states
    const [gitBranches, setGitBranches] = React.useState([]);
    const [useCustomBranch, setUseCustomBranch] = React.useState(false);
    const [linearIssues, setLinearIssues] = React.useState([]);
    const [useCustomIssue, setUseCustomIssue] = React.useState(false);

    // Antigravity 2.0 State Upgrades
    const [searchQuery, setSearchQuery] = React.useState('');
    const [expandedBranches, setExpandedBranches] = React.useState({ 'main': true });
    const [terminalFontSize, setTerminalFontSize] = React.useState(13);
    const [terminalMaximized, setTerminalMaximized] = React.useState(false);
    const [editingSessionId, setEditingSessionId] = React.useState(null);
    const [editingSessionTitle, setEditingSessionTitle] = React.useState('');

    const terminalRef = React.useRef(null);
    const termInstance = React.useRef(null);
    const socketRef = React.useRef(null);

    // Keep up-to-date refs of polled states for terminal connection to avoid flickering
    const sessionsRef = React.useRef(sessions);
    sessionsRef.current = sessions;
    const workspacesRef = React.useRef(workspaces);
    workspacesRef.current = workspaces;
    const selectedWorkspaceRef = React.useRef(selectedWorkspace);
    selectedWorkspaceRef.current = selectedWorkspace;

    // Google Antigravity 2.0 CSS Injection
    React.useEffect(() => {
      const styleId = 'antigravity-premium-styles';
      if (!document.getElementById(styleId)) {
        const style = document.createElement('style');
        style.id = styleId;
        style.innerHTML = `
          /* Google Antigravity 2.0 Custom Scrollbars */
          .custom-scrollbar::-webkit-scrollbar {
            width: 6px;
            height: 6px;
          }
          .custom-scrollbar::-webkit-scrollbar-track {
            background: rgba(0, 0, 0, 0.2);
            border-radius: 3px;
          }
          .custom-scrollbar::-webkit-scrollbar-thumb {
            background: rgba(99, 102, 241, 0.3);
            border-radius: 3px;
            transition: background 0.3s;
          }
          .custom-scrollbar::-webkit-scrollbar-thumb:hover {
            background: rgba(99, 102, 241, 0.6);
          }

          /* Hover Effects */
          .premium-card {
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
          }
          .premium-card:hover {
            transform: translateY(-1.5px);
            background-color: rgba(255, 255, 255, 0.04) !important;
            border-color: rgba(99, 102, 241, 0.3) !important;
            box-shadow: 0 4px 20px rgba(99, 102, 241, 0.15);
          }

          .premium-folder {
            transition: all 0.3s ease;
          }
          .premium-folder:hover {
            background-color: rgba(255, 255, 255, 0.02) !important;
            border-color: rgba(255, 255, 255, 0.08) !important;
          }

          .action-btn-hover {
            transition: all 0.2s ease;
            opacity: 0.5;
          }
          .action-btn-hover:hover {
            opacity: 1;
            transform: scale(1.1);
          }

          /* Glassmorphic Elements */
          .glass-panel {
            background: rgba(10, 10, 18, 0.6) !important;
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
          }
        `;
        document.head.appendChild(style);
      }
    }, []);

    // Toggle branch folders
    const toggleBranch = React.useCallback((branchName) => {
      setExpandedBranches(prev => ({
        ...prev,
        [branchName]: !prev[branchName]
      }));
    }, []);

    // Session editing handlers
    const startEditingSession = React.useCallback((sessionId, currentTitle) => {
      setEditingSessionId(sessionId);
      setEditingSessionTitle(currentTitle);
    }, []);

    const saveEditingSession = React.useCallback((sessionId) => {
      if (editingSessionTitle.trim()) {
        setSessions(prev => prev.map(s => s.id === sessionId ? { ...s, title: editingSessionTitle.trim() } : s));
      }
      setEditingSessionId(null);
    }, [editingSessionTitle]);

    const deleteSession = React.useCallback((sessionId) => {
      setSessions(prev => prev.filter(s => s.id !== sessionId));
      if (activeSessionId === sessionId) {
        setActiveSessionId(null);
      }
    }, [activeSessionId]);

    const duplicateSession = React.useCallback((session) => {
      const newId = 'session-' + Math.random().toString(36).substring(2, 15);
      const duplicated = {
        ...session,
        id: newId,
        title: `${session.title || 'Session'} (Copy)`,
        message_count: 0,
        estimated_cost_usd: 0,
        is_active: true
      };
      setSessions(prev => [duplicated, ...prev]);
      setActiveSessionId(newId);
    }, []);

    // Handle responsive resize
    React.useEffect(() => {
      const handleResize = () => {
        setWindowWidth(window.innerWidth);
        if (window.innerWidth < 768) {
          setSidebarCollapsed(true);
        }
      };
      window.addEventListener('resize', handleResize);
      return () => window.removeEventListener('resize', handleResize);
    }, []);

    // Fetch workspaces configuration on mount
    React.useEffect(() => {
      api('/api/config')
        .then(cfg => {
          if (cfg && cfg.workspaces && Array.isArray(cfg.workspaces)) {
            const icons = {
              'agentic-swarm-ops': '💼',
              'sovereign-sentinel': '🔒',
              'sentinel-it-asset-logistics': '📦',
              'active-oahu': '🌴',
              'asset-forge-3d': '🎮',
              'google-drive-gemini-context': '📂',
              'hermes-inbox': '📥'
            };
            const mapped = cfg.workspaces.map(w => ({
              id: w.id,
              name: w.name || w.id,
              path: (w.local_paths && w.local_paths[0]) || w.nas_path || '',
              icon: icons[w.id] || '📂'
            }));
            setWorkspaces(mapped);
            if (mapped.length > 0) {
              setSelectedWorkspace(mapped[0].id);
            }
          }
        })
        .catch(err => console.error("[swarm-manager] Failed to load workspaces:", err));
    }, []);

    // Fetch dynamic branch listing when selectedWorkspace changes
    React.useEffect(() => {
      if (selectedWorkspace) {
        const activeWorkspaceObj = workspaces.find(w => w.id === selectedWorkspace);
        if (activeWorkspaceObj && activeWorkspaceObj.path) {
          api(`/api/dashboard/git/branches?path=${encodeURIComponent(activeWorkspaceObj.path)}`)
            .then(data => {
              if (data && data.branches && data.branches.length > 0) {
                setGitBranches(data.branches);
                if (!useCustomBranch) {
                  setModalBranch(data.branches[0]);
                }
              } else {
                setGitBranches([]);
              }
            })
            .catch(err => console.error("Failed to load git branches:", err));
        }
      }
    }, [selectedWorkspace, workspaces, useCustomBranch]);

    // Fetch Linear issues when modal opens
    React.useEffect(() => {
      if (showModal) {
        api('/api/dashboard/linear/issues')
          .then(data => {
            if (data && data.issues) {
              setLinearIssues(data.issues);
            }
          })
          .catch(err => console.error("Failed to load Linear issues:", err));
      }
    }, [showModal]);

    // Helper to resolve workspace of a session
    const getWorkspaceForSession = React.useCallback((session) => {
      if (!session) return 'agentic-swarm-ops';
      if (session.workspace_id) return session.workspace_id;
      
      const searchStr = `${session.title || ''} ${session.id || ''} ${session.preview || ''}`.toLowerCase();
      for (const w of workspaces) {
        if (searchStr.includes(w.id.toLowerCase()) || searchStr.includes(w.name.toLowerCase())) {
          return w.id;
        }
      }
      return workspaces.length > 0 ? workspaces[0].id : 'agentic-swarm-ops';
    }, [workspaces]);

    // Filter sessions based on search query and selected workspace
    const filteredSessions = React.useMemo(() => {
      const activeWorkspaceObj = workspaces.find(w => w.id === selectedWorkspace);
      const activeWorkspacePath = activeWorkspaceObj ? activeWorkspaceObj.path : '';

      return sessions.filter(s => {
        // Workspace filtering: match by session cwd. Fallback to matching workspace ID.
        if (activeWorkspacePath) {
          if (s.cwd) {
            if (s.cwd !== activeWorkspacePath) return false;
          } else {
            const sessionWorkspaceId = s.workspace_id || getWorkspaceForSession(s);
            if (sessionWorkspaceId !== selectedWorkspace) return false;
          }
        }

        if (!searchQuery) return true;
        const query = searchQuery.toLowerCase();
        return (
          (s.title || '').toLowerCase().includes(query) ||
          (s.id || '').toLowerCase().includes(query) ||
          (s.profile || '').toLowerCase().includes(query) ||
          (s.branch || '').toLowerCase().includes(query) ||
          (s.linear_issue || '').toLowerCase().includes(query)
        );
      });
    }, [sessions, searchQuery, selectedWorkspace, workspaces, getWorkspaceForSession]);

    // Group sessions by Git branch
    const sessionsByBranch = React.useMemo(() => {
      const groups = {};
      
      // Initialize groups with all known branches for the active workspace
      gitBranches.forEach(b => {
        groups[b] = [];
      });
      
      // Always ensure "main" exists
      if (!groups['main']) {
        groups['main'] = [];
      }
      groups['unassigned'] = [];

      filteredSessions.forEach(s => {
        const sBranch = s.branch || 'main';
        if (groups[sBranch]) {
          groups[sBranch].push(s);
        } else {
          // If the session's branch is not in the active gitBranches list, dynamically create a folder for it so sessions aren't hidden
          if (!groups[sBranch]) {
            groups[sBranch] = [];
          }
          groups[sBranch].push(s);
        }
      });
      return groups;
    }, [filteredSessions, gitBranches]);

    // Fetch sessions list on mount and periodically
    const fetchSessions = React.useCallback(() => {
      api('/api/sessions?limit=30')
        .then(data => {
          if (data && data.sessions) {
            setSessions(data.sessions);
            // Default to the first session if none selected
            if (data.sessions.length > 0 && !activeSessionId) {
              setActiveSessionId(data.sessions[0].id);
            }
          }
        })
        .catch(err => console.error("Failed to load sessions:", err));
    }, [activeSessionId]);

    React.useEffect(() => {
      fetchSessions();
      const interval = setInterval(fetchSessions, 6000);
      return () => clearInterval(interval);
    }, [fetchSessions]);

    // Fetch active session messages
    React.useEffect(() => {
      if (!activeSessionId) return;
      
      const loadMessages = () => {
        api(`/api/sessions/${activeSessionId}/messages`)
          .then(data => {
            if (data && data.messages) {
              setMessages(data.messages);
            }
          })
          .catch(err => console.error("Failed to load messages:", err));
      };

      loadMessages();
      const msgInterval = setInterval(loadMessages, 3000);
      return () => clearInterval(msgInterval);
    }, [activeSessionId]);

    // Setup xterm.js terminal connection
    React.useEffect(() => {
      console.log("[PTY Debug] Setup run - activeSessionId:", activeSessionId, "terminalRef.current:", !!terminalRef.current);
      if (!activeSessionId || !terminalRef.current) return;

      // Clean up previous connection
      if (termInstance.current) {
        console.log("[PTY Debug] Disposing old termInstance");
        termInstance.current.dispose();
      }
      if (socketRef.current) {
        console.log("[PTY Debug] Closing old socketRef");
        socketRef.current.close();
      }

      // Inject styling and keyframes
      const styleId = 'xterm-dynamic-style';
      if (!document.getElementById(styleId)) {
        const style = document.createElement('style');
        style.id = styleId;
        style.innerHTML = `
          .xterm { font-family: 'Space Mono', monospace; font-size: 13px; line-height: 1.4; color: #fff; background-color: #0c0c12; padding: 12px; border-radius: 8px; }
          .xterm-viewport { overflow-y: auto; background-color: #0c0c12; }
          @keyframes pulse-glow {
            0% { box-shadow: 0 0 0 0 rgba(78, 205, 196, 0.7); }
            70% { box-shadow: 0 0 0 6px rgba(78, 205, 196, 0); }
            100% { box-shadow: 0 0 0 0 rgba(78, 205, 196, 0); }
          }
          @keyframes border-glow {
            0%, 100% { border-color: rgba(108, 92, 231, 0.2); }
            50% { border-color: rgba(108, 92, 231, 0.6); }
          }
          .pulse-active { animation: pulse-glow 2s infinite; }
          .glowing-border { animation: border-glow 4s infinite; }
        `;
        document.head.appendChild(style);
      }

      // Initialize xterm
      const term = new Terminal({
        theme: {
          background: '#0c0c12',
          foreground: '#f8f8f2',
          cursor: '#4ecdc4'
        },
        cursorBlink: true,
        cols: 80,
        rows: 24,
        scrollback: 10000,
        fontSize: terminalFontSize,
        fontFamily: "'Space Mono', monospace"
      });
      
      term.open(terminalRef.current);
      termInstance.current = term;

      // Extract active session attributes from refs to avoid re-triggering connection on polling
      const activeSessionObj = sessionsRef.current.find(s => s.id === activeSessionId) || {};
      const activeWorkspaceObj = workspacesRef.current.find(w => w.id === selectedWorkspaceRef.current) || {};

      // Connect to Durable PTY WebSocket
      const token = window.__HERMES_SESSION_TOKEN__ || '';
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      
      const profileVal = activeSessionObj.profile || '';
      const branchVal = activeSessionObj.branch || '';
      const issueVal = activeSessionObj.linear_issue || '';
      const cwdVal = activeSessionObj.cwd || activeWorkspaceObj.path || '';

      const queryParams = `?token=${token}&resume=${activeSessionId}&profile=${encodeURIComponent(profileVal)}&cwd=${encodeURIComponent(cwdVal)}&branch=${encodeURIComponent(branchVal)}&linear_issue=${encodeURIComponent(issueVal)}`;
      const wsUrl = `${protocol}//${window.location.host}/api/pty${queryParams}`;
      const ws = new WebSocket(wsUrl);
      socketRef.current = ws;

      let pingInterval = null;

      ws.onopen = () => {
        setWsConnected(true);
        ws.send(`\x1b[RESIZE:80;24]`);

        // Periodic heartbeat keepalive (30s)
        pingInterval = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send('\x00');
          }
        }, 30000);
      };

      ws.onmessage = (event) => {
        if (event.data instanceof Blob) {
          const reader = new FileReader();
          reader.onload = () => {
            term.write(new Uint8Array(reader.result));
          };
          reader.readAsArrayBuffer(event.data);
        } else {
          term.write(event.data);
        }
      };

      ws.onclose = () => {
        setWsConnected(false);
        if (pingInterval) clearInterval(pingInterval);
      };

      term.onData((data) => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(data);
        }
      });

      return () => {
        console.log("[PTY Debug] Cleanup run - activeSessionId:", activeSessionId);
        term.dispose();
        ws.close();
        if (pingInterval) clearInterval(pingInterval);
      };
    }, [activeSessionId]);

    // Update xterm font size dynamically when terminalFontSize state changes
    React.useEffect(() => {
      if (termInstance.current) {
        termInstance.current.options.set('fontSize', terminalFontSize);
      }
    }, [terminalFontSize]);

    // Handle input field changes & Autocomplete
    function handleInputChange(e) {
      const val = e.target.value;
      setComposerText(val);

      if (val.startsWith('/')) {
        const matches = SLASH_COMMANDS.filter(item => item.cmd.startsWith(val));
        setSuggestedCommands(matches);
        setShowSuggestions(matches.length > 0);
      } else {
        setShowSuggestions(false);
      }
    }

    function selectSuggestion(cmd) {
      setComposerText(cmd);
      setShowSuggestions(false);
    }

    // Modal submit handler
    function handleCreateSession(e) {
      if (e) e.preventDefault();
      const newId = 'session-' + Math.random().toString(36).substring(2, 15);
      const title = modalTitle.trim() || `Session [${modalProfile}]`;
      const activeWorkspaceObj = workspaces.find(w => w.id === selectedWorkspace) || {};
      
      const newSessionObj = {
        id: newId,
        title: title,
        is_active: true,
        workspace_id: selectedWorkspace,
        profile: modalProfile,
        branch: modalBranch,
        linear_issue: modalIssue,
        cwd: activeWorkspaceObj.path || '',
        model: modalProfile === 'jules' ? 'review-agent' : modalProfile === 'agy' ? 'research-agent' : 'gpt-5.5',
        message_count: 0,
        estimated_cost_usd: 0
      };

      setSessions(prev => [newSessionObj, ...prev]);
      setActiveSessionId(newId);
      setMessages([]);
      setShowModal(false);

      // Reset fields
      setModalTitle('');
      setModalProfile('orchestrator');
      setModalBranch('main');
      setModalIssue('');
      setUseCustomBranch(false);
      setUseCustomIssue(false);
    }

    function sendMessage(e) {
      if (e) e.preventDefault();
      const text = composerText.trim();
      if (!text) return;

      if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
        socketRef.current.send(text + '\r');
      }
      setComposerText('');
      setShowSuggestions(false);
    }

    function sendCommandDirectly(cmdText) {
      if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
        socketRef.current.send(cmdText + '\r');
      }
    }

    // Compute observability statistics dynamically
    const stats = React.useMemo(() => {
      let totalCost = 0;
      let totalMsgs = 0;
      const profileCosts = { jules: 0, agy: 0, codex: 0, orchestrator: 0, other: 0 };
      
      sessions.forEach(s => {
        let cost = parseFloat(s.estimated_cost_usd);
        if (isNaN(cost)) {
          cost = 0;
        }
        totalCost += cost;
        totalMsgs += (s.message_count || 0);
        
        const prof = (s.profile || 'orchestrator').toLowerCase();
        if (profileCosts[prof] !== undefined) {
          profileCosts[prof] += cost;
        } else {
          profileCosts['other'] += cost;
        }
      });
      
      return { totalCost, totalMsgs, profileCosts };
    }, [sessions]);

    const activeSession = sessions.find(s => s.id === activeSessionId) || {};
    const hasActiveWorkspaceSessions = (workspaceId) => {
      return sessions.some(s => getWorkspaceForSession(s) === workspaceId && s.is_active);
    };

    const isMobile = windowWidth < 768;

    return h('div', {
      style: {
        display: 'grid',
        gridTemplateColumns: isMobile ? '1fr' : (sidebarCollapsed ? '68px 1fr' : '260px 1fr'),
        height: 'calc(100vh - 64px)',
        fontFamily: '"Outfit", "Inter", sans-serif',
        color: '#e2e8f0',
        backgroundColor: '#050508',
        overflow: 'hidden',
        position: 'relative',
        transition: 'grid-template-columns 0.3s cubic-bezier(0.4, 0, 0.2, 1)'
      }
    },
      // Backdrop blur overlay for mobile sidebar drawer
      isMobile && !sidebarCollapsed && h('div', {
        onClick: () => setSidebarCollapsed(true),
        style: {
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          backgroundColor: 'rgba(0, 0, 0, 0.5)',
          backdropFilter: 'blur(4px)',
          zIndex: 999
        }
      }),

      // Left Sidebar: Workspaces, Sessions & Swarm Metrics
      h('div', {
        style: {
          backgroundColor: '#09090f',
          borderRight: '1px solid rgba(255,255,255,0.05)',
          padding: sidebarCollapsed ? (isMobile ? '0px' : '16px 8px') : '16px',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden', // Lock height, only the tree list scrolls
          alignItems: (sidebarCollapsed && !isMobile) ? 'center' : 'stretch',
          // Mobile responsive absolute overlay sidebar drawer styles
          position: isMobile ? 'absolute' : 'relative',
          top: isMobile ? 0 : 'auto',
          bottom: isMobile ? 0 : 'auto',
          left: isMobile ? 0 : 'auto',
          width: isMobile ? '270px' : 'auto',
          height: isMobile ? '100%' : 'auto',
          zIndex: isMobile ? 1000 : 'auto',
          boxShadow: isMobile && !sidebarCollapsed ? '5px 0 25px rgba(0,0,0,0.8)' : 'none',
          transition: 'transform 0.3s ease, padding 0.3s ease',
          transform: isMobile && sidebarCollapsed ? 'translateX(-270px)' : 'translateX(0)',
          visibility: isMobile && sidebarCollapsed ? 'hidden' : 'visible'
        }
      },
        // Sidebar Header
        h('div', {
          style: {
            display: 'flex',
            justifyContent: (sidebarCollapsed && !isMobile) ? 'center' : 'space-between',
            alignItems: 'center',
            marginBottom: 20
          }
        },
          (!sidebarCollapsed || isMobile) && h('h2', { style: { fontSize: 13, textTransform: 'uppercase', color: '#a29bfe', margin: 0, fontWeight: 'bold', letterSpacing: '0.05em' } }, '⧉ Swarm Controller'),
          h('button', {
            type: 'button',
            onClick: () => setSidebarCollapsed(!sidebarCollapsed),
            style: {
              background: 'rgba(255,255,255,0.03)',
              border: '1px solid rgba(255,255,255,0.08)',
              borderRadius: 6,
              color: '#fff',
              cursor: 'pointer',
              padding: '6px 10px',
              fontSize: 14
            }
          }, '☰')
        ),

        // Workspace Selector Dropdown
        !sidebarCollapsed && h('div', { style: { marginBottom: 16, width: '100%', padding: '0 4px' } },
          h('label', { style: { display: 'block', fontSize: 10, textTransform: 'uppercase', opacity: 0.5, marginBottom: 6, letterSpacing: '0.05em' } }, 'Active Workspace'),
          h('select', {
            value: selectedWorkspace,
            onChange: (e) => setSelectedWorkspace(e.target.value),
            style: {
              width: '100%',
              padding: '10px 12px',
              borderRadius: 8,
              border: '1px solid rgba(255, 255, 255, 0.08)',
              backgroundColor: '#0c0c12',
              color: '#fff',
              fontSize: 13,
              outline: 'none',
              cursor: 'pointer',
              fontWeight: 'bold',
              transition: 'border-color 0.2s'
            }
          },
            workspaces.map(w => h('option', { key: w.id, value: w.id }, `${w.icon} ${w.name}`))
          )
        ),

        // Global Search Bar
        !sidebarCollapsed && h('div', { style: { marginBottom: 16, padding: '0 4px', position: 'relative' } },
          h('input', {
            value: searchQuery,
            onChange: (e) => setSearchQuery(e.target.value),
            placeholder: 'Search sessions...',
            style: {
              width: '100%',
              padding: '8px 12px 8px 32px',
              borderRadius: 8,
              border: '1px solid rgba(255, 255, 255, 0.08)',
              backgroundColor: 'rgba(0, 0, 0, 0.3)',
              color: '#fff',
              fontSize: 12,
              outline: 'none',
              transition: 'border-color 0.2s'
            }
          }),
          h('span', { style: { position: 'absolute', left: 14, top: 8, opacity: 0.4, fontSize: 12 } }, '🔍')
        ),

        // Collapsible Git Branch Session Tree
        h('div', {
          className: 'custom-scrollbar',
          style: {
            flex: 1,
            overflowY: 'auto',
            width: '100%',
            display: 'flex',
            flexDirection: 'column',
            gap: 10
          }
        },
          gitBranches.map(branchName => {
            const isExpanded = !!expandedBranches[branchName];
            const childSessions = sessionsByBranch[branchName] || [];
            const activeSessionCount = childSessions.filter(s => s.is_active).length;

            return h('div', {
              key: branchName,
              style: {
                display: 'flex',
                flexDirection: 'column',
                borderRadius: 8,
                border: '1px solid rgba(255, 255, 255, 0.03)',
                backgroundColor: 'rgba(255, 255, 255, 0.01)',
                padding: '2px',
                transition: 'all 0.3s'
              }
            },
              // Branch Folder Header Row
              h('div', {
                className: 'premium-folder',
                style: {
                  padding: '8px 10px',
                  borderRadius: 6,
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  userSelect: 'none'
                },
                onClick: () => toggleBranch(branchName)
              },
                // Left side: Arrow + Icon + Title
                h('div', { style: { display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 } },
                  h('span', { style: { fontSize: 10, opacity: 0.5, transform: isExpanded ? 'rotate(90deg)' : 'none', display: 'inline-block', transition: 'transform 0.2s', marginRight: 2 } }, '▶'),
                  h('span', { style: { fontSize: 14 } }, '🌿'),
                  !sidebarCollapsed && h('span', { style: { fontSize: 12, fontWeight: '600', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', color: '#e2e8f0' } }, branchName)
                ),
                // Right side: Active count / Spawner
                !sidebarCollapsed && h('div', { style: { display: 'flex', alignItems: 'center', gap: 6 } },
                  // Active session count dot
                  activeSessionCount > 0 && h('span', {
                    className: 'pulse-active',
                    style: {
                      padding: '2px 6px',
                      fontSize: 9,
                      fontWeight: 'bold',
                      borderRadius: 10,
                      backgroundColor: '#4ecdc4',
                      color: '#0c0c12'
                    }
                  }, activeSessionCount),
                  
                  // Quick Session Spawner '+'
                  h('button', {
                    onClick: (e) => {
                      e.stopPropagation();
                      setModalBranch(branchName);
                      setUseCustomBranch(true);
                      setModalProfile('orchestrator');
                      setShowModal(true);
                    },
                    title: `Spawn session on branch ${branchName}`,
                    style: {
                      background: 'transparent',
                      border: 'none',
                      color: '#4ecdc4',
                      cursor: 'pointer',
                      fontSize: 14,
                      fontWeight: 'bold',
                      padding: '0 4px'
                    }
                  }, '+')
                )
              ),

              // Expanded sessions list
              isExpanded && h('div', {
                style: {
                  padding: '4px 6px 6px 20px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 4
                }
              },
                childSessions.length === 0 ? 
                  h('div', { style: { fontSize: 10, opacity: 0.3, padding: '4px 0' } }, 'No sessions') :
                  childSessions.map(s => {
                    const isActive = activeSessionId === s.id;
                    const isModelRunning = s.is_active;
                    const isEditing = editingSessionId === s.id;

                    return h('div', {
                      key: s.id,
                      className: 'premium-card',
                      onClick: () => setActiveSessionId(s.id),
                      style: {
                        padding: '6px 8px',
                        borderRadius: 6,
                        cursor: 'pointer',
                        backgroundColor: isActive ? 'rgba(255,255,255,0.04)' : 'transparent',
                        border: `1px solid ${isActive ? 'rgba(255,255,255,0.1)' : 'transparent'}`,
                        display: 'flex',
                        flexDirection: 'column',
                        gap: 3,
                        position: 'relative'
                      }
                    },
                      // First row: Title & active indicator
                      h('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 6 } },
                        isEditing ?
                          h('input', {
                            value: editingSessionTitle,
                            onClick: (e) => e.stopPropagation(),
                            onChange: (e) => setEditingSessionTitle(e.target.value),
                            onKeyDown: (e) => {
                              if (e.key === 'Enter') {
                                e.stopPropagation();
                                saveEditingSession(s.id);
                              } else if (e.key === 'Escape') {
                                e.stopPropagation();
                                setEditingSessionId(null);
                              }
                            },
                            onBlur: () => saveEditingSession(s.id),
                            autoFocus: true,
                            style: {
                              width: '100%',
                              fontSize: 11,
                              background: '#040406',
                              border: '1px solid #6c5ce7',
                              borderRadius: 4,
                              color: '#fff',
                              padding: '1px 4px',
                              outline: 'none'
                            }
                          }) :
                          h('span', {
                            style: {
                              fontSize: 11,
                              fontWeight: isActive ? 'bold' : 'normal',
                              color: isActive ? '#fff' : '#b2bec3',
                              whiteSpace: 'nowrap',
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              maxWidth: '85%'
                            }
                          }, s.title || s.id),
                        
                        h('div', { style: { display: 'flex', alignItems: 'center', gap: 4 } },
                          h('span', {
                            style: {
                              width: 6,
                              height: 6,
                              borderRadius: '50%',
                              backgroundColor: isModelRunning ? '#4ecdc4' : 'rgba(255,255,255,0.15)'
                            }
                          })
                        )
                      ),
                      
                      // Second row: Metadata & Action buttons
                      !sidebarCollapsed && h('div', {
                        style: {
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center',
                          fontSize: 9,
                          opacity: 0.5,
                          marginTop: 2
                        }
                      },
                        h('span', null, s.profile ? `👤 ${s.profile}` : '👤 repl'),
                        h('div', {
                          onClick: (e) => e.stopPropagation(),
                          style: {
                            display: 'flex',
                            gap: 6
                          }
                        },
                          h('button', {
                            onClick: () => startEditingSession(s.id, s.title || s.id),
                            className: 'action-btn-hover',
                            title: 'Rename Session',
                            style: { background: 'transparent', border: 'none', color: '#ffd32a', cursor: 'pointer', padding: 0 }
                          }, '✏️'),
                          h('button', {
                            onClick: () => duplicateSession(s),
                            className: 'action-btn-hover',
                            title: 'Duplicate Session',
                            style: { background: 'transparent', border: 'none', color: '#a29bfe', cursor: 'pointer', padding: 0 }
                          }, '👯'),
                          h('button', {
                            onClick: () => deleteSession(s.id),
                            className: 'action-btn-hover',
                            title: 'Delete Session',
                            style: { background: 'transparent', border: 'none', color: '#ff6b6b', cursor: 'pointer', padding: 0 }
                          }, '🗑️')
                        )
                      )
                    );
                  })
              )
            );
          }),

          // Global/Unassigned Sessions Folder
          (sessionsByBranch['unassigned'] && sessionsByBranch['unassigned'].length > 0) && h('div', {
            style: {
              display: 'flex',
              flexDirection: 'column',
              borderRadius: 8,
              border: '1px solid rgba(255, 255, 255, 0.03)',
              backgroundColor: 'rgba(255, 255, 255, 0.01)',
              padding: '2px'
            }
          },
            // Header Row
            h('div', {
              className: 'premium-folder',
              style: {
                padding: '8px 10px',
                borderRadius: 6,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                userSelect: 'none'
              },
              onClick: () => toggleBranch('unassigned')
            },
              h('div', { style: { display: 'flex', alignItems: 'center', gap: 6 } },
                h('span', { style: { fontSize: 10, opacity: 0.5, transform: expandedBranches['unassigned'] ? 'rotate(90deg)' : 'none', display: 'inline-block', transition: 'transform 0.2s', marginRight: 2 } }, '▶'),
                h('span', { style: { fontSize: 16 } }, '🌐'),
                !sidebarCollapsed && h('span', { style: { fontSize: 12, fontWeight: '600', color: '#e2e8f0' } }, 'Global / Unassigned')
              ),
              !sidebarCollapsed && h('span', {
                style: {
                  padding: '2px 6px',
                  fontSize: 9,
                  borderRadius: 10,
                  backgroundColor: 'rgba(255,255,255,0.1)',
                  color: '#fff'
                }
              }, sessionsByBranch['unassigned'].length)
            ),

            // Sessions list
            expandedBranches['unassigned'] && h('div', {
              style: {
                padding: '4px 6px 6px 20px',
                display: 'flex',
                flexDirection: 'column',
                gap: 4
              }
            },
              sessionsByBranch['unassigned'].map(s => {
                const isActive = activeSessionId === s.id;
                const isModelRunning = s.is_active;
                const isEditing = editingSessionId === s.id;

                return h('div', {
                  key: s.id,
                  className: 'premium-card',
                  onClick: () => setActiveSessionId(s.id),
                  style: {
                    padding: '6px 8px',
                    borderRadius: 6,
                    cursor: 'pointer',
                    backgroundColor: isActive ? 'rgba(255,255,255,0.04)' : 'transparent',
                    border: `1px solid ${isActive ? 'rgba(255,255,255,0.1)' : 'transparent'}`,
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 3,
                    position: 'relative'
                  }
                },
                  h('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 6 } },
                    isEditing ?
                      h('input', {
                        value: editingSessionTitle,
                        onClick: (e) => e.stopPropagation(),
                        onChange: (e) => setEditingSessionTitle(e.target.value),
                        onKeyDown: (e) => {
                          if (e.key === 'Enter') {
                            e.stopPropagation();
                            saveEditingSession(s.id);
                          } else if (e.key === 'Escape') {
                            e.stopPropagation();
                            setEditingSessionId(null);
                          }
                        },
                        onBlur: () => saveEditingSession(s.id),
                        autoFocus: true,
                        style: {
                          width: '100%',
                          fontSize: 11,
                          background: '#040406',
                          border: '1px solid #6c5ce7',
                          borderRadius: 4,
                          color: '#fff',
                          padding: '1px 4px',
                          outline: 'none'
                        }
                      }) :
                      h('span', { style: { fontSize: 11, color: isActive ? '#fff' : '#b2bec3', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '85%' } }, s.title || s.id),
                    h('span', { style: { width: 6, height: 6, borderRadius: '50%', backgroundColor: isModelRunning ? '#4ecdc4' : 'rgba(255,255,255,0.15)' } })
                  ),
                  !sidebarCollapsed && h('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 9, opacity: 0.5, marginTop: 2 } },
                    h('span', null, s.profile ? `👤 ${s.profile}` : '👤 repl'),
                    h('div', {
                      onClick: (e) => e.stopPropagation(),
                      style: { display: 'flex', gap: 6 }
                    },
                      h('button', {
                        onClick: () => startEditingSession(s.id, s.title || s.id),
                        className: 'action-btn-hover',
                        title: 'Rename',
                        style: { background: 'transparent', border: 'none', color: '#ffd32a', cursor: 'pointer', padding: 0 }
                      }, '✏️'),
                      h('button', {
                        onClick: () => duplicateSession(s),
                        className: 'action-btn-hover',
                        title: 'Duplicate',
                        style: { background: 'transparent', border: 'none', color: '#a29bfe', cursor: 'pointer', padding: 0 }
                      }, '👯'),
                      h('button', {
                        onClick: () => deleteSession(s.id),
                        className: 'action-btn-hover',
                        title: 'Delete',
                        style: { background: 'transparent', border: 'none', color: '#ff6b6b', cursor: 'pointer', padding: 0 }
                      }, '🗑️')
                    )
                  )
                );
              })
            )
          )
        ),

        // Swarm Observability Analytics Panel
        !sidebarCollapsed && h('div', {
          style: {
            marginTop: 20,
            padding: '12px',
            borderRadius: 8,
            backgroundColor: 'rgba(255,255,255,0.01)',
            border: '1px solid rgba(255,255,255,0.04)',
            fontSize: 12
          }
        },
          h('h4', { style: { margin: '0 0 10px 0', fontSize: 10, textTransform: 'uppercase', opacity: 0.5, letterSpacing: '0.05em', color: '#ffd32a' } }, '📊 Swarm Analytics'),
          h('div', { style: { display: 'flex', justifyContent: 'space-between', marginBottom: 6 } },
            h('span', { style: { opacity: 0.7 } }, 'Total Sessions:'),
            h('strong', null, sessions.length)
          ),
          h('div', { style: { display: 'flex', justifyContent: 'space-between', marginBottom: 6 } },
            h('span', { style: { opacity: 0.7 } }, 'Total Messages:'),
            h('strong', null, stats.totalMsgs)
          ),
          h('div', { style: { display: 'flex', justifyContent: 'space-between', marginBottom: 12 } },
            h('span', { style: { opacity: 0.7 } }, 'Total Cost:'),
            h('strong', { style: { color: '#ffd32a' } }, `$${stats.totalCost.toFixed(4)}`)
          ),
          h('div', { style: { fontSize: 10, opacity: 0.5, marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.05em' } }, 'Cost Breakdown'),
          Object.entries(stats.profileCosts).map(([prof, cost]) => {
            const maxCost = Math.max(...Object.values(stats.profileCosts), 0.0001);
            const pct = (cost / maxCost) * 100;
            const barColors = {
              jules: '#4ecdc4',
              agy: '#ffd32a',
              codex: '#a29bfe',
              orchestrator: '#5f27cd',
              other: '#888'
            };
            return h('div', { key: prof, style: { marginBottom: 6 } },
              h('div', { style: { display: 'flex', justifyContent: 'space-between', fontSize: 10, opacity: 0.8, marginBottom: 2 } },
                h('span', null, prof),
                h('span', null, `$${cost.toFixed(4)}`)
              ),
              h('div', { style: { height: 4, width: '100%', backgroundColor: 'rgba(255,255,255,0.05)', borderRadius: 2, overflow: 'hidden' } },
                h('div', { style: { height: '100%', width: `${pct}%`, backgroundColor: barColors[prof] || '#5f27cd', borderRadius: 2 } })
              )
            );
          })
        )
      ),

      h('div', {
        style: {
          display: 'grid',
          gridTemplateColumns: terminalMaximized ? '1fr' : (windowWidth < 1024 ? '1fr' : '1fr 1fr'),
          gridTemplateRows: terminalMaximized ? '1fr' : (windowWidth < 1024 ? '1fr 1fr' : '1fr'),
          height: '100%',
          overflow: 'hidden'
        }
      },
        // Central Panel: Multiplexed Chat Area
        !terminalMaximized && h('div', {
          key: 'chat-panel',
          style: {
            display: 'flex',
            flexDirection: 'column',
            borderRight: windowWidth < 1024 ? 'none' : '1px solid rgba(255,255,255,0.05)',
            borderBottom: windowWidth < 1024 ? '1px solid rgba(255,255,255,0.05)' : 'none',
            backgroundColor: '#07070b',
            position: 'relative',
            height: '100%',
            maxHeight: '100%',
            minHeight: 0,
            overflow: 'hidden'
          }
        },
          // Active Session Header
          h('div', {
            style: {
              padding: '16px 20px',
              borderBottom: '1px solid rgba(255,255,255,0.05)',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              backgroundColor: '#09090f'
            }
          },
            h('div', { style: { display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 } },
              isMobile && h('button', {
                type: 'button',
                onClick: (e) => {
                  e.stopPropagation();
                  setSidebarCollapsed(false);
                },
                style: {
                  background: 'rgba(255,255,255,0.03)',
                  border: '1px solid rgba(255,255,255,0.08)',
                  borderRadius: 6,
                  color: '#fff',
                  cursor: 'pointer',
                  padding: '6px 10px',
                  fontSize: 14
                }
              }, '☰'),
              h('div', { style: { minWidth: 0 } },
                h('h2', { style: { margin: 0, fontSize: 15, color: '#fff', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' } }, activeSession.title || 'Swarm Workspace Chat'),
                h('p', { style: { margin: '2px 0 0', fontSize: 10, opacity: 0.4, fontFamily: 'monospace' } }, `ID: ${activeSessionId || 'None'}`)
              )
            ),
            h('span', {
              className: wsConnected ? 'pulse-active' : '',
              style: {
                padding: '4px 10px',
                borderRadius: 12,
                fontSize: 10,
                fontWeight: 'bold',
                backgroundColor: wsConnected ? 'rgba(78, 205, 196, 0.12)' : 'rgba(255, 107, 107, 0.12)',
                color: wsConnected ? '#4ecdc4' : '#ff6b6b',
                border: `1px solid ${wsConnected ? 'rgba(78, 205, 196, 0.2)' : 'rgba(255, 107, 107, 0.2)'}`
              }
            }, wsConnected ? 'Connected' : 'Offline')
          ),

          // Active session parameters banner
          activeSessionId && h('div', {
            style: {
              padding: '8px 20px',
              backgroundColor: 'rgba(108, 92, 231, 0.05)',
              borderBottom: '1px solid rgba(108, 92, 231, 0.1)',
              display: 'flex',
              gap: 16,
              fontSize: 11,
              opacity: 0.7,
              flexWrap: 'wrap'
            }
          },
            h('span', null, `🤖 ${activeSession.profile || 'orchestrator'}`),
            activeSession.branch && h('span', null, `🌿 ${activeSession.branch}`),
            activeSession.linear_issue && h('span', null, `🎫 ${activeSession.linear_issue}`),
            activeSession.model && h('span', null, `🧠 ${activeSession.model}`),
            activeSession.estimated_cost_usd !== undefined && activeSession.estimated_cost_usd !== null && !isNaN(parseFloat(activeSession.estimated_cost_usd)) && h('span', { style: { color: '#ffd32a' } }, `💰 $${parseFloat(activeSession.estimated_cost_usd).toFixed(4)}`)
          ),

          // Chat messages log
          h('div', { style: { flex: 1, padding: 20, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 16, minHeight: 0, textTransform: 'none' } },
            messages.length === 0 ? h('div', { style: { display: 'flex', height: '100%', alignItems: 'center', justifyContent: 'center', opacity: 0.2, fontSize: 13, textTransform: 'none' } }, 'No messaging history in this stream.') :
              messages.map((m, idx) =>
                h('div', {
                  key: idx,
                  style: {
                    alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start',
                    maxWidth: '85%',
                    padding: '10px 14px',
                    borderRadius: 12,
                    backgroundColor: m.role === 'user' ? '#5f27cd' : 'rgba(255,255,255,0.02)',
                    border: `1px solid ${m.role === 'user' ? '#5f27cd' : 'rgba(255,255,255,0.05)'}`,
                    boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
                    textTransform: 'none'
                  }
                },
                  h('div', { style: { fontSize: 9, opacity: 0.5, marginBottom: 4, textTransform: 'none', fontWeight: 'bold', color: m.role === 'user' ? '#a29bfe' : '#4ecdc4' } }, m.role),
                  h('div', { style: { fontSize: 13, lineHeight: 1.5, whiteSpace: 'pre-wrap', textTransform: 'none' } }, m.content)
                )
              )
          ),

          // Autocomplete suggestions box
          showSuggestions && h('div', {
            style: {
              position: 'absolute',
              bottom: 120,
              left: 20,
              right: 20,
              backgroundColor: '#0c0c12',
              border: '1px solid rgba(108, 92, 231, 0.2)',
              borderRadius: 8,
              overflow: 'hidden',
              boxShadow: '0 8px 30px rgba(0,0,0,0.6)',
              zIndex: 100
            }
          },
            suggestedCommands.map(item =>
              h('div', {
                key: item.cmd,
                onClick: () => selectSuggestion(item.cmd),
                style: {
                  padding: '10px 14px',
                  cursor: 'pointer',
                  fontSize: 12,
                  borderBottom: '1px solid rgba(255,255,255,0.03)',
                  display: 'flex',
                  justifyContent: 'space-between'
                }
              },
                h('strong', { style: { color: '#ffd32a' } }, item.cmd),
                h('span', { style: { opacity: 0.5 } }, item.desc)
              )
            )
          ),

          // Interactive Dispatch Toolbar
          activeSessionId && h('div', {
            style: {
              padding: '6px 16px',
              backgroundColor: 'rgba(255,255,255,0.01)',
              borderTop: '1px solid rgba(255,255,255,0.03)',
              display: 'flex',
              gap: 8,
              overflowX: 'auto',
              whiteSpace: 'nowrap'
            }
          },
            h('button', {
              onClick: () => sendCommandDirectly('git status'),
              style: { padding: '4px 8px', fontSize: 10, background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 4, color: '#b2bec3', cursor: 'pointer' }
            }, '🌿 git status'),
            h('button', {
              onClick: () => sendCommandDirectly('git add . && git commit -m "Auto sync from Swarm Manager" && git push'),
              style: { padding: '4px 8px', fontSize: 10, background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 4, color: '#4ecdc4', cursor: 'pointer' }
            }, '🚀 git push sync'),
            h('button', {
              onClick: () => sendCommandDirectly('jules run --status'),
              style: { padding: '4px 8px', fontSize: 10, background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 4, color: '#a29bfe', cursor: 'pointer' }
            }, '🤖 jules status'),
            h('button', {
              onClick: () => sendCommandDirectly('hermes skills --summary list'),
              style: { padding: '4px 8px', fontSize: 10, background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 4, color: '#ffd32a', cursor: 'pointer' }
            }, '🛠️ hermes skills')
          ),

          // Chat Input Form
          h('form', { onSubmit: sendMessage, style: { padding: 16, borderTop: '1px solid rgba(255,255,255,0.04)', backgroundColor: '#09090f' } },
            h('input', {
              value: composerText,
              onChange: handleInputChange,
              placeholder: 'Type message or use / to trigger slash commands...',
              style: {
                width: '100%',
                padding: '12px 16px',
                borderRadius: 8,
                border: '1px solid rgba(108, 92, 231, 0.15)',
                backgroundColor: '#040406',
                color: '#fff',
                outline: 'none',
                fontSize: 13
              }
            })
          )
        ),

        h('div', {
          key: 'terminal-panel',
          style: {
            display: 'flex',
            flexDirection: 'column',
            backgroundColor: '#0c0c12',
            height: '100%',
            maxHeight: '100%',
            minHeight: 0,
            overflow: 'hidden'
          }
        },
          h('div', {
            style: {
              padding: '12px 20px',
              borderBottom: '1px solid rgba(255,255,255,0.05)',
              backgroundColor: '#09090f',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              flexWrap: 'wrap',
              gap: 8
            }
          },
            // Left: Header Title
            h('div', { style: { display: 'flex', alignItems: 'center', gap: 8 } },
              isMobile && h('button', {
                type: 'button',
                onClick: (e) => {
                  e.stopPropagation();
                  setSidebarCollapsed(false);
                },
                style: {
                  background: 'rgba(255,255,255,0.03)',
                  border: '1px solid rgba(255,255,255,0.08)',
                  borderRadius: 6,
                  color: '#fff',
                  cursor: 'pointer',
                  padding: '6px 10px',
                  fontSize: 14
                }
              }, '☰'),
              h('h2', { style: { margin: 0, fontSize: 13, color: '#fff', fontWeight: 'bold' } }, 'Durable PTY Console'),
              h('span', { style: { fontSize: 10, color: '#4ecdc4', fontFamily: 'monospace', backgroundColor: 'rgba(78, 205, 196, 0.08)', padding: '2px 6px', borderRadius: 4 } }, 'xterm.js')
            ),
            // Right: Control Toolbar
            h('div', { style: { display: 'flex', alignItems: 'center', gap: 10 } },
              // Reconnect
              h('button', {
                onClick: () => {
                  if (activeSessionId) {
                    const currentId = activeSessionId;
                    setActiveSessionId(null);
                    setTimeout(() => setActiveSessionId(currentId), 50);
                  }
                },
                title: 'Reconnect PTY Session',
                style: { background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 4, color: '#a29bfe', cursor: 'pointer', padding: '4px 8px', fontSize: 11 }
              }, '🔄 Reconnect'),
              
              // Clear screen
              h('button', {
                onClick: () => {
                  if (termInstance.current) {
                    termInstance.current.clear();
                  }
                },
                title: 'Clear Screen',
                style: { background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 4, color: '#ffd32a', cursor: 'pointer', padding: '4px 8px', fontSize: 11 }
              }, '🧹 Clear'),

              // Font Zoom controls
              h('div', { style: { display: 'flex', alignItems: 'center', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 4, overflow: 'hidden' } },
                h('button', {
                  type: 'button',
                  onMouseDown: e => e.preventDefault(),
                  onClick: () => setTerminalFontSize(prev => Math.max(10, prev - 1)),
                  style: {
                    background: 'rgba(255,255,255,0.03)',
                    border: 'none',
                    borderRight: '1px solid rgba(255,255,255,0.08)',
                    color: '#fff',
                    cursor: 'pointer',
                    padding: '4px 8px',
                    fontSize: 10,
                    lineHeight: 1,
                    fontWeight: 600,
                    appearance: 'none',
                    WebkitAppearance: 'none',
                    userSelect: 'none',
                    WebkitUserSelect: 'none',
                    WebkitTapHighlightColor: 'transparent'
                  }
                }, 'A-'),
                h('span', { style: { padding: '0 8px', fontSize: 10, color: '#fff', backgroundColor: 'rgba(0,0,0,0.2)', fontFamily: 'monospace', lineHeight: 1 } }, `${terminalFontSize}px`),
                h('button', {
                  type: 'button',
                  onMouseDown: e => e.preventDefault(),
                  onClick: () => setTerminalFontSize(prev => Math.min(24, prev + 1)),
                  style: {
                    background: 'rgba(255,255,255,0.03)',
                    border: 'none',
                    borderLeft: '1px solid rgba(255,255,255,0.08)',
                    color: '#fff',
                    cursor: 'pointer',
                    padding: '4px 8px',
                    fontSize: 10,
                    lineHeight: 1,
                    fontWeight: 600,
                    appearance: 'none',
                    WebkitAppearance: 'none',
                    userSelect: 'none',
                    WebkitUserSelect: 'none',
                    WebkitTapHighlightColor: 'transparent'
                  }
                }, 'A+')
              ),

              // Focus Mode Toggle (Maximize)
              h('button', {
                onClick: () => setTerminalMaximized(!terminalMaximized),
                title: terminalMaximized ? 'Restore split layout' : 'Maximize terminal console',
                style: {
                  background: terminalMaximized ? 'rgba(78, 205, 196, 0.12)' : 'rgba(255,255,255,0.03)',
                  border: `1px solid ${terminalMaximized ? '#4ecdc4' : 'rgba(255,255,255,0.08)'}`,
                  borderRadius: 4,
                  color: terminalMaximized ? '#4ecdc4' : '#fff',
                  cursor: 'pointer',
                  padding: '4px 8px',
                  fontSize: 11
                }
              }, terminalMaximized ? '🖥️ Split' : '🖥️ Maximize')
            )
          ),
          h('div', {
            ref: terminalRef,
            style: {
              flex: 1,
              padding: 6,
              overflow: 'hidden'
            }
          })
        )
      ),

      // Glassmorphic Modal overlay
      showModal && h('div', {
        style: {
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          backgroundColor: 'rgba(5, 5, 8, 0.8)',
          backdropFilter: 'blur(8px)',
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          zIndex: 9999
        }
      },
        h('div', {
          className: 'glowing-border',
          style: {
            backgroundColor: '#0e0e16',
            border: '1px solid rgba(108, 92, 231, 0.2)',
            borderRadius: 16,
            padding: 24,
            width: '100%',
            maxWidth: 460,
            boxShadow: '0 10px 40px rgba(0,0,0,0.8)'
          }
        },
          h('h3', { style: { marginTop: 0, marginBottom: 20, fontSize: 16, color: '#a29bfe', display: 'flex', justifyContent: 'space-between' } }, 
            h('span', null, 'Configure Swarm Session'),
            h('button', {
              onClick: () => setShowModal(false),
              style: { background: 'transparent', border: 'none', color: '#ff6b6b', cursor: 'pointer', fontSize: 16 }
            }, '×')
          ),
          
          h('form', { onSubmit: handleCreateSession, style: { display: 'flex', flexDirection: 'column', gap: 16 } },
            // Title Input
            h('div', null,
              h('label', { style: { display: 'block', fontSize: 11, opacity: 0.5, marginBottom: 6, textTransform: 'uppercase' } }, 'Session Title'),
              h('input', {
                value: modalTitle,
                onChange: (e) => setModalTitle(e.target.value),
                placeholder: 'e.g. Audit GPU Memory',
                style: { width: '100%', padding: '10px 12px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.08)', backgroundColor: '#06060a', color: '#fff', fontSize: 13, outline: 'none' }
              })
            ),

            // Profile / CLI wrapper
            h('div', null,
              h('label', { style: { display: 'block', fontSize: 11, opacity: 0.5, marginBottom: 6, textTransform: 'uppercase' } }, 'Agent Wrapper Profile'),
              h('select', {
                value: modalProfile,
                onChange: (e) => setModalProfile(e.target.value),
                style: { width: '100%', padding: '10px 12px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.08)', backgroundColor: '#06060a', color: '#fff', fontSize: 13, outline: 'none' }
              },
                h('option', { value: 'orchestrator' }, 'Hermes Orchestrator (TUI)'),
                h('option', { value: 'jules' }, 'Jules CLI (Git/PR agent)'),
                h('option', { value: 'agy' }, 'Antigravity CLI (Research agent)'),
                h('option', { value: 'codex' }, 'Codex (Visible coding session)'),
                h('option', { value: 'ollama-qwen' }, 'Local Qwen 32B (LLM Engine)'),
                h('option', { value: 'ollama-hermes' }, 'Local Hermes 70B (LLM Engine)')
              )
            ),

            // Branch target dropdown with custom toggle
            h('div', null,
              h('label', { style: { display: 'block', fontSize: 11, opacity: 0.5, marginBottom: 6, textTransform: 'uppercase' } }, 'Git Branch target'),
              h('div', { style: { display: 'flex', gap: 8 } },
                useCustomBranch ? 
                  h('input', {
                    value: modalBranch,
                    onChange: (e) => setModalBranch(e.target.value),
                    placeholder: 'e.g. feature-branch',
                    style: { flex: 1, padding: '10px 12px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.08)', backgroundColor: '#06060a', color: '#fff', fontSize: 13, outline: 'none' }
                  }) :
                  h('select', {
                    value: modalBranch,
                    onChange: (e) => setModalBranch(e.target.value),
                    style: { flex: 1, padding: '10px 12px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.08)', backgroundColor: '#06060a', color: '#fff', fontSize: 13, outline: 'none' }
                  },
                    gitBranches.length === 0 ? h('option', { value: 'main' }, 'main (default)') :
                      gitBranches.map(b => h('option', { key: b, value: b }, b))
                  ),
                h('button', {
                  type: 'button',
                  onClick: () => {
                    setUseCustomBranch(!useCustomBranch);
                    if (!useCustomBranch && gitBranches.length > 0) setModalBranch(gitBranches[0]);
                  },
                  style: {
                    background: 'rgba(255,255,255,0.04)',
                    border: '1px solid rgba(255,255,255,0.08)',
                    borderRadius: 8,
                    color: '#ffd32a',
                    padding: '0 12px',
                    cursor: 'pointer',
                    fontSize: 11
                  }
                }, useCustomBranch ? 'List' : 'Custom')
              )
            ),

            // Linear Issue dropdown with custom toggle
            h('div', null,
              h('label', { style: { display: 'block', fontSize: 11, opacity: 0.5, marginBottom: 6, textTransform: 'uppercase' } }, 'Linear Issue Link (Optional)'),
              h('div', { style: { display: 'flex', gap: 8 } },
                useCustomIssue ?
                  h('input', {
                    value: modalIssue,
                    onChange: (e) => setModalIssue(e.target.value),
                    placeholder: 'e.g. GRO-60',
                    style: { flex: 1, padding: '10px 12px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.08)', backgroundColor: '#06060a', color: '#fff', fontSize: 13, outline: 'none' }
                  }) :
                  h('select', {
                    value: modalIssue,
                    onChange: (e) => setModalIssue(e.target.value),
                    style: { flex: 1, padding: '10px 12px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.08)', backgroundColor: '#06060a', color: '#fff', fontSize: 13, outline: 'none' }
                  },
                    h('option', { value: '' }, 'None (Unassigned)'),
                    linearIssues.map(issue => h('option', { key: issue.id, value: issue.identifier }, issue.title))
                  ),
                h('button', {
                  type: 'button',
                  onClick: () => {
                    setUseCustomIssue(!useCustomIssue);
                    setModalIssue('');
                  },
                  style: {
                    background: 'rgba(255,255,255,0.04)',
                    border: '1px solid rgba(255,255,255,0.08)',
                    borderRadius: 8,
                    color: '#ffd32a',
                    padding: '0 12px',
                    cursor: 'pointer',
                    fontSize: 11
                  }
                }, useCustomIssue ? 'List' : 'Custom')
              )
            ),

            // Submit Button
            h('button', {
              type: 'submit',
              style: {
                marginTop: 10,
                padding: '12px',
                borderRadius: 8,
                border: 'none',
                background: 'linear-gradient(135deg, #6c5ce7, #5f27cd)',
                color: '#fff',
                fontWeight: 'bold',
                cursor: 'pointer',
                fontSize: 13,
                boxShadow: '0 4px 12px rgba(108, 92, 231, 0.3)'
              }
            }, 'Initialize Swarm Run')
          )
        )
      )
    );
  }

  registry.register('hermes-plugin-swarm-manager', SwarmManagerApp);
})();
