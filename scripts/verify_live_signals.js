const playwright = require('playwright');

async function testSignalsTab() {
  const browser = await playwright.chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  
  console.log('Navigating to https://prismatic.growthwebdev.com/#signals ...');
  await page.goto('https://prismatic.growthwebdev.com/#signals', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(1000);

  const controlStrip = await page.$('[data-proof-marker="signals-fleet-control-strip"]');
  const pauseBtn = await page.$('#btn-fleet-pause-toggle');
  
  console.log('Fleet Control Strip Present:', !!controlStrip);
  console.log('Pause Button Present:', !!pauseBtn);

  await page.screenshot({ path: '/home/ubuntu/work/prismatic-engine/artifacts/live_prod_signals_tab.png' });
  await browser.close();
  
  if (!controlStrip || !pauseBtn) {
    console.error('Fleet control strip missing in Signals tab');
    process.exit(1);
  }
  console.log('✅ SIGNALS TAB FLEET CONTROL VERIFIED LIVE!');
}

testSignalsTab().catch(err => {
  console.error(err);
  process.exit(1);
});
