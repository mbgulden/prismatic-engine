const puppeteer = require('/tmp/node_modules/puppeteer-core');
const fs = require('fs');

(async () => {
  console.log("=== STARTING PUPPETEER WORKSPACE DASHBOARD VISUAL AUDIT ===");
  
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/chromium-browser',
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
  });

  const page = await browser.newPage();
  
  // 1. Desktop Viewport (1280x800)
  await page.setViewport({ width: 1280, height: 800 });
  console.log("Navigating to Gateway Dashboard http://127.0.0.1:8899...");
  await page.goto('http://127.0.0.1:8899', { waitUntil: 'networkidle0', timeout: 15000 });

  console.log("Clicking Curated Workspace tab...");
  await page.click('#tab-btn-curated-workspace');
  await new Promise(r => setTimeout(r, 1000));

  console.log("Waiting for tree items to load...");
  await page.waitForSelector('.workspace-doc-item', { timeout: 10000 });

  const docCount = await page.$$eval('.workspace-doc-item', items => items.length);
  console.log(`Found ${docCount} document items rendered in UI tree!`);

  // Click first document item
  console.log("Selecting first document item...");
  await page.evaluate(() => {
    const item = document.querySelector('.workspace-doc-item');
    if (item) item.click();
  });
  await new Promise(r => setTimeout(r, 1000));

  // Check document header & content text
  const docTitle = await page.$eval('#doc-title', el => el.textContent.trim());
  const docPath = await page.$eval('#doc-path', el => el.textContent.trim());
  const badgeText = await page.$eval('#doc-status-badge', el => el.textContent.trim());
  const contentSnippet = await page.$eval('#doc-content-container', el => el.textContent.trim().substring(0, 100));

  console.log(`Document Selected: "${docTitle}"`);
  console.log(`Doc Path: "${docPath}"`);
  console.log(`Status Badge: "${badgeText}"`);
  console.log(`Content Snippet: "${contentSnippet}..."`);

  // Take Desktop Screenshot
  const desktopPath = '/home/ubuntu/work/prismatic-engine/docs/dashboard-workspace-desktop.png';
  await page.screenshot({ path: desktopPath, fullPage: false });
  console.log(`Saved Desktop Screenshot to: ${desktopPath}`);

  // 2. Mobile Viewport (375x812)
  await page.setViewport({ width: 375, height: 812, isMobile: true });
  await new Promise(r => setTimeout(r, 500));

  const mobilePath = '/home/ubuntu/work/prismatic-engine/docs/dashboard-workspace-mobile.png';
  await page.screenshot({ path: mobilePath, fullPage: false });
  console.log(`Saved Mobile Screenshot to: ${mobilePath}`);

  await browser.close();
  console.log("=== VISUAL AUDIT COMPLETED SUCCESSFULLY ===");
})();
