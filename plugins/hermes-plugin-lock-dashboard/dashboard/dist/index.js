(function () {
  const sdk = window.__HERMES_PLUGIN_SDK__;
  const registry = window.__HERMES_PLUGINS__;
  if (!sdk || !registry) return;

  const React = sdk.React;
  const { useState, useEffect, useRef, useCallback } = React;
  const h = React.createElement;

  // ── Agent Colors ────────────────────────────────────
  const AGENT_COLORS = {
    fred:        { bg: 'rgba(108,92,231,0.12)',  border: '#6c5ce7', text: '#a29bfe' },
    ned:         { bg: 'rgba(0,184,148,0.12)',   border: '#00b894', text: '#55efc4' },
    kai:         { bg: 'rgba(253,203,110,0.12)', border: '#fdcb6e', text: '#ffeaa7' },
    agy:         { bg: 'rgba(225,112,85,0.12)',  border: '#e17055', text: '#fab1a0' },
    jules:       { bg: 'rgba(116,185,255,0.12)', border: '#74b9ff', text: '#81ecec' },
    codex:       { bg: 'rgba(162,155,254,0.12)', border: '#a29bfe', text: '#dfe6e9' },
  };

  function getColor(name) {
    return AGENT_COLORS[(name || '').toLowerCase()] || { bg: 'rgba(255,255,255,0.06)', border: '#636e72', text: '#b2bec3' };
  }

  // ── Helpers ─────────────────────────────────────────
  const STALE_MS = 120000;  // 2 minutes = stale

  function formatDuration(ms) {
    if (ms < 1000) return Math.round(ms) + 'ms';
    if (ms < 60000) return (ms / 1000).toFixed(1) + 's';
    if (ms < 3600000) return Math.round(ms / 60000) + 'm';
    return Math.round(ms / 3600000) + 'h';
  }

  function isStale(lock) {
    const now = Date.now();
    const lastHb = (lock.lastHeartbeat || lock.timestamp || 0);
    return (now - lastHb) > STALE_MS;
  }

  function lockAge(lock) {
    const now = Date.now();
    const lastHb = (lock.lastHeartbeat || lock.timestamp || 0);
    return now - lastHb;
  }

  // ── API ─────────────────────────────────────────────
  async function fetchLocks() {
    try {
      // Try the SSE server lock endpoint first
      const resp = await fetch('http://127.0.0.1:8098/api/events/locks');
      if (resp.ok) return await resp.json();
    } catch {}
    // Fallback: try the recent events endpoint (last event might have lock snapshot)
    return [];
  }

  async function forceUnlock(filePath, agentId) {
    try {
      const resp = await fetch('http://127.0.0.1:8098/api/events/unlock', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filePath, agentId }),
      });
      return resp.ok;
    } catch {
      return false;
    }
  }

  // ── Main Component ──────────────────────────────────
  function LockDashboard() {
    const [locks, setLocks] = useState([]);
    const [polling, setPolling] = useState(true);
    const [confirmUnlock, setConfirmUnlock] = useState(null);
    const [statusMsg, setStatusMsg] = useState('');
    const intervalRef = useRef(null);

    // Poll lock state every 5 seconds
    useEffect(() => {
      const poll = async () => {
        const data = await fetchLocks();
        if (Array.isArray(data) && data.length > 0) {
          setLocks(data);
        }
      };
      poll();
      intervalRef.current = setInterval(poll, 5000);
      return () => clearInterval(intervalRef.current);
    }, []);

    const handleForceUnlock = useCallback(async (lock) => {
      if (confirmUnlock && confirmUnlock.filePath === lock.filePath) {
        // Confirmed — execute unlock
        const success = await forceUnlock(lock.filePath, lock.agentId);
        if (success) {
          setLocks(prev => prev.filter(l => l.filePath !== lock.filePath));
          setStatusMsg(`🔓 Unlocked: ${lock.filePath}`);
          setTimeout(() => setStatusMsg(''), 3000);
        } else {
          setStatusMsg(`❌ Failed to unlock: ${lock.filePath}`);
          setTimeout(() => setStatusMsg(''), 3000);
        }
        setConfirmUnlock(null);
      } else {
        // First click — ask for confirmation
        setConfirmUnlock(lock);
        setTimeout(() => setConfirmUnlock(null), 5000);
      }
    }, [confirmUnlock]);

    const activeLocks = locks.filter(l => !isStale(l));
    const staleLocks = locks.filter(l => isStale(l));
    const agentCounts = {};
    activeLocks.forEach(l => {
      const a = l.agentId || 'unknown';
      agentCounts[a] = (agentCounts[a] || 0) + 1;
    });

    return h('div', {
      style: {
        padding: '16px 24px',
        maxWidth: 1200,
        margin: '0 auto',
        fontFamily: '"Outfit", "Inter", -apple-system, sans-serif',
        color: '#e0e0e0',
        minHeight: '100vh',
      }
    },
      // ── Header ──────────────────────────────────────
      h('div', { style: { marginBottom: 20 } },
        h('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 } },
          h('h1', { style: { margin: 0, fontSize: 22, fontWeight: 800, color: '#fff' } }, '🔒 Swarm Lock Dashboard'),
          h('div', { style: { display: 'flex', gap: 8, alignItems: 'center' } },
            h('span', { style: { fontSize: 12, opacity: 0.5 } }, polling ? '🔄 Polling 5s' : '⏹ Paused'),
            h('button', {
              onClick: () => setPolling(!polling),
              style: {
                padding: '4px 10px', fontSize: 11, fontWeight: 600,
                borderRadius: 6, border: '1px solid rgba(255,255,255,0.1)',
                background: 'rgba(255,255,255,0.04)', color: '#999', cursor: 'pointer',
              }
            }, polling ? 'Pause' : 'Resume')
          )
        ),
        statusMsg && h('div', {
          style: {
            marginTop: 8, padding: '8px 12px', borderRadius: 8,
            background: 'rgba(0,184,148,0.1)', border: '1px solid rgba(0,184,148,0.2)',
            fontSize: 13, fontWeight: 600,
          }
        }, statusMsg)
      ),

      // ── Stats Row ────────────────────────────────────
      h('div', {
        style: {
          display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
          gap: 10, marginBottom: 20,
        }
      },
        h(StatCard, { label: 'Active Locks', value: activeLocks.length, color: '#6c5ce7' }),
        h(StatCard, { label: 'Stale Locks', value: staleLocks.length, color: staleLocks.length > 0 ? '#e17055' : '#00b894' }),
        h(StatCard, { label: 'Locked Files', value: [...new Set(activeLocks.map(l => l.filePath))].length, color: '#fdcb6e' }),
        h(StatCard, { label: 'Lock Holders', value: Object.keys(agentCounts).length, color: '#74b9ff' }),
      ),

      // ── Agent Summary ────────────────────────────────
      Object.keys(agentCounts).length > 0 && h('div', {
        style: { display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }
      },
        Object.entries(agentCounts).map(([agent, count]) => {
          const c = getColor(agent);
          return h('div', {
            key: agent,
            style: {
              padding: '6px 12px', borderRadius: 20,
              background: c.bg, border: `1px solid ${c.border}33`,
              fontSize: 12, fontWeight: 600, color: c.text,
            }
          }, `${agent}: ${count} lock${count !== 1 ? 's' : ''}`);
        })
      ),

      // ── Lock List ────────────────────────────────────
      activeLocks.length === 0
        ? h('div', {
            style: {
              padding: 60, textAlign: 'center', opacity: 0.4, fontSize: 14,
            }
          }, '🔓 No active locks. All clear.')
        : h('div', {
            style: {
              display: 'flex', flexDirection: 'column', gap: 8,
            }
          },
            activeLocks.sort((a, b) => (b.lastHeartbeat || b.timestamp) - (a.lastHeartbeat || a.timestamp)).map(lock => {
              const age = lockAge(lock);
              const stale = isStale(lock);
              const c = getColor(lock.agentId);
              const confirming = confirmUnlock && confirmUnlock.filePath === lock.filePath;
              return h('div', {
                key: lock.filePath + lock.agentId,
                style: {
                  display: 'flex', alignItems: 'center', gap: 12,
                  padding: '10px 14px', borderRadius: 12,
                  background: stale ? 'rgba(225,112,85,0.06)' : c.bg,
                  border: `1px solid ${stale ? 'rgba(225,112,85,0.25)' : c.border + '22'}`,
                  transition: 'all 0.2s',
                }
              },
                // File icon + path
                h('div', {
                  style: {
                    flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 8,
                  }
                },
                  h('span', { style: { fontSize: 16, flexShrink: 0 } }, '📄'),
                  h('span', {
                    style: {
                      fontFamily: 'monospace', fontSize: 12, color: '#ccc',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }
                  }, lock.filePath || 'unknown')
                ),
                // Agent badge
                h('span', {
                  style: {
                    padding: '2px 8px', borderRadius: 6, fontSize: 11, fontWeight: 700,
                    background: c.bg, color: c.text, border: `1px solid ${c.border}33`,
                    whiteSpace: 'nowrap',
                  }
                }, lock.agentId || 'unknown'),
                // Age
                h('span', {
                  style: {
                    fontSize: 11, opacity: stale ? 1 : 0.5, color: stale ? '#e17055' : '#999',
                    fontWeight: stale ? 700 : 400, whiteSpace: 'nowrap',
                  }
                }, stale ? '⚠ ' + formatDuration(age) : formatDuration(age)),
                // Force unlock button
                h('button', {
                  onClick: () => handleForceUnlock(lock),
                  style: {
                    padding: '4px 10px', fontSize: 10, fontWeight: 600,
                    borderRadius: 6, border: 'none', cursor: 'pointer',
                    background: confirming ? 'rgba(225,112,85,0.25)' : 'rgba(225,112,85,0.1)',
                    color: confirming ? '#e17055' : '#fab1a0',
                    whiteSpace: 'nowrap',
                  }
                }, confirming ? 'Confirm?' : '🗑 Unlock')
              );
            })
          ),

      // ── Stale Locks Section ──────────────────────────
      staleLocks.length > 0 && h('div', { style: { marginTop: 24 } },
        h('h3', {
          style: { fontSize: 14, opacity: 0.6, marginBottom: 8 }
        }, `⚠ Stale Locks (${staleLocks.length})`),
        h('div', { style: { fontSize: 11, opacity: 0.4, marginBottom: 12 } },
          'These locks have no heartbeat >2min. They will auto-release on next lock operation.'
        ),
        staleLocks.map(lock => {
          const age = lockAge(lock);
          return h('div', {
            key: 'stale-' + lock.filePath,
            style: {
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '6px 12px', borderRadius: 8,
              background: 'rgba(225,112,85,0.05)', border: '1px solid rgba(225,112,85,0.12)',
              marginBottom: 4,
            }
          },
            h('span', { style: { fontFamily: 'monospace', fontSize: 11, opacity: 0.6, flex: 1 } }, lock.filePath),
            h('span', { style: { fontSize: 10, opacity: 0.4 } }, lock.agentId),
            h('span', { style: { fontSize: 10, color: '#e17055' } }, formatDuration(age) + ' stale'),
            h('button', {
              onClick: () => handleForceUnlock(lock),
              style: {
                padding: '2px 8px', fontSize: 9, borderRadius: 4,
                border: '1px solid rgba(225,112,85,0.2)', background: 'transparent',
                color: '#fab1a0', cursor: 'pointer',
              }
            }, 'Clear')
          );
        })
      ),

      // ── CSS ──────────────────────────────────────────
      h('style', null, `
        @media (max-width: 768px) {
          .lock-row { flex-wrap: wrap; }
        }
      `)
    );
  }

  // Stat card helper component
  function StatCard({ label, value, color }) {
    return h('div', {
      style: {
        padding: '12px 14px', borderRadius: 12,
        background: 'rgba(255,255,255,0.02)',
        border: '1px solid rgba(255,255,255,0.05)',
      }
    },
      h('div', { style: { fontSize: 11, opacity: 0.5, marginBottom: 4 } }, label),
      h('div', { style: { fontSize: 28, fontWeight: 800, color } }, value)
    );
  }

  registry.register('hermes-plugin-lock-dashboard', LockDashboard);
})();
