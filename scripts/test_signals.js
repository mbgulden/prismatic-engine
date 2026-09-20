const playwright = require('playwright');
async function run() {
  const browser = await playwright.chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  await page.goto('https://prismatic.growthwebdev.com/?tab=signals', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(2000);
  await page.screenshot({ path: '/home/ubuntu/work/prismatic-engine/artifacts/live_prod_signals_tab.png' });
  await browser.close();
  console.log('Done capturing signals tab.');
}
run().catch(e => { console.error(e); process.exit(1); });
