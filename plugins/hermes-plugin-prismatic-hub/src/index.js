(function () {
  const sdk = window.__HERMES_PLUGIN_SDK__;
  const registry = window.__HERMES_PLUGINS__;
  if (!sdk || !registry) {
    console.warn('[prismatic-hub] Hermes plugin SDK not available');
    return;
  }

  const React = sdk.React;
  const h = React.createElement;
  const api = sdk.fetchJSON || sdk.api;

  // Agent profiles config mapping from spec
  const AGENTS = [
    { id: 'agy', name: 'AGY', color: '#F39C12', badge: '🟠', label: 'agent:agy', role: 'Vision & Research CLI', status: 'idle', lastActive: '2m ago', task: 'Completed UI mockup draft' },
    { id: 'jules', name: 'Jules', color: '#E67E22', badge: '🍊', label: 'agent:jules', role: 'Async Git & PR Agent', status: 'working', lastActive: 'Active now', task: 'Creating branch for GRO-671' },
    { id: 'fred', name: 'Fred', color: '#3498DB', badge: '🔵', label: 'agent:fred', role: 'Nudge/Staging Governor', status: 'idle', lastActive: '15m ago', task: 'Idle · listening' },
    { id: 'ned', name: 'Ned', color: '#9B59B6', badge: '🟣', label: 'agent:ned', role: 'Research & Synthesis', status: 'offline', lastActive: '1h ago', task: 'Idle · standby' },
    { id: 'kai', name: 'Kai', color: '#2ECC71', badge: '🟢', label: 'agent:kai', role: 'Orchestrator Coordinator', status: 'working', lastActive: '10s ago', task: 'Synchronizing file signals' },
    { id: 'codex', name: 'Codex', color: '#E74C3C', badge: '🔴', label: 'agent:codex', role: 'PR Review Specialist', status: 'error', lastActive: '30m ago', task: 'PR #12 failed checks' }
  ];

  const INITIAL_WORKSPACES = [
    { id: 'linear-growth', name: 'Linear · GrowthWebDev', path: '/home/ubuntu/work/linear-growth-context', branch: 'main', status: 'connected', files: 142, size: '2.4 MB' },
    { id: 'github-mbgulden', name: 'GitHub · mbgulden', path: '/home/ubuntu/work/agentic-swarm-ops', branch: 'main', status: 'connected', files: 89, size: '1.8 MB' },
    { id: 'gdrive-context', name: 'Google Drive · Context', path: '/home/ubuntu/mounts/google-drive-context', branch: 'n/a', status: 'stalled', files: 34, size: '480 KB' },
    { id: 'discord-aot', name: 'Discord · AOT Feed', path: '/home/ubuntu/work/discord-feed-adapter', branch: 'main', status: 'connected', files: 12, size: '150 KB' }
  ];

  const INITIAL_EVENTS = [
    { id: 'evt-001', time: '14:41:22', source: 'Linear', ref: 'GRO-675', title: 'Design Prismatic Hub dashboard mockup', label: 'agent:agy', route: 'AGY', status: 'Success', duration: '12s' },
    { id: 'evt-002', time: '14:39:10', source: 'GitHub', ref: 'PR #24', title: 'Implement SQLite dedup database', label: 'requires:review', route: 'Codex', status: 'Success', duration: '45s' },
    { id: 'evt-003', time: '14:36:55', source: 'Cron', ref: 'daily-backup', title: 'Run weekly curator backup task', label: 'agent:kai', route: 'Kai', status: 'Success', duration: '90s' },
    { id: 'evt-004', time: '14:23:05', source: 'Linear', ref: 'GRO-671', title: 'Rebase active-oahu workspace tree', label: 'agent:jules', route: 'Jules', status: 'Success', duration: '5s' }
  ];

  const INITIAL_SKILLS = [
    { id: 'code-review', name: 'Code Reviewer', description: 'Automated PR sanity checking, security vulnerability scan, and performance regression reviews.', version: 'v1.2', author: 'Codex Spoke', installed: true, status: 'Active' },
    { id: 'docs-generator', name: 'Docs Generator', description: 'Reads project classes, functions, and docstrings to auto-generate markdown files in the docs directory.', version: 'v0.9', author: 'Hermes Spoke', installed: true, status: 'Active' },
    { id: 'research-synthesizer', name: 'Research Synthesizer', description: 'Scrapes Synology NAS backups, Google Drive folders, and compiles historical event summaries.', version: 'v1.0', author: 'Research Spoke', installed: false, status: 'Available' },
    { id: 'vram-watchdog', name: 'VRAM Watchdog', description: 'GPU monitoring daemon that halts running models if hardware memory exceeds 90% capacity limit.', version: 'v1.5', author: 'Custom Spoke', installed: false, status: 'Available' }
  ];

  const INITIAL_RUN_RECORDS = [
    { id: 'run-101', date: '2026-06-11 14:41', agent: 'AGY', status: 'Success', trigger: 'Linear GRO-675', logSize: '4.8 KB' },
    { id: 'run-102', date: '2026-06-11 14:36', agent: 'Kai', status: 'Success', trigger: 'Cron daily-backup', logSize: '2.1 KB' },
    { id: 'run-103', date: '2026-06-11 14:23', agent: 'Jules', status: 'Success', trigger: 'Linear GRO-671', logSize: '1.4 KB' },
    { id: 'run-104', date: '2026-06-11 14:15', agent: 'Codex', status: 'Failed', trigger: 'GitHub PR #22', logSize: '18.4 KB' }
  ];

  const AGENT_QUEUES = {
    agy: [
      { ref: 'GRO-1219', title: 'Design mobile-responsive Prismatic Hub dashboard', priority: 'High' }
    ],
    jules: [
      { ref: 'GRO-671', title: 'Rebase active-oahu workspace tree', priority: 'Medium' }
    ],
    fred: [
      { ref: 'GRO-677', title: 'Watch staging repository for main merge', priority: 'High' }
    ],
    ned: [
      { ref: 'GRO-1100', title: 'Search Google Drive contexts for ITAD', priority: 'Low' }
    ],
    kai: [
      { ref: 'GRO-36', title: 'Coordinate swarm dispatcher registry config', priority: 'Urgent' }
    ],
    codex: [
      { ref: 'GRO-120', title: 'PR sanity checking and vulnerability scan', priority: 'High' }
    ]
  };

  const getTabFromPath = () => {
    const path = window.location.pathname || '';
    if (path.endsWith('/workspaces')) return 'workspaces';
    if (path.endsWith('/skills')) return 'skills';
    if (path.endsWith('/signals')) return 'signals';
    
    const hash = window.location.hash || '';
    if (hash.includes('/workspaces')) return 'workspaces';
    if (hash.includes('/skills')) return 'skills';
    if (hash.includes('/signals')) return 'signals';
    return 'dashboard';
  };

  function PrismaticHubApp() {
    // UI state
    const [activeTab, setActiveTab] = React.useState(getTabFromPath());
    const [deploymentMode, setDeploymentMode] = React.useState('hermes-native'); // hermes-native / pip-standalone
    const [selectedAgentFilter, setSelectedAgentFilter] = React.useState(null);
    const [hoveredAgentId, setHoveredAgentId] = React.useState(null);
    const [agents, setAgents] = React.useState(AGENTS);
    const [workspaces, setWorkspaces] = React.useState(INITIAL_WORKSPACES);
    const [events, setEvents] = React.useState(INITIAL_EVENTS);
    const [skills, setSkills] = React.useState(INITIAL_SKILLS);
    const [runRecords, setRunRecords] = React.useState(INITIAL_RUN_RECORDS);
    const [drawerAgent, setDrawerAgent] = React.useState(null);
    
    // Skill loading spinner states
    const [loadingSkillId, setLoadingSkillId] = React.useState(null);

    // Live Simulator state
    const [simIssueId, setSimIssueId] = React.useState('GRO-1219');
    const [simTitle, setSimTitle] = React.useState('Design Prismatic Hub dashboard mockup');
    const [simLabel, setSimLabel] = React.useState('agent:agy');
    const [simStatus, setSimStatus] = React.useState(''); // 'routing' | 'executing' | 'success' | ''
    const [activePulseAgent, setActivePulseAgent] = React.useState(null);
    
    // SQLite Dedup Stats
    const [dedupStats, setDedupStats] = React.useState({
      totalSeen: 1424,
      suppressed: 189,
      efficiency: '94.2%',
      dbSize: '240 KB'
    });

    React.useEffect(() => {
      const handlePopState = () => {
        setActiveTab(getTabFromPath());
      };
      window.addEventListener('popstate', handlePopState);
      return () => window.removeEventListener('popstate', handlePopState);
    }, []);

    const handleTabClick = (tab) => {
      setActiveTab(tab);
      const path = tab === 'dashboard' ? '/prismatic-hub' : `/prismatic-hub/${tab}`;
      if (window.location.hash) {
        window.location.hash = path;
      } else {
        window.history.pushState({}, '', path);
      }
    };

    const triggerLiveDispatch = () => {
      if (simStatus !== '') return;
      setSimStatus('routing');
      
      // Determine agent from label
      let targetAgent = 'kai';
      if (simLabel.includes('agy')) targetAgent = 'agy';
      else if (simLabel.includes('jules')) targetAgent = 'jules';
      else if (simLabel.includes('fred')) targetAgent = 'fred';
      else if (simLabel.includes('ned') || simLabel.includes('research')) targetAgent = 'ned';
      else if (simLabel.includes('codex') || simLabel.includes('review')) targetAgent = 'codex';
      
      // Animate prism pulse
      setTimeout(() => {
        setSimStatus('executing');
        setActivePulseAgent(targetAgent);
        
        // Update the agent card status
        setAgents(prev => prev.map(a => {
          if (a.id === targetAgent) {
            return { ...a, status: 'working', task: `Processing ${simIssueId}: ${simTitle}`, lastActive: 'Active now' };
          }
          return a;
        }));
      }, 1000);

      // Complete execution
      setTimeout(() => {
        setSimStatus('success');
        setActivePulseAgent(null);
        
        // Add event
        const now = new Date();
        const timeStr = now.toTimeString().split(' ')[0];
        const newEvt = {
          id: `evt-${Math.random().toString(36).substring(2, 6)}`,
          time: timeStr,
          source: 'Live Sim',
          ref: simIssueId,
          title: simTitle,
          label: simLabel,
          route: targetAgent.toUpperCase(),
          status: 'Success',
          duration: '4s'
        };
        setEvents(prev => [newEvt, ...prev]);

        // Add run record
        const dateStr = now.toISOString().split('T')[0] + ' ' + timeStr.substring(0, 5);
        const newRun = {
          id: `run-${Math.floor(Math.random() * 900) + 100}`,
          date: dateStr,
          agent: targetAgent.toUpperCase(),
          status: 'Success',
          trigger: `Live Sim ${simIssueId}`,
          logSize: '3.2 KB'
        };
        setRunRecords(prev => [newRun, ...prev]);

        // Increment stats
        setDedupStats(prev => ({
          ...prev,
          totalSeen: prev.totalSeen + 1
        }));

        // Reset agent status
        setTimeout(() => {
          setAgents(prev => prev.map(a => {
            if (a.id === targetAgent) {
              return { ...a, status: 'idle', task: `Completed ${simIssueId}` };
            }
            return a;
          }));
          setSimStatus('');
        }, 1500);

      }, 3000);
    };

    const toggleSkill = (skillId) => {
      setLoadingSkillId(skillId);
      setTimeout(() => {
        setSkills(prev => prev.map(s => {
          if (s.id === skillId) {
            const nextInstalled = !s.installed;
            return { ...s, installed: nextInstalled, status: nextInstalled ? 'Active' : 'Available' };
          }
          return s;
        }));
        setLoadingSkillId(null);
      }, 1200);
    };

    const toggleAgentPause = (agentId) => {
      setAgents(prev => prev.map(a => {
        if (a.id === agentId) {
          const nextStatus = a.status === 'offline' ? 'idle' : 'offline';
          const updated = { ...a, status: nextStatus, task: nextStatus === 'offline' ? 'Offline · disabled' : 'Idle · listening' };
          if (drawerAgent && drawerAgent.id === agentId) {
            setDrawerAgent(updated);
          }
          return updated;
        }
        return a;
      }));
    };

    const forceDispatchFromDrawer = (agentId) => {
      const target = agents.find(a => a.id === agentId);
      if (!target || target.status === 'offline') return;
      
      setSimIssueId('GRO-MOCK');
      setSimTitle(`Ad-hoc dispatch trigger for ${target.name}`);
      setSimLabel(`agent:${target.id}`);
      
      setActiveTab('dashboard');
      setTimeout(() => {
        triggerLiveDispatch();
      }, 100);
      setDrawerAgent(null);
    };

    const filteredEvents = React.useMemo(() => {
      if (!selectedAgentFilter) return events;
      return events.filter(e => e.route.toLowerCase() === selectedAgentFilter.toLowerCase());
    }, [events, selectedAgentFilter]);

    const activeAgentDetail = React.useMemo(() => {
      const activeId = hoveredAgentId || selectedAgentFilter;
      if (!activeId) return null;
      return agents.find(a => a.id === activeId);
    }, [agents, hoveredAgentId, selectedAgentFilter]);

    // RENDER HELPER FOR HEAD & NAV
    const renderHeaderAndNav = () => {
      return h('div', null, [
        // Header
        h('header', { className: 'hub-header' }, [
          h('div', { style: { display: 'flex', alignItems: 'center', gap: '16px' } }, [
            h('div', { className: 'logo-box' }, [
              h('span', { className: 'logo-icon' }, '⧉')
            ]),
            h('div', null, [
              h('h1', { className: 'brand-title' }, 'PRISMATIC HUB'),
              h('p', { className: 'brand-subtitle' }, 'White Coordinator ➔ Full Spectrum Autonomy')
            ])
          ]),
          h('div', { className: 'header-controls' }, [
            // Switcher
            h('div', { className: 'mode-switcher' }, [
              h('button', {
                onClick: () => setDeploymentMode('hermes-native'),
                className: `mode-btn ${deploymentMode === 'hermes-native' ? 'active' : ''}`
              }, 'Hermes-Native'),
              h('button', {
                onClick: () => setDeploymentMode('pip-standalone'),
                className: `mode-btn ${deploymentMode === 'pip-standalone' ? 'active' : ''}`
              }, 'Standalone (pip)')
            ]),
            // Live Indicator
            h('div', { className: 'live-badge' }, [
              h('span', { className: 'pulse-dot' }),
              'HUB LIVE'
            ])
          ])
        ]),

        // Tab Navigation
        h('nav', { className: 'nav-tabs' }, [
          h('button', {
            onClick: () => handleTabClick('dashboard'),
            className: `nav-tab ${activeTab === 'dashboard' ? 'active' : ''}`
          }, 'Dashboard'),
          h('button', {
            onClick: () => handleTabClick('workspaces'),
            className: `nav-tab ${activeTab === 'workspaces' ? 'active' : ''}`
          }, 'Workspaces'),
          h('button', {
            onClick: () => handleTabClick('skills'),
            className: `nav-tab ${activeTab === 'skills' ? 'active' : ''}`
          }, 'Skills'),
          h('button', {
            onClick: () => handleTabClick('signals'),
            className: `nav-tab ${activeTab === 'signals' ? 'active' : ''}`
          }, 'Signals')
        ])
      ]);
    };

    return h('div', { className: 'hub-container' }, [
      renderHeaderAndNav(),

      /* -------------------------------------------------- */
      /* TAB PANEL: DASHBOARD                               */
      /* -------------------------------------------------- */
      activeTab === 'dashboard' && h('div', { className: 'tab-panel' }, [
        // Visualizer section
        h('div', { className: 'visualizer-grid' }, [
          // Interactive Prism
          h('div', { className: 'card-panel prism-card' }, [
            h('div', { className: 'card-header' }, [
              h('h2', null, 'Orchestration Spectrum (Interactive)'),
              h('span', { className: 'card-meta-text' }, 'White Coordinator ➔ Wavelength Beams')
            ]),
            h('div', { className: 'prism-svg-container' }, [
              h('svg', {
                width: '100%',
                height: '240px',
                viewBox: '0 0 600 240',
                className: 'prism-svg'
              }, [
                h('defs', null, [
                  h('filter', { id: 'glow' }, [
                    h('feGaussianBlur', { stdDeviation: '3', result: 'coloredBlur' }),
                    h('feMerge', null, [
                      h('feMergeNode', { in: 'coloredBlur' }),
                      h('feMergeNode', { in: 'SourceGraphic' })
                    ])
                  ]),
                  h('filter', { id: 'glow-strong' }, [
                    h('feGaussianBlur', { stdDeviation: '6', result: 'coloredBlur' }),
                    h('feMerge', null, [
                      h('feMergeNode', { in: 'coloredBlur' }),
                      h('feMergeNode', { in: 'SourceGraphic' })
                    ])
                  ])
                ]),
                // Dispatcher input beam
                h('line', {
                  x1: '50', y1: '120',
                  x2: '235', y2: '120',
                  stroke: '#ffffff',
                  strokeWidth: simStatus === 'routing' ? '5' : '2',
                  opacity: simStatus === 'routing' ? '1.0' : '0.4',
                  filter: simStatus === 'routing' ? 'url(#glow-strong)' : 'none',
                  style: { transition: 'all 0.3s ease' }
                }),
                h('text', { x: '60', y: '110', fill: '#fff', fontSize: '11px', fontFamily: 'monospace', opacity: '0.6' }, 'white_light (events)'),

                // Glass Triangle
                h('polygon', {
                  points: '270,40 330,170 210,170',
                  fill: 'rgba(255, 255, 255, 0.03)',
                  stroke: 'rgba(255, 255, 255, 0.25)',
                  strokeWidth: '1.5'
                }),
                h('polygon', {
                  points: '270,45 320,165 220,165',
                  fill: 'rgba(108, 92, 231, 0.05)',
                  opacity: '0.7'
                }),

                // Pulsing trigger
                simStatus === 'executing' && h('circle', {
                  cx: '270', cy: '120', r: '12',
                  fill: 'rgba(108, 92, 231, 0.8)',
                  filter: 'url(#glow)',
                  style: { animation: 'pulse 1.5s infinite' }
                }),

                // Wavelength Beams (Exiting spectrum)
                agents.map((agent, i) => {
                  const beamY = 50 + (i * 30);
                  const isSelected = selectedAgentFilter === agent.id;
                  const isHovered = hoveredAgentId === agent.id;
                  const isPulse = activePulseAgent === agent.id;

                  return h('g', { key: agent.id }, [
                    h('line', {
                      x1: '270', y1: '120',
                      x2: '460', y2: beamY,
                      stroke: agent.color,
                      strokeWidth: isPulse ? '5' : (isHovered || isSelected ? '3' : '1.5'),
                      opacity: agent.status === 'offline' ? '0.1' : (isPulse ? '1.0' : (isHovered || isSelected || !selectedAgentFilter ? '0.8' : '0.15')),
                      filter: isPulse || isHovered || isSelected ? 'url(#glow)' : 'none',
                      style: { cursor: 'pointer', transition: 'all 0.2s ease' },
                      onMouseEnter: () => setHoveredAgentId(agent.id),
                      onMouseLeave: () => setHoveredAgentId(null),
                      onClick: () => setSelectedAgentFilter(isSelected ? null : agent.id)
                    }),
                    h('text', {
                      x: '475', y: beamY + 4,
                      fill: isHovered || isSelected ? '#fff' : (agent.status === 'offline' ? 'rgba(255,255,255,0.2)' : agent.color),
                      fontSize: '11px',
                      fontFamily: 'monospace',
                      fontWeight: 'bold',
                      style: { cursor: 'pointer', transition: 'all 0.2s ease' },
                      onMouseEnter: () => setHoveredAgentId(agent.id),
                      onMouseLeave: () => setHoveredAgentId(null),
                      onClick: () => setSelectedAgentFilter(isSelected ? null : agent.id)
                    }, `${agent.name} (${agent.badge})`)
                  ]);
                })
              ])
            ]),
            h('div', { className: 'visualizer-tip' }, [
              h('span', null, '💡 Tip: Click on a wavelength beam above or a card below to filter dispatch history by agent. Click the agent card header to open details panel.'),
              selectedAgentFilter && h('button', {
                onClick: () => setSelectedAgentFilter(null),
                className: 'btn-clear-filter'
              }, 'Clear Filter')
            ])
          ]),

          // Info Card
          h('div', { className: 'card-panel info-card' }, [
            h('div', { className: 'card-header' }, [
              h('h2', null, 'Orchestration Wavelength Info'),
              activeAgentDetail && h('span', {
                className: 'label-badge',
                style: { backgroundColor: `${activeAgentDetail.color}15`, border: `1px solid ${activeAgentDetail.color}40`, color: activeAgentDetail.color }
              }, activeAgentDetail.label)
            ]),

            activeAgentDetail ? h('div', { className: 'info-content' }, [
              h('div', { className: 'info-agent-row' }, [
                h('div', { className: 'status-dot-large', style: { backgroundColor: activeAgentDetail.color, boxShadow: `0 0 12px ${activeAgentDetail.color}` } }),
                h('h3', null, activeAgentDetail.name),
                h('span', { className: 'info-role-text' }, `— ${activeAgentDetail.role}`)
              ]),
              
              h('div', { className: 'info-task-box' }, [
                h('div', { className: 'task-box-header' }, [
                  h('span', null, 'ACTIVE TASK'),
                  h('span', { className: `status-label ${activeAgentDetail.status}` }, activeAgentDetail.status.toUpperCase())
                ]),
                h('div', { className: 'task-box-body' }, activeAgentDetail.task)
              ]),

              h('div', { className: 'info-meta-grid' }, [
                h('div', null, [
                  h('span', { className: 'meta-lbl' }, 'Last Activity'),
                  h('span', { className: 'meta-val' }, activeAgentDetail.lastActive)
                ]),
                h('div', null, [
                  h('span', { className: 'meta-lbl' }, 'Dedup Rules'),
                  h('span', { className: 'meta-val code' }, 'SQLite Key Hash')
                ])
              ])
            ]) : h('div', { className: 'info-placeholder' }, 'Hover over a prism wavelength beam or click one to view detailed agent profile mapping.'),

            h('div', { className: 'info-footer-config' }, [
              deploymentMode === 'hermes-native' ? h('div', null, [
                h('span', { className: 'config-header' }, 'Hermes Integration Config'),
                h('pre', { className: 'config-pre native' }, 'hermes profile create prismatic\ncp templates/hermes-profile/* ~/.hermes/profiles/prismatic/')
              ]) : h('div', null, [
                h('span', { className: 'config-header' }, 'Standalone Deployment Config'),
                h('pre', { className: 'config-pre standalone' }, 'pip install prismatic-hub\nprismatic-hub init\nprismatic-hub serve  # runs dispatch daemon')
              ])
            ])
          ])
        ]),

        // Agent Wavelength Spokes
        h('div', { className: 'spokes-section' }, [
          h('h2', { className: 'section-title' }, [
            h('span', null, '📡'),
            'Agent Wavelength Spokes'
          ]),
          h('div', { className: 'agent-cards-grid' }, agents.map(agent => {
            const isSelected = selectedAgentFilter === agent.id;
            const isHovered = hoveredAgentId === agent.id;
            const isPulse = activePulseAgent === agent.id;

            return h('div', {
              key: agent.id,
              className: `agent-card ${isSelected ? 'selected' : ''} ${agent.status}`,
              style: {
                borderColor: isSelected || isHovered ? agent.color : 'rgba(255,255,255,0.08)',
                boxShadow: isSelected || isHovered || isPulse ? `0 4px 20px ${agent.color}15, 0 0 10px ${agent.color}05` : 'none'
              },
              onMouseEnter: () => setHoveredAgentId(agent.id),
              onMouseLeave: () => setHoveredAgentId(null)
            }, [
              h('div', { className: 'card-edge-tag', style: { backgroundColor: agent.color } }),
              h('div', {
                className: 'card-header-clickable',
                onClick: () => setDrawerAgent(agent)
              }, [
                h('span', { className: 'agent-name-badge' }, `${agent.badge} ${agent.name}`),
                h('span', { className: `status-tag ${agent.status}` }, agent.status)
              ]),
              h('div', { className: 'card-body-clickable', onClick: () => setSelectedAgentFilter(isSelected ? null : agent.id) }, [
                h('p', { className: 'agent-role' }, agent.role),
                h('div', { className: `agent-task-snippet ${agent.status === 'working' ? 'active' : ''}` }, agent.task)
              ])
            ]);
          }))
        ]),

        // Tier 3 Split Layout
        h('div', { className: 'dashboard-grid' }, [
          // Left: Activity log
          h('div', { className: 'card-panel' }, [
            h('div', { className: 'card-header' }, [
              h('div', null, [
                h('h2', null, 'Recent Activity Feed'),
                h('p', { className: 'card-meta-text' }, selectedAgentFilter ? `Filtered by agent: ${selectedAgentFilter}` : 'All routed webhook events')
              ]),
              selectedAgentFilter && h('button', {
                onClick: () => setSelectedAgentFilter(null),
                className: 'btn-table-action'
              }, 'Show All')
            ]),
            h('div', { className: 'shimmer-divider' }),
            h('div', { className: 'activity-feed' }, filteredEvents.length > 0 ? filteredEvents.map(evt => {
              const agentObj = agents.find(a => a.name.toUpperCase() === evt.route.toUpperCase());
              const agentColor = agentObj ? agentObj.color : '#fff';

              return h('div', { key: evt.id, className: 'activity-item', style: { borderLeftColor: agentColor } }, [
                h('span', { className: 'dot-activity', style: { backgroundColor: agentColor, boxShadow: `0 0 6px ${agentColor}` } }),
                h('div', { className: 'activity-text-row' }, [
                  h('span', { className: 'time' }, evt.time),
                  h('span', { className: 'ref-code' }, `[${evt.ref}]`),
                  h('span', { className: 'source-badge' }, evt.source),
                  h('span', { className: 'title' }, evt.title)
                ]),
                h('span', { className: 'route-badge', style: { color: agentColor, border: `1px solid ${agentColor}30`, backgroundColor: `${agentColor}10` } }, evt.route)
              ]);
            }) : h('div', { className: 'empty-feed' }, 'No recent dispatch events found for this filter.'))
          ]),

          // Right: Active Workspaces & SQLite Dedup metrics
          h('div', { style: { display: 'flex', flexDirection: 'column', gap: '24px' } }, [
            // Workspaces list
            h('div', { className: 'card-panel' }, [
              h('div', { className: 'card-header' }, [
                h('h2', null, 'Active Workspaces'),
                h('button', {
                  onClick: () => handleTabClick('workspaces'),
                  className: 'btn-card-action'
                }, 'Manage')
              ]),
              h('div', { className: 'shimmer-divider' }),
              h('div', { className: 'workspace-table-container' }, [
                h('table', { className: 'compact-table' }, [
                  h('thead', null, [
                    h('tr', null, [
                      h('th', null, 'Workspace'),
                      h('th', null, 'Active Branch'),
                      h('th', null, 'Status')
                    ])
                  ]),
                  h('tbody', null, workspaces.map(ws => {
                    return h('tr', { key: ws.id }, [
                      h('td', { className: 'ws-name' }, ws.name),
                      h('td', { className: 'ws-branch' }, ws.branch),
                      h('td', { className: `ws-status ${ws.status}` }, ws.status)
                    ]);
                  }))
                ])
              ])
            ]),

            // SQLite Dedup metrics
            h('div', { className: 'card-panel' }, [
              h('h2', null, 'SQLite Dedup Database'),
              h('div', { className: 'shimmer-divider' }),
              h('div', { className: 'dedup-metrics-grid' }, [
                h('div', { className: 'metric-tile' }, [
                  h('span', { className: 'tile-lbl' }, 'Total Event Hashes'),
                  h('span', { className: 'tile-val' }, dedupStats.totalSeen)
                ]),
                h('div', { className: 'metric-tile' }, [
                  h('span', { className: 'tile-lbl' }, 'Duplicate Suppressions'),
                  h('span', { className: 'tile-val suppressed' }, dedupStats.suppressed)
                ])
              ]),
              h('div', { className: 'dedup-status-list' }, [
                h('div', { className: 'dedup-row' }, [
                  h('span', null, 'Lock State'),
                  h('span', { style: { color: '#2ECC71', fontWeight: 'bold' } }, 'Unlocked')
                ]),
                h('div', { className: 'dedup-row' }, [
                  h('span', null, 'Database Size'),
                  h('span', { className: 'code-text' }, dedupStats.dbSize)
                ])
              ]),
              h('button', {
                onClick: () => {
                  setDedupStats(prev => ({ ...prev, suppressed: prev.suppressed + 2 }));
                  alert('Cleared stale event cache in SQLite database. Suppressed duplicates normalized.');
                },
                className: 'btn-action-wide'
              }, 'Flush SQLite Stale Cache')
            ])
          ])
        ])
      ]),

      /* -------------------------------------------------- */
      /* TAB PANEL: WORKSPACES                              */
      /* -------------------------------------------------- */
      activeTab === 'workspaces' && h('div', { className: 'tab-panel' }, [
        h('div', { className: 'workspaces-layout' }, [
          h('div', { className: 'card-panel workspaces-main-panel' }, [
            h('div', { className: 'card-header' }, [
              h('h2', null, 'Workspace Registry'),
              h('button', {
                onClick: () => alert('Scanning local workspaces... Found 4 active project scopes.'),
                className: 'btn-header-action'
              }, 'Rescan Workspaces')
            ]),
            h('div', { className: 'shimmer-divider' }),
            h('div', { className: 'table-responsive' }, [
              h('table', { className: 'registry-table' }, [
                h('thead', null, [
                  h('tr', null, [
                    h('th', null, 'Workspace Name'),
                    h('th', null, 'Target Local Path'),
                    h('th', null, 'VCS Branch'),
                    h('th', null, 'Connection Status'),
                    h('th', null, 'Action')
                  ])
                ]),
                h('tbody', null, workspaces.map(ws => {
                  return h('tr', { key: ws.id }, [
                    h('td', { style: { fontWeight: '600' } }, ws.name),
                    h('td', { className: 'code-text secondary' }, ws.path),
                    h('td', null, [
                      ws.branch === 'n/a' ? h('span', { className: 'secondary' }, 'n/a') : h('select', {
                        value: ws.branch,
                        onChange: (e) => {
                          const nextVal = e.target.value;
                          setWorkspaces(prev => prev.map(w => w.id === ws.id ? { ...w, branch: nextVal, status: 'synchronizing' } : w));
                          setTimeout(() => {
                            setWorkspaces(prev => prev.map(w => w.id === ws.id ? { ...w, status: 'connected' } : w));
                          }, 1000);
                        },
                        className: 'select-branch'
                      }, [
                        h('option', { value: 'main' }, 'main'),
                        h('option', { value: 'dev-itad' }, 'dev-itad'),
                        h('option', { value: 'agent:ned' }, 'agent:ned')
                      ])
                    ]),
                    h('td', null, [
                      h('span', { className: `status-badge ${ws.status}` }, ws.status === 'connected' ? '✓ connected' : '⚠ stalled')
                    ]),
                    h('td', null, [
                      ws.status === 'stalled' 
                        ? h('button', {
                            onClick: () => {
                              setWorkspaces(prev => prev.map(w => w.id === ws.id ? { ...w, status: 'connected' } : w));
                              alert('Google Drive OAuth Token successfully rehydrated.');
                            },
                            className: 'btn-table-action reconnect'
                          }, 'Reconnect')
                        : h('button', {
                            onClick: () => alert(`Force synchronization requested for ${ws.name}`),
                            className: 'btn-table-action'
                          }, 'Force Sync'),
                      h('button', {
                        onClick: () => alert(`Workspace disconnected: ${ws.name}`),
                        className: 'btn-table-action danger'
                      }, ws.status === 'stalled' ? 'Remove' : 'Disconnect')
                    ])
                  ]);
                }))
              ])
            ])
          ]),

          h('div', { className: 'card-panel workspaces-side-panel' }, [
            h('h2', null, 'Dedup Engine Config'),
            h('div', { className: 'shimmer-divider' }),
            h('p', { className: 'secondary-text', style: { fontSize: '13px', lineHeight: '1.5', marginBottom: '16px' } }, 
              'The event dispatcher utilizes a local SQLite database to compute event keys and prevent duplicate processing sweeps.'
            ),
            h('div', { className: 'metrics-stack' }, [
              h('div', { className: 'metric-row' }, [
                h('span', null, 'Total Event Hashes'),
                h('strong', null, dedupStats.totalSeen)
              ]),
              h('div', { className: 'metric-row' }, [
                h('span', null, 'Suppressed Duplicates'),
                h('strong', { style: { color: '#ff6b6b' } }, dedupStats.suppressed)
              ]),
              h('div', { className: 'metric-row' }, [
                h('span', null, 'Dedup Efficiency'),
                h('strong', { style: { color: '#2ECC71' } }, dedupStats.efficiency)
              ]),
              h('div', { className: 'metric-row' }, [
                h('span', null, 'SQLite DB Size'),
                h('strong', null, dedupStats.dbSize)
              ])
            ]),
            h('button', {
              onClick: () => {
                setDedupStats(prev => ({ ...prev, suppressed: prev.suppressed + 2 }));
                alert('SQLite Database Cache cleared. Clean index rebuilt.');
              },
              className: 'btn-action-wide',
              style: { marginTop: '20px' }
            }, 'Flush SQLite Stale Cache')
          ])
        ])
      ]),

      /* -------------------------------------------------- */
      /* TAB PANEL: SKILLS                                  */
      /* -------------------------------------------------- */
      activeTab === 'skills' && h('div', { className: 'tab-panel' }, [
        h('div', { className: 'skills-layout' }, [
          h('div', { className: 'card-panel skills-search-header' }, [
            h('div', { className: 'skills-search-row' }, [
              h('h2', null, 'Skill Marketplace & Active Plugins'),
              h('div', { className: 'search-input-box' }, [
                h('input', {
                  type: 'text',
                  placeholder: 'Enter git repository / local folder to install custom skill...',
                  className: 'install-input'
                }),
                h('button', {
                  onClick: () => alert('Mock skill repository downloaded. Checked AGPLv3 compliance markers.'),
                  className: 'btn-install-skill'
                }, 'Install Skill')
              ])
            ]),
            h('div', { className: 'shimmer-divider' }),
            h('div', { className: 'skills-grid' }, skills.map(skill => {
              const isLoading = loadingSkillId === skill.id;

              return h('div', { key: skill.id, className: `skill-card ${skill.installed ? 'installed' : ''}` }, [
                h('div', { className: 'skill-card-header' }, [
                  h('div', null, [
                    h('h3', { className: 'skill-title' }, skill.name),
                    h('span', { className: 'skill-author' }, skill.author)
                  ]),
                  h('span', { className: 'skill-version' }, skill.version)
                ]),
                h('p', { className: 'skill-desc' }, skill.description),
                h('div', { className: 'skill-footer' }, [
                  h('span', { className: 'skill-status' }, [
                    'Status: ',
                    h('strong', { style: { color: skill.installed ? '#2ECC71' : 'rgba(255,255,255,0.4)' } }, skill.status)
                  ]),
                  h('button', {
                    onClick: () => toggleSkill(skill.id),
                    disabled: isLoading,
                    className: `btn-skill-toggle ${skill.installed ? 'danger' : 'primary'}`
                  }, isLoading ? 'Working...' : (skill.installed ? 'Uninstall' : 'Install'))
                ])
              ]);
            }))
          ])
        ])
      ]),

      /* -------------------------------------------------- */
      /* TAB PANEL: SIGNALS                                 */
      /* -------------------------------------------------- */
      activeTab === 'signals' && h('div', { className: 'tab-panel' }, [
        h('div', { className: 'signals-layout' }, [
          // Left: Providers & Log
          h('div', { className: 'signals-main-panel' }, [
            h('div', { className: 'card-panel' }, [
              h('h2', null, 'Signal Providers & Router Log'),
              h('div', { className: 'shimmer-divider' }),
              h('div', { className: 'signals-log-feed' }, [
                h('div', { className: 'signals-log-item' }, [
                  h('span', { className: 'provider-tag file' }, '⚡ FileSignalProvider'),
                  h('span', { className: 'provider-text' }, 'Polled /tmp/nudge-file-trigger ➔ no new signals'),
                  h('span', { className: 'provider-time' }, 'Just now')
                ]),
                h('div', { className: 'signals-log-item' }, [
                  h('span', { className: 'provider-tag http' }, '⚡ HTTPSignalProvider'),
                  h('span', { className: 'provider-text' }, 'Received Linear webhook for GRO-1219 (dashboard design)'),
                  h('span', { className: 'provider-time' }, '2 min ago')
                ]),
                h('div', { className: 'signals-log-item' }, [
                  h('span', { className: 'provider-tag telegram' }, '⚡ TelegramSignalProvider'),
                  h('span', { className: 'provider-text' }, 'Checking gateway status... standby'),
                  h('span', { className: 'provider-time' }, '15 min ago')
                ])
              ])
            ]),

            // Event Sandbox
            h('div', { className: 'card-panel dispatcher-sandbox-panel' }, [
              h('h2', null, '⚡ Dispatcher Sandbox'),
              h('p', { className: 'secondary-text', style: { fontSize: '12px', marginBottom: '16px' } }, 
                'Simulate webhook triggers to test the routing logic and wavelength refractions.'
              ),
              h('div', { className: 'sandbox-form-grid' }, [
                h('div', null, [
                  h('label', null, 'Issue ID / Reference'),
                  h('input', {
                    type: 'text',
                    value: simIssueId,
                    onChange: (e) => setSimIssueId(e.target.value),
                    className: 'form-control'
                  })
                ]),
                h('div', null, [
                  h('label', null, 'Issue / PR Title'),
                  h('input', {
                    type: 'text',
                    value: simTitle,
                    onChange: (e) => setSimTitle(e.target.value),
                    className: 'form-control'
                  })
                ]),
                h('div', null, [
                  h('label', null, 'Trigger Label / Wavelength'),
                  h('select', {
                    value: simLabel,
                    onChange: (e) => setSimLabel(e.target.value),
                    className: 'form-control'
                  }, [
                    h('option', { value: 'agent:agy' }, 'agent:agy (Amber)'),
                    h('option', { value: 'agent:jules' }, 'agent:jules (Orange)'),
                    h('option', { value: 'agent:fred' }, 'agent:fred (Blue)'),
                    h('option', { value: 'agent:ned' }, 'agent:ned (Purple)'),
                    h('option', { value: 'agent:kai' }, 'agent:kai (Green)'),
                    h('option', { value: 'agent:codex' }, 'agent:codex (Red)')
                  ])
                ])
              ]),
              h('button', {
                onClick: triggerLiveDispatch,
                disabled: simStatus !== '',
                className: `btn-sandbox-submit ${simStatus}`
              }, [
                simStatus === 'routing' && h('span', null, '🔍 Matching labels...'),
                simStatus === 'executing' && h('span', null, '⚡ Running Spoke CLI...'),
                simStatus === 'success' && h('span', null, '✓ Event Dispatched Successfully!'),
                simStatus === '' && h('span', null, '🚀 Dispatch Live Event')
              ])
            ])
          ]),

          // Right: Configurations
          h('div', { className: 'card-panel signals-side-panel' }, [
            h('h2', null, 'Router Configurations'),
            h('div', { className: 'shimmer-divider' }),
            h('div', { className: 'config-rows-stack' }, [
              h('div', { className: 'config-row' }, [
                h('span', { className: 'lbl' }, 'File Provider Path'),
                h('span', { className: 'val code' }, '/tmp/nudge-*')
              ]),
              h('div', { className: 'config-row' }, [
                h('span', { className: 'lbl' }, 'HTTP Server Port'),
                h('span', { className: 'val code' }, '8080 (webhook-receiver)')
              ]),
              h('div', { className: 'config-row' }, [
                h('span', { className: 'lbl' }, 'Telegram Username'),
                h('span', { className: 'val code' }, '@PrismaticSwarmBot')
              ]),
              h('div', { className: 'config-row' }, [
                h('span', { className: 'lbl' }, 'Gateway Chain'),
                h('span', { className: 'val highlight' }, 'File ➔ HTTP ➔ Telegram')
              ])
            ])
          ])
        ])
      ]),

      /* -------------------------------------------------- */
      /* DRAWER COMPONENT                                   */
      /* -------------------------------------------------- */
      drawerAgent && h('div', { className: 'drawer-wrapper' }, [
        // Backdrop overlay
        h('div', {
          onClick: () => setDrawerAgent(null),
          className: 'drawer-overlay'
        }),
        // Drawer sliding card
        h('div', { className: 'drawer-container' }, [
          h('div', { className: 'drawer-header' }, [
            h('div', { className: 'drawer-header-left' }, [
              h('div', { className: 'drawer-agent-badge', style: { color: drawerAgent.color, backgroundColor: `${drawerAgent.color}15`, borderColor: `${drawerAgent.color}40` } }, drawerAgent.badge),
              h('div', null, [
                h('h3', { className: 'drawer-title' }, drawerAgent.name),
                h('span', { className: 'drawer-subtitle' }, drawerAgent.role)
              ])
            ]),
            h('button', {
              onClick: () => setDrawerAgent(null),
              className: 'drawer-close-btn'
            }, '×')
          ]),

          h('div', { className: 'drawer-body' }, [
            // Telemetry Section
            h('div', { className: 'drawer-section' }, [
              h('h4', null, 'Telemetry Metrics'),
              h('div', { className: 'metrics-grid' }, [
                h('div', { className: 'metric-box' }, [
                  h('span', { className: 'm-lbl' }, 'Dispatched Today'),
                  h('span', { className: 'm-val' }, drawerAgent.id === 'agy' || drawerAgent.id === 'kai' ? '12 times' : '4 times')
                ]),
                h('div', { className: 'metric-box' }, [
                  h('span', { className: 'm-lbl' }, 'Avg Duration'),
                  h('span', { className: 'm-val' }, drawerAgent.id === 'codex' ? '2.4s' : '0.8s')
                ]),
                h('div', { className: 'metric-box' }, [
                  h('span', { className: 'm-lbl' }, 'SQLite Dedup Rate'),
                  h('span', { className: 'm-val' }, '96.8%')
                ]),
                h('div', { className: 'metric-box' }, [
                  h('span', { className: 'm-lbl' }, 'Status State'),
                  h('span', { className: `m-val status-color ${drawerAgent.status}` }, drawerAgent.status.toUpperCase())
                ])
              ])
            ]),

            // Issue Queue Section
            h('div', { className: 'drawer-section' }, [
              h('h4', null, 'Active Issue Queue'),
              h('div', { className: 'queue-list' }, (AGENT_QUEUES[drawerAgent.id] || []).map((qItem, idx) => {
                return h('div', { key: idx, className: 'queue-item' }, [
                  h('div', { className: 'queue-item-header' }, [
                    h('span', { className: 'q-ref' }, qItem.ref),
                    h('span', { className: `q-prio ${qItem.priority.toLowerCase()}` }, qItem.priority)
                  ]),
                  h('p', { className: 'q-title' }, qItem.title)
                ]);
              }))
            ]),

            // Run History section
            h('div', { className: 'drawer-section' }, [
              h('h4', null, 'Recent Routing Log (Last 5 runs)'),
              h('div', { className: 'log-list' }, [
                h('div', { className: 'log-item success' }, [
                  h('div', { className: 'log-meta' }, [
                    h('span', { className: 'badge' }, '✓ Success'),
                    h('span', { className: 'duration' }, '1.2s')
                  ]),
                  h('span', { className: 'time' }, 'Completed 14:41')
                ]),
                h('div', { className: 'log-item success' }, [
                  h('div', { className: 'log-meta' }, [
                    h('span', { className: 'badge' }, '✓ Success'),
                    h('span', { className: 'duration' }, '0.9s')
                  ]),
                  h('span', { className: 'time' }, 'Completed 14:23')
                ]),
                drawerAgent.status === 'error' && h('div', { className: 'log-item error' }, [
                  h('div', { className: 'log-meta' }, [
                    h('span', { className: 'badge' }, '✕ Failed'),
                    h('span', { className: 'duration' }, 'timeout')
                  ]),
                  h('span', { className: 'time' }, 'Completed 14:15')
                ])
              ].filter(Boolean))
            ])
          ]),

          h('div', { className: 'drawer-footer' }, [
            h('button', {
              onClick: () => toggleAgentPause(drawerAgent.id),
              className: 'drawer-btn-secondary'
            }, drawerAgent.status === 'offline' ? 'Resume Agent' : 'Pause Agent'),
            h('button', {
              onClick: () => forceDispatchFromDrawer(drawerAgent.id),
              disabled: drawerAgent.status === 'offline',
              className: 'drawer-btn-primary'
            }, 'Dispatch Now')
          ])
        ])
      ])
    ]);
  }

  // CSS Animations and Styles Injection
  const styleEl = document.createElement('style');
  styleEl.innerHTML = `
    /* Design Tokens */
    :root {
      --bg-deep-space: #0B0C10;
      --bg-industrial: #151A22;
      --bg-industrial-hover: #1C232E;
      --text-primary: #F5F6F7;
      --text-secondary: #8B9BB4;
      --border-subtle: #2B3544;
      --color-agy: #F39C12;
      --color-jules: #E67E22;
      --color-fred: #3498DB;
      --color-ned: #9B59B6;
      --color-kai: #2ECC71;
      --color-codex: #E74C3C;
      --color-custom: #F1C40F;
      --spectrum-gradient: linear-gradient(90deg, #F39C12, #E67E22, #3498DB, #9B59B6, #2ECC71, #E74C3C);
      --font-sans: 'Outfit', 'Inter', system-ui, sans-serif;
      --font-mono: 'JetBrains Mono', monospace;
      --radius-sm: 4px;
      --radius-md: 8px;
      --radius-lg: 12px;
    }

    /* Core Styles */
    .hub-container {
      max-width: 1440px;
      margin: 0 auto;
      font-family: var(--font-sans);
      background-color: var(--bg-deep-space);
      color: var(--text-primary);
      min-height: 100vh;
      box-sizing: border-box;
      padding: 24px;
    }

    /* Header */
    .hub-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-bottom: 20px;
      border-bottom: 1px solid rgba(255,255,255,0.08);
      margin-bottom: 24px;
    }
    .logo-box {
      background: linear-gradient(135deg, #6c5ce7, #a29bfe);
      padding: 8px 12px;
      border-radius: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 0 15px rgba(108, 92, 231, 0.4);
    }
    .logo-icon {
      font-size: 20px;
      font-weight: bold;
      color: #fff;
    }
    .brand-title {
      margin: 0;
      font-size: 22px;
      font-weight: 800;
      letter-spacing: 0.5px;
      background: linear-gradient(90deg, #fff, #a29bfe);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }
    .brand-subtitle {
      margin: 2px 0 0 0;
      font-size: 11px;
      color: rgba(255,255,255,0.5);
      text-transform: uppercase;
      letter-spacing: 1px;
    }
    .header-controls {
      display: flex;
      align-items: center;
      gap: 16px;
    }
    .mode-switcher {
      backgroundColor: #11111a;
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 20px;
      padding: 3px;
      display: flex;
      gap: 4px;
    }
    .mode-btn {
      background: transparent;
      color: rgba(255,255,255,0.6);
      border: none;
      border-radius: 16px;
      padding: 6px 14px;
      font-size: 11px;
      font-weight: bold;
      cursor: pointer;
      transition: all 0.2s ease;
    }
    .mode-btn.active {
      background-color: #6c5ce7;
      color: #fff;
    }
    .live-badge {
      display: flex;
      align-items: center;
      gap: 6px;
      background-color: rgba(46, 204, 113, 0.08);
      border: 1px solid rgba(46, 204, 113, 0.2);
      border-radius: 8px;
      padding: 6px 12px;
      font-size: 11px;
      color: #2ECC71;
      font-weight: 600;
      letter-spacing: 0.5px;
    }
    .pulse-dot {
      width: 8px;
      height: 8px;
      background-color: #2ECC71;
      border-radius: 50%;
      display: inline-block;
      box-shadow: 0 0 8px #2ECC71;
      animation: pulse 1.5s infinite;
    }

    /* Tabs Navigation */
    .nav-tabs {
      display: flex;
      gap: 8px;
      border-bottom: 1px solid rgba(255,255,255,0.08);
      padding-bottom: 10px;
      margin-bottom: 24px;
    }
    .nav-tab {
      background: transparent;
      color: rgba(255,255,255,0.6);
      border: none;
      border-radius: 6px;
      padding: 8px 16px;
      font-size: 13px;
      font-weight: bold;
      cursor: pointer;
      transition: all 0.25s ease;
    }
    .nav-tab:hover {
      color: #fff;
      background-color: rgba(255,255,255,0.04);
    }
    .nav-tab.active {
      color: #fff;
      background-color: rgba(108, 92, 231, 0.12);
      border: 1px solid rgba(108, 92, 231, 0.25);
    }

    /* Card Layouts */
    .card-panel {
      background-color: var(--bg-industrial);
      border: 1px solid var(--border-subtle);
      border-radius: 12px;
      padding: 20px;
      box-shadow: 0 4px 15px rgba(0,0,0,0.15);
      transition: border-color 0.25s ease;
    }
    .card-panel:hover {
      border-color: rgba(255,255,255,0.12);
    }
    .shimmer-divider {
      height: 1px;
      background: var(--spectrum-gradient);
      margin: 12px 0 16px 0;
      opacity: 0.6;
    }
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .card-header h2 {
      margin: 0;
      font-size: 15px;
      font-weight: 700;
      color: #fff;
    }
    .card-meta-text {
      font-size: 11px;
      color: rgba(255,255,255,0.4);
      font-family: var(--font-mono);
    }

    /* Dashboard Layouts */
    .visualizer-grid {
      display: grid;
      grid-template-columns: 7fr 5fr;
      gap: 24px;
      margin-bottom: 24px;
    }
    .prism-svg-container {
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 10px 0;
    }
    .visualizer-tip {
      display: flex;
      justify-content: space-between;
      align-items: center;
      background-color: rgba(255,255,255,0.02);
      padding: 10px 14px;
      border-radius: 8px;
      border: 1px solid rgba(255,255,255,0.04);
      font-size: 11px;
      color: rgba(255,255,255,0.5);
    }
    .btn-clear-filter {
      background: rgba(108, 92, 231, 0.2);
      border: 1px solid rgba(108, 92, 231, 0.4);
      color: #a29bfe;
      border-radius: 4px;
      padding: 2px 8px;
      cursor: pointer;
      font-size: 10px;
      font-weight: bold;
    }
    .info-card {
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }
    .label-badge {
      font-size: 10px;
      font-weight: bold;
      border-radius: 4px;
      padding: 2px 8px;
      font-family: var(--font-mono);
    }
    .info-content {
      display: flex;
      flex-direction: column;
      gap: 12px;
      margin: 16px 0;
    }
    .info-agent-row {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .status-dot-large {
      width: 12px;
      height: 12px;
      border-radius: 50%;
    }
    .info-agent-row h3 {
      margin: 0;
      font-size: 17px;
      font-weight: bold;
      color: #fff;
    }
    .info-role-text {
      font-size: 12px;
      color: rgba(255,255,255,0.4);
    }
    .info-task-box {
      background-color: rgba(0,0,0,0.2);
      border: 1px solid rgba(255,255,255,0.04);
      border-radius: 8px;
      padding: 12px;
      font-size: 13px;
      line-height: 1.4;
    }
    .task-box-header {
      display: flex;
      justify-content: space-between;
      margin-bottom: 6px;
      font-size: 10px;
      font-weight: bold;
      color: rgba(255,255,255,0.4);
      letter-spacing: 0.5px;
    }
    .status-label {
      font-weight: bold;
    }
    .status-label.idle { color: var(--color-fred); }
    .status-label.working { color: var(--color-kai); }
    .status-label.error { color: var(--color-codex); }
    .status-label.offline { color: rgba(255,255,255,0.3); }
    .task-box-body {
      color: #e2e8f0;
      font-family: var(--font-mono);
      font-size: 12px;
    }
    .info-meta-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      font-size: 12px;
    }
    .meta-lbl {
      display: block;
      color: rgba(255,255,255,0.4);
      margin-bottom: 2px;
    }
    .meta-val {
      color: #fff;
      font-weight: 500;
    }
    .meta-val.code {
      font-family: var(--font-mono);
    }
    .info-placeholder {
      height: 140px;
      display: flex;
      align-items: center;
      justify-content: center;
      border: 1px dashed rgba(255,255,255,0.1);
      border-radius: 8px;
      color: rgba(255,255,255,0.4);
      font-size: 13px;
      text-align: center;
      padding: 0 20px;
      margin: 16px 0;
    }
    .info-footer-config {
      border-top: 1px solid rgba(255,255,255,0.06);
      padding-top: 16px;
    }
    .config-header {
      font-size: 10px;
      color: #6c5ce7;
      font-weight: bold;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      display: block;
      margin-bottom: 6px;
    }
    .config-pre {
      margin: 0;
      padding: 10px;
      background-color: #06060a;
      border: 1px solid rgba(255,255,255,0.06);
      border-radius: 6px;
      font-size: 11px;
      font-family: var(--font-mono);
      overflow-x: auto;
    }
    .config-pre.native { color: #ffd32a; }
    .config-pre.standalone { color: #4ecdc4; }

    /* Spokes Section */
    .spokes-section {
      margin-bottom: 24px;
    }
    .section-title {
      margin: 0 0 16px 0;
      font-size: 16px;
      color: #fff;
      font-weight: bold;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .agent-cards-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
      gap: 16px;
    }
    .agent-card {
      background-color: var(--bg-industrial);
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 12px;
      position: relative;
      overflow: hidden;
      transition: all 0.2s ease;
    }
    .agent-card.offline {
      opacity: 0.65;
    }
    .card-edge-tag {
      position: absolute;
      top: 0; left: 0;
      width: 4px; height: 100%;
    }
    .card-header-clickable {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 14px 14px 8px 18px;
      cursor: pointer;
    }
    .card-header-clickable:hover .agent-name-badge {
      text-decoration: underline;
    }
    .card-body-clickable {
      padding: 0 14px 14px 18px;
      cursor: pointer;
    }
    .agent-name-badge {
      font-weight: bold;
      font-size: 14px;
      color: #fff;
    }
    .status-tag {
      font-size: 9px;
      font-weight: bold;
      padding: 1px 5px;
      border-radius: 3px;
      text-transform: uppercase;
    }
    .status-tag.idle {
      background-color: rgba(52, 152, 219, 0.1);
      color: var(--color-fred);
    }
    .status-tag.working {
      background-color: rgba(46, 204, 113, 0.1);
      color: var(--color-kai);
      animation: pulse 1.5s infinite;
    }
    .status-tag.error {
      background-color: rgba(231, 76, 60, 0.1);
      color: var(--color-codex);
    }
    .status-tag.offline {
      background-color: rgba(255,255,255,0.05);
      color: rgba(255,255,255,0.4);
    }
    .agent-role {
      margin: 0 0 8px 0;
      font-size: 11px;
      color: rgba(255,255,255,0.5);
      height: 28px;
      overflow: hidden;
    }
    .agent-task-snippet {
      background-color: rgba(0,0,0,0.15);
      border-radius: 6px;
      padding: 8px;
      font-size: 11px;
      font-family: var(--font-mono);
      white-space: nowrap;
      text-overflow: ellipsis;
      overflow: hidden;
      color: rgba(255,255,255,0.4);
      border: 1px solid transparent;
    }
    .agent-task-snippet.active {
      color: #ffd32a;
      border-color: rgba(243, 156, 18, 0.2);
    }

    /* Dashboard Bottom Grid */
    .dashboard-grid {
      display: grid;
      grid-template-columns: 7fr 5fr;
      gap: 24px;
      margin-bottom: 24px;
    }

    /* Activity Feed */
    .activity-feed {
      display: flex;
      flex-direction: column;
      gap: 8px;
      max-height: 280px;
      overflow-y: auto;
      padding-right: 4px;
    }
    .activity-item {
      background-color: rgba(0,0,0,0.2);
      border: 1px solid rgba(255,255,255,0.04);
      border-left: 3px solid #6c5ce7;
      border-radius: 6px;
      padding: 10px 12px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 12px;
    }
    .activity-text-row {
      display: flex;
      align-items: center;
      gap: 8px;
      max-width: 80%;
    }
    .activity-item .time {
      font-size: 10px;
      font-family: var(--font-mono);
      color: rgba(255,255,255,0.4);
    }
    .activity-item .ref-code {
      font-size: 11px;
      color: #ffd32a;
      font-weight: bold;
    }
    .activity-item .source-badge {
      font-size: 9px;
      padding: 1px 5px;
      border-radius: 3px;
      background-color: rgba(108, 92, 231, 0.1);
      color: #a29bfe;
      border: 1px solid rgba(108, 92, 231, 0.2);
    }
    .activity-item .title {
      color: #fff;
      font-weight: 500;
      white-space: nowrap;
      text-overflow: ellipsis;
      overflow: hidden;
    }
    .route-badge {
      font-size: 10px;
      font-weight: bold;
      font-family: var(--font-mono);
      padding: 2px 6px;
      border-radius: 4px;
    }

    /* Compact Workspace Table */
    .workspace-table-container {
      overflow-x: auto;
    }
    .compact-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
      text-align: left;
    }
    .compact-table th {
      padding: 8px;
      color: rgba(255,255,255,0.4);
      font-weight: bold;
      border-bottom: 1px solid rgba(255,255,255,0.06);
    }
    .compact-table td {
      padding: 8px;
      border-bottom: 1px solid rgba(255,255,255,0.04);
    }
    .compact-table .ws-name {
      font-weight: bold;
      color: #fff;
    }
    .compact-table .ws-branch {
      font-family: var(--font-mono);
      color: rgba(255,255,255,0.6);
    }
    .compact-table .ws-status.connected { color: #2ECC71; }
    .compact-table .ws-status.stalled { color: #ffd32a; }

    /* SQLite Dedup Tiles */
    .dedup-metrics-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
      margin-bottom: 16px;
    }
    .metric-tile {
      background-color: #06060a;
      border: 1px solid rgba(255,255,255,0.04);
      border-radius: 8px;
      padding: 12px;
      text-align: center;
    }
    .tile-lbl {
      display: block;
      font-size: 10px;
      color: rgba(255,255,255,0.4);
      text-transform: uppercase;
      margin-bottom: 4px;
    }
    .tile-val {
      font-size: 20px;
      font-weight: bold;
      color: #fff;
      font-family: var(--font-mono);
    }
    .tile-val.suppressed {
      color: #ff6b6b;
    }
    .dedup-status-list {
      display: flex;
      flex-direction: column;
      gap: 8px;
      font-size: 12px;
      margin-bottom: 16px;
    }
    .dedup-row {
      display: flex;
      justify-content: space-between;
      border-bottom: 1px solid rgba(255,255,255,0.04);
      padding-bottom: 6px;
    }
    .btn-action-wide {
      width: 100%;
      background-color: rgba(108, 92, 231, 0.1);
      border: 1px solid rgba(108, 92, 231, 0.2);
      border-radius: 8px;
      color: #a29bfe;
      padding: 10px;
      font-size: 11px;
      font-weight: bold;
      cursor: pointer;
      transition: all 0.2s ease;
    }
    .btn-action-wide:hover {
      background-color: rgba(108, 92, 231, 0.2);
      border-color: rgba(108, 92, 231, 0.4);
      color: #fff;
    }

    /* Workspaces View Registry */
    .workspaces-layout {
      display: grid;
      grid-template-columns: 8fr 4fr;
      gap: 24px;
    }
    .select-branch {
      background: #06060a;
      border: 1px solid var(--border-subtle);
      border-radius: 4px;
      color: #a29bfe;
      font-size: 11px;
      padding: 3px 6px;
      font-family: var(--font-mono);
      outline: none;
      cursor: pointer;
    }
    .status-badge {
      font-size: 10px;
      font-weight: bold;
      padding: 2px 6px;
      border-radius: 4px;
    }
    .status-badge.connected {
      background-color: rgba(46, 204, 113, 0.1);
      color: #2ECC71;
    }
    .status-badge.stalled {
      background-color: rgba(243, 156, 18, 0.1);
      color: #F39C12;
    }
    .registry-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
      text-align: left;
    }
    .registry-table th {
      padding: 10px;
      color: rgba(255,255,255,0.4);
      font-weight: bold;
      border-bottom: 1px solid rgba(255,255,255,0.06);
    }
    .registry-table td {
      padding: 12px 10px;
      border-bottom: 1px solid rgba(255,255,255,0.04);
    }
    .btn-table-action {
      background: rgba(255,255,255,0.03);
      border: 1px solid rgba(255,255,255,0.08);
      color: #fff;
      border-radius: 4px;
      padding: 4px 8px;
      font-size: 10px;
      cursor: pointer;
      margin-right: 6px;
      transition: all 0.2s;
    }
    .btn-table-action:hover {
      background: rgba(255,255,255,0.08);
    }
    .btn-table-action.reconnect {
      border-color: var(--color-agy);
      color: var(--color-agy);
    }
    .btn-table-action.reconnect:hover {
      background-color: rgba(243, 156, 18, 0.08);
    }
    .btn-table-action.danger {
      color: #ff6b6b;
      border-color: rgba(255, 107, 107, 0.1);
    }
    .btn-table-action.danger:hover {
      background-color: rgba(255, 107, 107, 0.08);
    }
    .metrics-stack {
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .metric-row {
      display: flex;
      justify-content: space-between;
      font-size: 13px;
      border-bottom: 1px solid rgba(255,255,255,0.04);
      padding-bottom: 8px;
    }

    /* Skills View Registry */
    .skills-search-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 16px;
    }
    .skills-search-row h2 {
      margin: 0;
      font-size: 16px;
    }
    .search-input-box {
      display: flex;
      gap: 8px;
    }
    .install-input {
      background-color: #06060a;
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      padding: 6px 12px;
      color: #fff;
      font-size: 11px;
      width: 320px;
      outline: none;
    }
    .btn-install-skill {
      background-color: #6c5ce7;
      border: none;
      border-radius: 6px;
      color: #fff;
      padding: 6px 14px;
      font-size: 11px;
      font-weight: bold;
      cursor: pointer;
    }
    .skills-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 20px;
      margin-top: 16px;
    }
    .skill-card {
      background-color: var(--bg-industrial);
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 12px;
      padding: 16px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      transition: all 0.25s ease;
    }
    .skill-card.installed {
      border-color: rgba(108, 92, 231, 0.25);
      box-shadow: 0 4px 10px rgba(108, 92, 231, 0.05);
    }
    .skill-card-header {
      display: flex;
      justify-content: space-between;
      margin-bottom: 12px;
    }
    .skill-title {
      margin: 0 0 2px 0;
      font-size: 14px;
      font-weight: bold;
      color: #fff;
    }
    .skill-author {
      font-size: 10px;
      color: var(--text-secondary);
      font-weight: 500;
    }
    .skill-version {
      font-size: 11px;
      font-family: var(--font-mono);
      background-color: rgba(255,255,255,0.05);
      padding: 2px 6px;
      border-radius: 4px;
      color: rgba(255,255,255,0.8);
      align-self: flex-start;
    }
    .skill-desc {
      font-size: 12px;
      color: rgba(255,255,255,0.65);
      line-height: 1.4;
      margin: 0 0 16px 0;
      flex-grow: 1;
    }
    .skill-footer {
      border-top: 1px solid rgba(255,255,255,0.04);
      padding-top: 12px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .skill-status {
      font-size: 11px;
      color: rgba(255,255,255,0.4);
    }
    .btn-skill-toggle {
      border-radius: 6px;
      padding: 6px 12px;
      font-size: 11px;
      font-weight: bold;
      cursor: pointer;
      transition: all 0.2s;
    }
    .btn-skill-toggle.primary {
      background-color: rgba(108, 92, 231, 0.1);
      border: 1px solid rgba(108, 92, 231, 0.3);
      color: #a29bfe;
    }
    .btn-skill-toggle.primary:hover {
      background-color: #6c5ce7;
      color: #fff;
    }
    .btn-skill-toggle.danger {
      background-color: rgba(255, 107, 107, 0.1);
      border: 1px solid rgba(255, 107, 107, 0.3);
      color: #ff6b6b;
    }
    .btn-skill-toggle.danger:hover {
      background-color: #ff6b6b;
      color: #fff;
    }

    /* Signals Page */
    .signals-layout {
      display: grid;
      grid-template-columns: 8fr 4fr;
      gap: 24px;
    }
    .signals-main-panel {
      display: flex;
      flex-direction: column;
      gap: 24px;
    }
    .signals-log-feed {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .signals-log-item {
      background-color: rgba(0,0,0,0.2);
      border: 1px solid rgba(255,255,255,0.04);
      border-radius: 6px;
      padding: 12px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 12px;
    }
    .provider-tag {
      font-weight: bold;
      font-family: var(--font-mono);
      font-size: 11px;
    }
    .provider-tag.file { color: var(--color-kai); }
    .provider-tag.http { color: var(--color-fred); }
    .provider-tag.telegram { color: var(--color-custom); }
    .provider-text {
      color: var(--text-secondary);
      margin-left: 10px;
      flex-grow: 1;
    }
    .provider-time {
      color: rgba(255,255,255,0.3);
      font-size: 10px;
    }

    /* Sandbox Form */
    .sandbox-form-grid {
      display: grid;
      grid-template-columns: 1fr 2fr 1fr;
      gap: 16px;
      margin-bottom: 16px;
    }
    .form-control {
      width: 100%;
      padding: 8px 12px;
      background-color: #06060a;
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      color: #fff;
      font-size: 12px;
      outline: none;
      box-sizing: border-box;
      margin-top: 4px;
    }
    .form-control:focus {
      border-color: #6c5ce7;
    }
    .btn-sandbox-submit {
      width: 100%;
      padding: 12px;
      border-radius: 8px;
      border: none;
      color: #fff;
      font-weight: bold;
      font-size: 12px;
      cursor: pointer;
      box-shadow: 0 4px 12px rgba(108, 92, 231, 0.3);
      transition: all 0.3s ease;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      background: linear-gradient(135deg, #6c5ce7, #5f27cd);
    }
    .btn-sandbox-submit.routing {
      background: linear-gradient(135deg, #e67e22, #f39c12);
      cursor: not-allowed;
    }
    .btn-sandbox-submit.executing {
      background: linear-gradient(135deg, #2ecc71, #27ae60);
      cursor: not-allowed;
    }
    .btn-sandbox-submit.success {
      background: #27ae60;
      cursor: not-allowed;
    }
    .config-rows-stack {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .config-row {
      display: flex;
      flex-direction: column;
      gap: 4px;
      border-bottom: 1px solid rgba(255,255,255,0.04);
      padding-bottom: 8px;
    }
    .config-row .lbl {
      font-size: 11px;
      color: rgba(255,255,255,0.4);
    }
    .config-row .val {
      font-size: 13px;
      color: #fff;
    }
    .config-row .val.code {
      font-family: var(--font-mono);
      font-size: 12px;
    }
    .config-row .val.highlight {
      color: var(--color-fred);
      font-weight: bold;
    }

    /* Sliding Drawer Component */
    .drawer-wrapper {
      position: fixed;
      top: 0; left: 0;
      width: 100vw; height: 100vh;
      z-index: 10000;
      display: flex;
      justify-content: flex-end;
    }
    .drawer-overlay {
      position: absolute;
      top: 0; left: 0;
      width: 100%; height: 100%;
      background: rgba(0,0,0,0.65);
      backdrop-filter: blur(4px);
    }
    .drawer-container {
      position: relative;
      z-index: 1;
      width: 450px;
      max-width: 100%;
      height: 100%;
      background-color: #11141b;
      border-left: 1px solid var(--border-subtle);
      box-shadow: -10px 0 30px rgba(0,0,0,0.5);
      display: flex;
      flex-direction: column;
      animation: slideIn 0.25s ease-out;
    }
    .drawer-header {
      padding: 20px;
      border-bottom: 1px solid rgba(255,255,255,0.08);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .drawer-header-left {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .drawer-agent-badge {
      font-size: 20px;
      font-weight: bold;
      border: 1px solid;
      border-radius: 8px;
      width: 38px;
      height: 38px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .drawer-title {
      margin: 0;
      font-size: 18px;
      color: #fff;
      font-weight: bold;
    }
    .drawer-subtitle {
      font-size: 11px;
      color: var(--text-secondary);
    }
    .drawer-close-btn {
      background: transparent;
      border: none;
      color: rgba(255,255,255,0.5);
      font-size: 26px;
      cursor: pointer;
      padding: 0 4px;
    }
    .drawer-close-btn:hover {
      color: #fff;
    }
    .drawer-body {
      flex: 1;
      overflow-y: auto;
      padding: 20px;
      display: flex;
      flex-direction: column;
      gap: 24px;
    }
    .drawer-section h4 {
      margin: 0 0 12px 0;
      font-size: 11px;
      color: rgba(255,255,255,0.4);
      text-transform: uppercase;
      letter-spacing: 1px;
      border-left: 2px solid #6c5ce7;
      padding-left: 8px;
    }
    .metrics-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }
    .metric-box {
      background-color: rgba(0,0,0,0.25);
      border: 1px solid rgba(255,255,255,0.04);
      border-radius: 6px;
      padding: 10px;
    }
    .m-lbl {
      display: block;
      font-size: 10px;
      color: rgba(255,255,255,0.4);
      margin-bottom: 2px;
    }
    .m-val {
      font-size: 13px;
      font-weight: bold;
      color: #fff;
    }
    .m-val.status-color.idle { color: var(--color-fred); }
    .m-val.status-color.working { color: var(--color-kai); }
    .m-val.status-color.error { color: var(--color-codex); }
    .m-val.status-color.offline { color: rgba(255,255,255,0.3); }

    .queue-list {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    .queue-item {
      background-color: rgba(0,0,0,0.2);
      border: 1px solid rgba(255,255,255,0.04);
      border-radius: 6px;
      padding: 10px 12px;
    }
    .queue-item-header {
      display: flex;
      justify-content: space-between;
      margin-bottom: 4px;
    }
    .q-ref {
      font-size: 11px;
      color: #ffd32a;
      font-weight: bold;
      font-family: var(--font-mono);
    }
    .q-prio {
      font-size: 9px;
      text-transform: uppercase;
      font-weight: bold;
      padding: 1px 4px;
      border-radius: 3px;
    }
    .q-prio.high { background-color: rgba(231, 76, 60, 0.1); color: var(--color-codex); }
    .q-prio.medium { background-color: rgba(243, 156, 18, 0.1); color: var(--color-agy); }
    .q-prio.low { background-color: rgba(255,255,255,0.05); color: rgba(255,255,255,0.5); }
    .q-prio.urgent { background-color: rgba(155, 89, 182, 0.15); color: var(--color-ned); }
    .q-title {
      margin: 0;
      font-size: 12px;
      color: #fff;
    }
    .log-list {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .log-item {
      display: flex;
      justify-content: space-between;
      align-items: center;
      background-color: rgba(0,0,0,0.25);
      border-left: 3px solid transparent;
      border-radius: 4px;
      padding: 8px 12px;
      font-size: 11px;
    }
    .log-item.success { border-left-color: #2ECC71; }
    .log-item.error { border-left-color: #E74C3C; }
    .log-meta {
      display: flex;
      gap: 8px;
    }
    .log-item.success .badge { color: #2ECC71; font-weight: bold; }
    .log-item.error .badge { color: #E74C3C; font-weight: bold; }
    .log-item .duration { color: rgba(255,255,255,0.4); }
    .log-item .time { color: rgba(255,255,255,0.3); }

    .drawer-footer {
      padding: 20px;
      border-top: 1px solid rgba(255,255,255,0.08);
      display: flex;
      gap: 12px;
    }
    .drawer-btn-secondary {
      flex: 1;
      background: transparent;
      border: 1px solid rgba(255,255,255,0.15);
      border-radius: 6px;
      color: rgba(255,255,255,0.8);
      padding: 10px;
      font-size: 12px;
      font-weight: bold;
      cursor: pointer;
    }
    .drawer-btn-secondary:hover {
      background-color: rgba(255,255,255,0.04);
      color: #fff;
    }
    .drawer-btn-primary {
      flex: 1;
      background-color: #6c5ce7;
      border: none;
      border-radius: 6px;
      color: #fff;
      padding: 10px;
      font-size: 12px;
      font-weight: bold;
      cursor: pointer;
    }
    .drawer-btn-primary:hover:not(:disabled) {
      background-color: #5f27cd;
      box-shadow: 0 0 10px rgba(108, 92, 231, 0.4);
    }
    .drawer-btn-primary:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }

    /* Keyframes */
    @keyframes pulse {
      0% { transform: scale(0.95); opacity: 0.4; }
      50% { transform: scale(1.15); opacity: 0.9; }
      100% { transform: scale(0.95); opacity: 0.4; }
    }
    @keyframes card-pulse {
      from { box-shadow: 0 4px 10px rgba(108, 92, 231, 0.1); }
      to { box-shadow: 0 4px 25px rgba(108, 92, 231, 0.4); border-color: #6c5ce7; }
    }
    @keyframes slideIn {
      from { transform: translateX(100%); }
      to { transform: translateX(0); }
    }

    /* -------------------------------------------------- */
    /* RESPONSIVE LAYOUTS & BREAKPOINTS                  */
    /* -------------------------------------------------- */
    
    /* Desktop (>=1200px) */
    @media (min-width: 1200px) {
      .visualizer-grid {
        grid-template-columns: 7.2fr 4.8fr;
      }
      .dashboard-grid {
        grid-template-columns: 7.2fr 4.8fr;
      }
      .workspaces-layout {
        grid-template-columns: 8fr 4fr;
      }
      .signals-layout {
        grid-template-columns: 8fr 4fr;
      }
    }

    /* Tablet (768px - 1200px) */
    @media (max-width: 1200px) {
      .visualizer-grid {
        grid-template-columns: 1fr;
      }
      .agent-cards-grid {
        grid-template-columns: repeat(3, 1fr);
      }
      .dashboard-grid {
        grid-template-columns: 1fr;
      }
      .workspaces-layout {
        grid-template-columns: 1fr;
      }
      .signals-layout {
        grid-template-columns: 1fr;
      }
    }

    /* Mobile (<768px) */
    @media (max-width: 768px) {
      .hub-header {
        flex-direction: column;
        align-items: flex-start;
        gap: 12px;
      }
      .header-controls {
        width: 100%;
        justify-content: space-between;
      }
      .agent-cards-grid {
        grid-template-columns: repeat(2, 1fr);
      }
      .nav-tabs {
        flex-wrap: wrap;
      }
      .nav-tab {
        flex: 1 1 auto;
        text-align: center;
        padding: 8px 10px;
        font-size: 12px;
      }
      .drawer-container {
        width: 100% !important; /* Full width drawer on mobile */
      }
      .sandbox-form-grid {
        grid-template-columns: 1fr;
      }
    }

    /* Compact Mobile (<400px) */
    @media (max-width: 400px) {
      .hub-container {
        padding: 12px;
      }
      .agent-cards-grid {
        grid-template-columns: 1fr;
      }
      .agent-role {
        display: none; /* Hide role for compactness */
      }
      .card-header-clickable {
        padding: 10px 10px 6px 14px;
      }
      .card-body-clickable {
        padding: 0 10px 10px 14px;
      }
      .activity-item {
        flex-direction: column;
        align-items: flex-start;
        gap: 6px;
      }
      .activity-text-row {
        max-width: 100%;
      }
      .route-badge {
        align-self: flex-end;
      }
    }
  `;
  document.head.appendChild(styleEl);

  // Register the dashboard plugin extension
  registry.register('hermes-plugin-prismatic-hub', PrismaticHubApp);
  console.log('[prismatic-hub] Plugin successfully registered to registry.');
})();
