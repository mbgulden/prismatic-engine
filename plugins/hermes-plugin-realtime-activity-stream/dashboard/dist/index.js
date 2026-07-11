(function () {
  const sdk = window.__HERMES_PLUGIN_SDK__;
  const registry = window.__HERMES_PLUGINS__;
  if (!sdk || !registry) return;

  const React = sdk.React;
  const { useState, useEffect, useRef, useCallback } = React;
  const h = React.createElement;

  // ── Agent color map ───────────────────────────────
  const AGENT_COLORS = {
    fred:        { bg: 'rgba(108,92,231,0.12)',  border: '#6c5ce7', text: '#a29bfe', dot: '#6c5ce7' },
    ned:         { bg: 'rgba(0,184,148,0.12)',   border: '#00b894', text: '#55efc4', dot: '#00b894' },
    kai:         { bg: 'rgba(253,203,110,0.12)', border: '#fdcb6e', text: '#ffeaa7', dot: '#fdcb6e' },
    agy:         { bg: 'rgba(225,112,85,0.12)',  border: '#e17055', text: '#fab1a0', dot: '#e17055' },
    jules:       { bg: 'rgba(116,185,255,0.12)', border: '#74b9ff', text: '#81ecec', dot: '#74b9ff' },
    codex:       { bg: 'rgba(162,155,254,0.12)', border: '#a29bfe', text: '#dfe6e9', dot: '#a29bfe' },
    orchestrator:{ bg: 'rgba(255,234,167,0.12)', border: '#ffeaa7', text: '#ffeaa7', dot: '#ffeaa7' },
  };

  function getAgentColor(name) {
    const key = (name || '').toLowerCase();
    return AGENT_COLORS[key] || { bg: 'rgba(255,255,255,0.06)', border: '#636e72', text: '#b2bec3', dot: '#636e72' };
  }

  // ── Status icon map ───────────────────────────────
  const STATUS_ICONS = {
    launched:  '🚀',
    completed: '✅',
    error:     '❌',
    stalled:   '⚠️',
    running:   '▶️',
    stopped:   '⏹️',
  };

  function formatTime(iso) {
    if (!iso) return '';
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch {
      return iso;
    }
  }

  function formatRelative(iso) {
    if (!iso) return '';
    try {
      const ms = Date.now() - new Date(iso).getTime();
      if (ms < 60000) return Math.round(ms / 1000) + 's ago';
      if (ms < 3600000) return Math.round(ms / 60000) + 'm ago';
      return Math.round(ms / 3600000) + 'h ago';
    } catch {
      return '';
    }
  }

  // ── Main Component ──────────────────────────────────
  function ActivityStream() {
    const isMobile = typeof window !== 'undefined' && (
      /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent) ||
      window.innerWidth <= 768
    );

    function normalizeEvent(e) {
      if (!e) return null;
      const payload = e.payload || {};
      const timestamp = e.timestamp || e.ts || '';
      const agentName = e.agent_name || payload.agent || (e.source && e.source.split(':')[1]) || 'unknown';
      const title = e.title || payload.title || '';
      const message = e.message || payload.message || '';
      const id = e._event_id || e.rowid || `${timestamp}-${agentName}-${title}`;
      return {
        _event_id: id,
        event: e.event || e.type || 'unknown',
        agent_name: agentName,
        issue_id: e.issue_id || payload.issue_id || '',
        timestamp: timestamp,
        title: title,
        message: message
      };
    }

    const getInitialEvents = () => {
      if (isMobile) {
        try {
          const saved = localStorage.getItem('prismatic_stream_events');
          if (saved) {
            const parsed = JSON.parse(saved);
            if (Array.isArray(parsed)) {
              return parsed.map(normalizeEvent).filter(Boolean);
            }
          }
        } catch (err) {
          console.error('Failed to load initial events:', err);
        }
      }
      return [];
    };

    const getInitialFilter = () => {
      if (isMobile) {
        try {
          const saved = localStorage.getItem('prismatic_stream_filter');
          if (saved) return saved;
        } catch (err) {}
      }
      return 'all';
    };

    const getInitialPaused = () => {
      if (isMobile) {
        try {
          const saved = localStorage.getItem('prismatic_stream_paused');
          if (saved) return JSON.parse(saved);
        } catch (err) {}
      }
      return false;
    };

    const [events, setEvents] = useState(getInitialEvents);
    const [filterAgent, setFilterAgent] = useState(getInitialFilter);
    const [paused, setPaused] = useState(getInitialPaused);
    const [connected, setConnected] = useState(false);
    const scrollRef = useRef(null);
    const eventSourceRef = useRef(null);

    const handleIncomingEvent = useCallback((rawEvent) => {
      const norm = normalizeEvent(rawEvent);
      if (!norm) return;
      setEvents(prev => {
        if (prev.some(x => x._event_id === norm._event_id)) {
          return prev;
        }
        const merged = [norm, ...prev].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
        return merged.slice(0, 200);
      });
    }, []);

    // Connect to SSE
    useEffect(() => {
      const host = window.location.hostname || '127.0.0.1';
      const port = window.location.port || '8098';
      const url = `http://${host}:${port}/api/events/stream`;
      const es = new EventSource(url);
      eventSourceRef.current = es;

      const fetchHistory = async () => {
        const historyUrls = [
          `http://${host}:${port}/api/events/history?limit=100`,
          `http://${host}:${port}/events/history?limit=100`,
        ];
        try {
          let data = null;
          for (const historyUrl of historyUrls) {
            const resp = await fetch(historyUrl);
            if (resp.ok) {
              data = await resp.json();
              break;
            }
          }
          if (data && Array.isArray(data.events)) {
            setEvents(prev => {
              const map = new Map();
              prev.forEach(ev => map.set(ev._event_id, ev));
              data.events.forEach(item => {
                const norm = normalizeEvent(item);
                if (norm) map.set(norm._event_id, norm);
              });
              const merged = Array.from(map.values()).sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
              return merged.slice(0, 200);
            });
          }
        } catch (err) {
          console.error('Failed to fetch event history:', err);
        }
      };

      es.onopen = () => {
        setConnected(true);
        fetchHistory();
      };

      es.addEventListener('launched', (e) => {
        try { handleIncomingEvent(JSON.parse(e.data)); } catch {}
      });

      es.addEventListener('completed', (e) => {
        try { handleIncomingEvent(JSON.parse(e.data)); } catch {}
      });

      es.addEventListener('error', (e) => {
        if (e.data) {
          try { handleIncomingEvent(JSON.parse(e.data)); } catch {}
        }
      });

      es.addEventListener('stalled', (e) => {
        try { handleIncomingEvent(JSON.parse(e.data)); } catch {}
      });

      es.onmessage = (e) => {
        try { handleIncomingEvent(JSON.parse(e.data)); } catch {}
      };

      es.onerror = () => {
        setConnected(false);
      };

      return () => {
        es.close();
        eventSourceRef.current = null;
      };
    }, [handleIncomingEvent]);

    // For mobile clients, save session state and events to localStorage
    useEffect(() => {
      if (isMobile) {
        try {
          localStorage.setItem('prismatic_stream_events', JSON.stringify(events));
          localStorage.setItem('prismatic_stream_filter', filterAgent);
          localStorage.setItem('prismatic_stream_paused', JSON.stringify(paused));
        } catch (e) {
          console.error('Failed to save session state:', e);
        }
      }
    }, [events, filterAgent, paused, isMobile]);

    // Restore scroll position on mobile reconnect/mount
    useEffect(() => {
      if (isMobile && connected && scrollRef.current) {
        const timer = setTimeout(() => {
          try {
            const savedScroll = localStorage.getItem('prismatic_stream_scroll');
            if (savedScroll) {
              scrollRef.current.scrollTop = parseInt(savedScroll, 10);
            }
          } catch (e) {}
        }, 100);
        return () => clearTimeout(timer);
      }
    }, [connected, isMobile]);

    // Auto-scroll to top (newest first) for desktop
    useEffect(() => {
      if (!isMobile && !paused && scrollRef.current) {
        scrollRef.current.scrollTop = 0;
      }
    }, [events, paused, isMobile]);

    // Get unique agents for filter
    const agents = [...new Set(events.map(e => (e.agent_name || 'unknown').toLowerCase()))].sort();

    // Filter events
    const filtered = filterAgent === 'all'
      ? events
      : events.filter(e => (e.agent_name || '').toLowerCase() === filterAgent);

    // Agent summary cards
    const agentSummaries = {};
    events.forEach(e => {
      const name = (e.agent_name || 'unknown').toLowerCase();
      if (!agentSummaries[name]) {
        agentSummaries[name] = { agent: name, lastEvent: e.event, lastTime: e.timestamp, taskCount: 0 };
      }
      agentSummaries[name].taskCount++;
    });

    return h('div', {
      style: {
        padding: '16px 24px',
        maxWidth: 1400,
        margin: '0 auto',
        fontFamily: '"Outfit", "Inter", -apple-system, sans-serif',
        color: '#e0e0e0',
        minHeight: '100vh',
      }
    },

      // ── Header ──────────────────────────────────────
      h('div', { style: { marginBottom: 20, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 12 } },
        h('div', null,
          h('h1', { style: { margin: 0, fontSize: 22, fontWeight: 800, color: '#fff' } }, '⚡ Agent Activity Stream'),
          h('div', { style: { fontSize: 12, opacity: 0.5, marginTop: 4 } },
            connected ? '🟢 Live' : '🔴 Disconnected',
            ' · ', events.length, ' events'
          )
        ),
        h('div', { style: { display: 'flex', gap: 8, alignItems: 'center' } },
          // Pause toggle
          h('button', {
            onClick: () => setPaused(!paused),
            style: {
              padding: '6px 12px', fontSize: 12, fontWeight: 600,
              borderRadius: 8, border: '1px solid rgba(255,255,255,0.12)',
              background: paused ? 'rgba(225,112,85,0.15)' : 'rgba(255,255,255,0.04)',
              color: paused ? '#e17055' : '#999', cursor: 'pointer',
            }
          }, paused ? '⏸ Paused' : '▶ Live'),
          // Filter dropdown
          h('select', {
            value: filterAgent,
            onChange: (e) => setFilterAgent(e.target.value),
            style: {
              padding: '6px 12px', fontSize: 12, fontWeight: 600,
              borderRadius: 8, border: '1px solid rgba(255,255,255,0.12)',
              background: 'rgba(255,255,255,0.04)', color: '#ccc', cursor: 'pointer',
              outline: 'none',
            }
          },
            h('option', { value: 'all' }, 'All Agents'),
            ...agents.map(a => h('option', { key: a, value: a }, a.charAt(0).toUpperCase() + a.slice(1)))
          )
        )
      ),

      // ── Agent summary cards ──────────────────────────
      h('div', {
        className: 'as-summary-grid',
        style: {
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
          gap: 10, marginBottom: 20,
        }
      },
        Object.values(agentSummaries).slice(0, 10).map(summary => {
          const color = getAgentColor(summary.agent);
          return h('div', {
            key: summary.agent,
            onClick: () => setFilterAgent(summary.agent),
            style: {
              padding: '12px 14px',
              borderRadius: 12,
              background: color.bg,
              border: `1px solid ${color.border}22`,
              cursor: 'pointer',
              transition: 'all 0.2s',
            }
          },
            h('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' } },
              h('span', { style: { fontWeight: 700, fontSize: 14, color: color.text } }, summary.agent),
              h('span', { style: { fontSize: 12, opacity: 0.6 } }, formatRelative(summary.lastTime))
            ),
            h('div', { style: { fontSize: 11, opacity: 0.5, marginTop: 4 } },
              summary.taskCount, ' event', summary.taskCount !== 1 ? 's' : '',
              ' · ', summary.lastEvent
            )
          );
        })
      ),

      // ── Reconnecting Banner ──────────────────────────
      !connected && h('div', {
        style: {
          background: 'rgba(241, 196, 15, 0.12)',
          border: '1px solid rgba(241, 196, 15, 0.25)',
          color: '#ffd32a',
          padding: '10px 16px',
          borderRadius: 8,
          marginBottom: 12,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          fontSize: 12,
          fontWeight: 'bold',
          fontFamily: '"Outfit", "Inter", sans-serif',
          animation: 'pulse 1.5s infinite alternate ease-in-out'
        }
      },
        h('div', { style: { display: 'flex', alignItems: 'center', gap: 8 } },
          h('span', {
            style: {
              width: 8,
              height: 8,
              borderRadius: '50%',
              backgroundColor: '#ffd32a',
              display: 'inline-block',
              boxShadow: '0 0 8px #ffd32a'
            }
          }),
          'Connection lost. Reconnecting...'
        ),
        h('span', { style: { fontSize: 10, opacity: 0.6 } }, 'handshake (GRO-711)')
      ),

      // ── Event feed ───────────────────────────────────
      h('div', {
        ref: scrollRef,
        onMouseEnter: () => setPaused(true),
        onMouseLeave: () => setPaused(false),
        onScroll: () => {
          if (isMobile && scrollRef.current) {
            try {
              localStorage.setItem('prismatic_stream_scroll', scrollRef.current.scrollTop.toString());
            } catch (err) {}
          }
        },
        style: {
          maxHeight: 'calc(100vh - 320px)',
          overflowY: 'auto',
          borderRadius: 16,
          background: '#0a0a10',
          border: '1px solid rgba(255,255,255,0.05)',
          padding: 0,
        }
      },
        filtered.length === 0
          ? h('div', {
              style: {
                padding: 40, textAlign: 'center', opacity: 0.4, fontSize: 14,
              }
            }, 'Waiting for agent events...')
          : filtered.map((event, i) => {
              const color = getAgentColor(event.agent_name);
              const icon = STATUS_ICONS[event.event] || '📌';
              return h('div', {
                key: event._event_id || i,
                style: {
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 12,
                  padding: '10px 16px',
                  borderBottom: '1px solid rgba(255,255,255,0.03)',
                  transition: 'background 0.3s',
                  background: i === 0 ? 'rgba(255, 255, 255, 0.02)' : 'transparent',
                }
              },
                // Status icon
                h('div', { style: { fontSize: 16, flexShrink: 0, marginTop: 2 } }, icon),
                // Content
                h('div', { style: { flex: 1, minWidth: 0 } },
                  h('div', { style: { display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' } },
                    h('span', {
                      style: {
                        fontWeight: 700, fontSize: 13, color: color.text,
                        padding: '1px 6px', borderRadius: 4,
                        background: color.bg,
                      }
                    }, event.agent_name || 'unknown'),
                    event.issue_id && h('span', {
                      style: {
                        fontSize: 11, opacity: 0.5,
                        fontFamily: 'monospace',
                      }
                    }, event.issue_id),
                    h('span', {
                      style: {
                        fontSize: 11, opacity: 0.4,
                        marginLeft: 'auto',
                      }
                    }, formatRelative(event.timestamp))
                  ),
                  event.title && h('div', {
                    style: { fontSize: 13, marginTop: 3, color: '#ccc' }
                  }, event.title),
                  event.message && h('div', {
                    style: { fontSize: 11, marginTop: 2, opacity: 0.5, fontFamily: 'monospace' }
                  }, event.message)
                ),
                // Timestamp
                h('div', {
                  style: {
                    fontSize: 10, opacity: 0.35, flexShrink: 0,
                    fontFamily: 'monospace', marginTop: 2,
                    display: 'none',  // hidden on desktop, show on mobile via media query would be ideal
                  }
                }, formatTime(event.timestamp))
              );
            })
      ),

      // ── Mobile-only compact cards & Animations ────────
      h('style', null, `
        @keyframes pulse {
          0% { opacity: 0.6; }
          100% { opacity: 1; }
        }
        @media (max-width: 768px) {
          .as-summary-grid {
            grid-template-columns: repeat(2, 1fr) !important;
          }
        }
        @media (max-width: 480px) {
          .as-summary-grid {
            grid-template-columns: 1fr !important;
          }
        }
      `)
    );
  }

  registry.register('hermes-plugin-realtime-activity-stream', ActivityStream);
})();
