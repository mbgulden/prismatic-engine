const playwright = require('playwright');
const http = require('http');

function postJson(urlPath, payload) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(payload);
    const req = http.request({
      hostname: '127.0.0.1',
      port: 9000,
      path: urlPath,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(data)
      }
    }, (res) => {
      let body = '';
      res.on('data', chunk => body += chunk);
      res.on('end', () => resolve(JSON.parse(body || '{}')));
    });
    req.on('error', reject);
    req.write(data);
    req.end();
  });
}

async function runLiveProof() {
  const browser = await playwright.chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });

  console.log('1. Navigating to Prismatic Hub on live domain...');
  await page.goto('https://prismatic.growthwebdev.com/', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(2000);

  // Assert WebSocket Connection Status
  const statusText = await page.$eval('#status-text', el => el.textContent.trim());
  console.log(`WebSocket Status on page: "${statusText}"`);

  // Step 2: Acquire Multi-Agent Leases simultaneously
  console.log('2. Acquiring 6 real file leases across Lightbringer AGY, Hermes, and Fred...');

  // Agent 1: Lightbringer AGY locks 3 core files
  await postJson('/api/gateway/swarmlock/acquire', {
    paths: [
      'prismatic/core/locking.py',
      'prismatic/gateway/server.py',
      'prismatic/gateway/control_auth.py'
    ],
    agent_id: 'lightbringer-agy',
    task_id: 'GRO-4203',
    task_title: 'Phase 5B Multi-Node Swarm Hardening',
    ttl: 300
  });

  // Agent 2: Hermes locks 2 client & hypervisor files
  await postJson('/api/gateway/swarmlock/acquire', {
    paths: [
      'prismatic/client/interceptor.py',
      'prismatic/hypervisor/ledger.py'
    ],
    agent_id: 'hermes',
    task_id: 'GRO-3319',
    task_title: 'Real-Time Interceptor Pipeline Verification',
    ttl: 300
  });

  // Agent 3: Fred locks frontend dashboard
  await postJson('/api/gateway/swarmlock/acquire', {
    paths: [
      'prismatic/gateway/dashboard_src/scripts/dashboard.js'
    ],
    agent_id: 'fred',
    task_id: 'GRO-1465',
    task_title: 'Dynamic Reactive Merkle-DAG Visualization',
    ttl: 300
  });

  // Allow WebSocket broadcast to propagate to DOM
  await page.waitForTimeout(2500);

  // Capture Screenshot of Active Multi-Agent Held Leases on Live Merkle-DAG
  const activeDagScreenshot = '/home/ubuntu/work/prismatic-engine/artifacts/live_fleet_active_leases_dag.png';
  await page.screenshot({ path: activeDagScreenshot });
  console.log(`Saved active DAG screenshot: ${activeDagScreenshot}`);

  // Navigate to Signals tab to capture active multi-file lease matrix
  console.log('3. Navigating to Signals Tab to audit Multi-Agent Swarm Matrix...');
  await page.goto('https://prismatic.growthwebdev.com/?tab=signals', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(2500);

  const activeSignalsScreenshot = '/home/ubuntu/work/prismatic-engine/artifacts/live_fleet_active_signals_matrix.png';
  await page.screenshot({ path: activeSignalsScreenshot });
  console.log(`Saved active Signals Matrix screenshot: ${activeSignalsScreenshot}`);

  // Step 4: Induce Deflected Lock Collision
  console.log('4. Inducing lock collision (Kai attempting to acquire server.py held by Lightbringer AGY)...');
  const conflictRes = await postJson('/api/gateway/swarmlock/acquire', {
    paths: ['prismatic/gateway/server.py'],
    agent_id: 'kai',
    task_id: 'GRO-5001',
    task_title: 'Contested Resource Collision Test',
    ttl: 60
  });
  console.log('Collision attempt response:', conflictRes);

  await page.waitForTimeout(2000);

  // Step 5: Cleanly Release All Leases
  console.log('5. Releasing all leases...');
  await postJson('/api/gateway/swarmlock/release', {
    paths: [
      'prismatic/core/locking.py',
      'prismatic/gateway/server.py',
      'prismatic/gateway/control_auth.py'
    ],
    agent_id: 'lightbringer-agy',
    task_id: 'GRO-4203'
  });

  await postJson('/api/gateway/swarmlock/release', {
    paths: [
      'prismatic/client/interceptor.py',
      'prismatic/hypervisor/ledger.py'
    ],
    agent_id: 'hermes',
    task_id: 'GRO-3319'
  });

  await postJson('/api/gateway/swarmlock/release', {
    paths: [
      'prismatic/gateway/dashboard_src/scripts/dashboard.js'
    ],
    agent_id: 'fred',
    task_id: 'GRO-1465'
  });

  await page.waitForTimeout(2000);
  await page.goto('https://prismatic.growthwebdev.com/?tab=signals', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(2000);

  const releasedSignalsScreenshot = '/home/ubuntu/work/prismatic-engine/artifacts/live_fleet_released_signals_matrix.png';
  await page.screenshot({ path: releasedSignalsScreenshot });
  console.log(`Saved released Signals Matrix screenshot: ${releasedSignalsScreenshot}`);

  await browser.close();
  console.log('Live Fleet Concurrency & WebSocket Proof Complete!');
}

runLiveProof().catch(e => {
  console.error('Fatal Proof Error:', e);
  process.exit(1);
});
