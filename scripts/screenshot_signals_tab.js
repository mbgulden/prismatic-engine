const playwright = require('playwright');

async function capture() {
  const browser = await playwright.chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });

  await page.goto('https://prismatic.growthwebdev.com/?tab=signals', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(3000);

  const screenshotPath = '/home/ubuntu/work/prismatic-engine/artifacts/live_fred_signals_tab.png';
  await page.screenshot({ path: screenshotPath });
  console.log(`Saved screenshot to ${screenshotPath}`);

  await browser.close();
}

capture().catch(e => {
  console.error(e);
  process.exit(1);
});
