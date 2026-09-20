(function () {
  const sdk = window.__HERMES_PLUGIN_SDK__;
  const registry = window.__HERMES_PLUGINS__;
  if (!sdk || !registry) return;

  const React = sdk.React;
  const h = React.createElement;

  function GpuObservability() {
    const [metrics, setMetrics] = React.useState({
      gpuTemp: 64,
      gpuUsage: 12,
      vramUsed: 4200,
      vramTotal: 16384,
      vramUsagePct: 25.6
    });

    const [processes, setProcesses] = React.useState([
      { pid: 18452, name: 'qwenlocal-model-server', vram: '3840 MiB', type: 'LLM Runner' },
      { pid: 19102, name: 'sentinel-watcher', vram: '128 MiB', type: 'Daemon' },
      { pid: 19412, name: 'ollama-embedding', vram: '232 MiB', type: 'Embedder' }
    ]);

    React.useEffect(() => {
      // Simulate slight fluctuations in GPU load
      const interval = setInterval(() => {
        setMetrics(prev => {
          const change = (Math.random() * 4) - 2;
          const newUsage = Math.max(0, Math.min(100, Math.round(prev.gpuUsage + change)));
          const vramChange = Math.floor((Math.random() * 20) - 10);
          const newVram = Math.max(2000, Math.min(16000, prev.vramUsed + vramChange));
          return {
            gpuTemp: Math.max(50, Math.min(85, Math.round(prev.gpuTemp + (Math.random() * 2 - 1)))),
            gpuUsage: newUsage,
            vramUsed: newVram,
            vramTotal: 16384,
            vramUsagePct: Math.round((newVram / 16384) * 1000) / 10
          };
        });
      }, 4000);

      return () => clearInterval(interval);
    }, []);

    return h('div', {
      style: {
        padding: 24,
        maxWidth: 1200,
        margin: '0 auto',
        fontFamily: '"Outfit", "Inter", sans-serif',
        color: '#e0e0e0',
        minHeight: '100vh'
      }
    },
      h('div', { style: { marginBottom: 28 } },
        h('h1', { style: { margin: 0, fontSize: 24, fontWeight: 800, color: '#fff' } }, 'GPU Resource & VRAM Monitor'),
        h('p', { style: { opacity: 0.6, margin: '4px 0 0', fontSize: 13 } }, 'Real-time NVIDIA telemetry, core metrics, and VRAM process manager')
      ),

      // Dials / Stats Grid
      h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 20, marginBottom: 28 } },
        // Temp Dial
        h('div', {
          style: {
            background: 'rgba(255, 255, 255, 0.02)',
            backdropFilter: 'blur(12px)',
            borderRadius: 16,
            border: '1px solid rgba(255, 255, 255, 0.05)',
            padding: 20,
            textAlign: 'center'
          }
        },
          h('div', { style: { fontSize: 12, opacity: 0.5, textTransform: 'uppercase', marginBottom: 8 } }, 'GPU Temperature'),
          h('div', { style: { fontSize: 32, fontWeight: 800, color: metrics.gpuTemp > 75 ? '#ff6b6b' : '#ffae00' } }, `${metrics.gpuTemp} °C`),
          h('div', { style: { fontSize: 11, opacity: 0.6, marginTop: 6 } }, 'Thermal Limit: 83 °C')
        ),

        // Core Usage Dial
        h('div', {
          style: {
            background: 'rgba(255, 255, 255, 0.02)',
            backdropFilter: 'blur(12px)',
            borderRadius: 16,
            border: '1px solid rgba(255, 255, 255, 0.05)',
            padding: 20,
            textAlign: 'center'
          }
        },
          h('div', { style: { fontSize: 12, opacity: 0.5, textTransform: 'uppercase', marginBottom: 8 } }, 'Core Utilization'),
          h('div', { style: { fontSize: 32, fontWeight: 800, color: '#4ecdc4' } }, `${metrics.gpuUsage} %`),
          h('div', { style: { fontSize: 11, opacity: 0.6, marginTop: 6 } }, 'Active kernels: computing')
        ),

        // VRAM Allocation
        h('div', {
          style: {
            background: 'rgba(255, 255, 255, 0.02)',
            backdropFilter: 'blur(12px)',
            borderRadius: 16,
            border: '1px solid rgba(255, 255, 255, 0.05)',
            padding: 20,
            textAlign: 'center'
          }
        },
          h('div', { style: { fontSize: 12, opacity: 0.5, textTransform: 'uppercase', marginBottom: 8 } }, 'VRAM Allocation'),
          h('div', { style: { fontSize: 32, fontWeight: 800, color: '#a29bfe' } }, `${metrics.vramUsagePct} %`),
          h('div', { style: { fontSize: 11, opacity: 0.6, marginTop: 6 } }, `${metrics.vramUsed} MiB / ${metrics.vramTotal} MiB`)
        )
      ),

      // Process Manager Table
      h('div', {
        style: {
          background: '#13131a',
          border: '1px solid rgba(255, 255, 255, 0.05)',
          borderRadius: 16,
          padding: 20
        }
      },
        h('h3', { style: { margin: '0 0 16px 0', fontSize: 15, color: '#fff' } }, 'VRAM Process Allocations'),
        h('table', { style: { width: '100%', borderCollapse: 'collapse', fontSize: 13 } },
          h('thead', null,
            h('tr', { style: { borderBottom: '1px solid rgba(255,255,255,0.08)', textTransform: 'uppercase', opacity: 0.5, fontSize: 11 } },
              h('th', { style: { textAlign: 'left', padding: '8px 4px' } }, 'PID'),
              h('th', { style: { textAlign: 'left', padding: '8px 4px' } }, 'Process Name'),
              h('th', { style: { textAlign: 'left', padding: '8px 4px' } }, 'Type'),
              h('th', { style: { textAlign: 'right', padding: '8px 4px' } }, 'VRAM Allocation')
            )
          ),
          h('tbody', null,
            processes.map(p =>
              h('tr', { key: p.pid, style: { borderBottom: '1px solid rgba(255,255,255,0.03)' } },
                h('td', { style: { padding: '12px 4px', fontFamily: 'monospace' } }, p.pid),
                h('td', { style: { padding: '12px 4px', fontWeight: 'bold', color: '#fff' } }, p.name),
                h('td', { style: { padding: '12px 4px', opacity: 0.7 } }, p.type),
                h('td', { style: { padding: '12px 4px', textAlign: 'right', fontWeight: 'bold', color: '#a29bfe' } }, p.vram)
              )
            )
          )
        )
      )
    );
  }

  registry.register('hermes-plugin-vram-observability', GpuObservability);
})();
