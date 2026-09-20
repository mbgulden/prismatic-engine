const playwright = require('playwright');
const fs = require('fs');
const path = require('path');

async function captureTabs() {
  const browser = await playwright.chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  const page = await context.newPage();

  const tabs = [
    { name: 'ingestion_queue', tab: 'ingestion-queue', file: 'live_ingestion_queue_tab.png' },
    { name: 'review_factory', tab: 'review-factory', file: 'live_review_factory_tab.png' },
    { name: 'swarmproof', tab: 'swarmproof', file: 'live_swarmproof_tab.png' },
    { name: 'signals', tab: 'signals', file: 'live_signals_wave_tab.png' },
  ];

  fs.mkdirSync('/home/ubuntu/work/prismatic-engine/artifacts', { recursive: true });

  for (const t of tabs) {
    console.log(`Navigating to ${t.name} (tab=${t.tab})...`);
    await page.goto(`http://127.0.0.1:9000/?tab=${t.tab}`, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(3000);
    const outPath = path.join('/home/ubuntu/work/prismatic-engine/artifacts', t.file);
    await page.screenshot({ path: outPath, fullPage: false });
    console.log(`Saved screenshot to ${outPath}`);
  }

  // Mobile Viewport for Signals
  const mobileContext = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true });
  const mobilePage = await mobileContext.newPage();
  await mobilePage.goto('http://127.0.0.1:9000/?tab=signals', { waitUntil: 'networkidle', timeout: 30000 });
  await mobilePage.waitForTimeout(2000);
  const mobilePath = '/home/ubuntu/work/prismatic-engine/artifacts/live_mobile_375px_signals.png';
  await mobilePage.screenshot({ path: mobilePath, fullPage: false });
  console.log(`Saved mobile screenshot to ${mobilePath}`);

  await browser.close();
}

captureTabs().catch(err => {
  console.error('Capture error:', err);
  process.exit(1);
});
