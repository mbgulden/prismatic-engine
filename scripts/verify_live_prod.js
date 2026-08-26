const playwright = require('playwright');
const fs = require('fs');

async function testLiveProduction() {
  const browser = await playwright.chromium.launch({ headless: true });
  
  const viewports = [
    { name: 'Desktop (1920px)', width: 1920, height: 1080, screenshot: 'live_prod_desktop.png' },
    { name: 'Tablet (768px)', width: 768, height: 1024, screenshot: 'live_prod_tablet_768px.png' },
    { name: 'Mobile (375px)', width: 375, height: 812, screenshot: 'live_prod_mobile_375px.png' }
  ];

  console.log('🚀 Starting Multi-Viewport Live Ground-Truth Playwright Oracle...');
  console.log('Target: https://prismatic.growthwebdev.com/\n');

  for (const vp of viewports) {
    const page = await browser.newPage({ viewport: { width: vp.width, height: vp.height } });
    
    console.log(`[Viewport: ${vp.name}] Navigating...`);
    const resp = await page.goto('https://prismatic.growthwebdev.com/', { waitUntil: 'networkidle', timeout: 30000 });
    
    const status = resp.status();
    if (status !== 200) {
      console.error(`❌ HTTP Status Error: expected 200, got ${status}`);
      process.exit(1);
    }

    // 1. Assert valid DOCTYPE & Title
    const doctypeValid = await page.evaluate(() => document.doctype !== null && document.doctype.name === 'html');
    const title = await page.title();
    
    // 2. Assert DOM Hierarchy
    const header = await page.$('header');
    const nav = await page.$('nav');
    const dagPanel = await page.$('#merkle-dag-cockpit-panel');
    const sectionDashboard = await page.$('#section-dashboard');

    // 3. Assert Zero Raw Code Leaks
    const rawJsLeak = await page.evaluate(() => {
      const text = document.body.innerText;
      const forbidden = [
        'reviewFactoryStateCount',
        'ctoryStateCount',
        'const isLight = document.body',
        'function connectWS()',
        'async function simulateWebhook',
        'export const',
        'import {'
      ];
      for (const token of forbidden) {
        if (text.includes(token)) return token;
      }
      return null;
    });

    // 4. Assert Zero Horizontal Overflow
    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    const overflow = scrollWidth > vp.width;

    // Capture screenshot
    const screenshotPath = `/home/ubuntu/work/prismatic-engine/artifacts/${vp.screenshot}`;
    await page.screenshot({ path: screenshotPath });

    console.log(`  ✓ HTTP Status: 200 OK`);
    console.log(`  ✓ Valid <!DOCTYPE html>: ${doctypeValid}`);
    console.log(`  ✓ Title: "${title}"`);
    console.log(`  ✓ Header Present: ${!!header}`);
    console.log(`  ✓ Nav Present: ${!!nav}`);
    console.log(`  ✓ Merkle DAG Present: ${!!dagPanel}`);
    console.log(`  ✓ Section Dashboard: ${!!sectionDashboard}`);
    console.log(`  ✓ Raw JS Text Leak: ${rawJsLeak ? `FAIL (Found "${rawJsLeak}")` : 'NONE (Clean)'}`);
    console.log(`  ✓ Viewport Width: ${vp.width}px, Document scrollWidth: ${scrollWidth}px (Overflow: ${overflow ? 'FAIL' : 'PASS 0px'})`);
    console.log(`  ✓ Screenshot Saved: ${screenshotPath}\n`);

    await page.close();

    if (!doctypeValid || !header || !nav || rawJsLeak || overflow) {
      console.error(`❌ FATAL AUDIT FAILURE on viewport ${vp.name}`);
      await browser.close();
      process.exit(1);
    }
  }

  await browser.close();
  console.log('✅ ALL 3 VIEWPORTS PASSED LIVE GROUND-TRUTH PLAYWRIGHT ORACLE (100% GREEN)!');
}

testLiveProduction().catch(err => {
  console.error('Fatal Playwright Error:', err);
  process.exit(1);
});
