const playwright = require('playwright');

async function testLiveProduction() {
  const browser = await playwright.chromium.launch({ headless: true });
  
  // 1. Desktop test
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  console.log('Navigating to https://prismatic.growthwebdev.com/ ...');
  await page.goto('https://prismatic.growthwebdev.com/', { waitUntil: 'networkidle', timeout: 30000 });
  
  const title = await page.title();
  const header = await page.$('header');
  const nav = await page.$('nav');
  const dagPanel = await page.$('#merkle-dag-cockpit-panel');
  const rawJsLeak = await page.evaluate(() => {
    return document.body.innerText.includes('reviewFactoryStateCount') || document.body.innerText.includes('ctoryStateCount');
  });

  await page.screenshot({ path: '/home/ubuntu/work/prismatic-engine/artifacts/live_prod_desktop.png' });

  // 2. Mobile test
  await page.setViewportSize({ width: 375, height: 812 });
  await page.screenshot({ path: '/home/ubuntu/work/prismatic-engine/artifacts/live_prod_mobile_375px.png' });

  console.log('Title:', title);
  console.log('Header Present:', !!header);
  console.log('Nav Present:', !!nav);
  console.log('Merkle DAG Present:', !!dagPanel);
  console.log('Raw JS Leak Detected:', rawJsLeak);

  if (!header || !nav || rawJsLeak) {
    console.error('FATAL: Visual rendering regression detected!');
    process.exit(1);
  }

  console.log('✅ LIVE PRODUCTION VISUAL SMOKE AUDIT PASSED 100% GREEN!');
  await browser.close();
}

testLiveProduction().catch(err => {
  console.error('Test failed:', err);
  process.exit(1);
});
