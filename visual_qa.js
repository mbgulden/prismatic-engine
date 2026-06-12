const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

(async () => {
  const screenshotsDir = path.join(__dirname, 'screenshots');
  if (!fs.existsSync(screenshotsDir)) {
    fs.mkdirSync(screenshotsDir, { recursive: true });
  }

  console.log('Launching browser...');
  const browser = await chromium.launch({ headless: true });
  
  const views = [
    { name: 'desktop', width: 1200, height: 800 },
    { name: 'tablet', width: 768, height: 1024 },
    { name: 'mobile', width: 375, height: 667 }
  ];

  for (const view of views) {
    console.log(`Capturing ${view.name} screenshot (${view.width}x${view.height})...`);
    const page = await browser.newPage();
    await page.setViewportSize({ width: view.width, height: view.height });
    
    // Load the local index.html using absolute file URL
    const filePath = path.resolve(__dirname, 'index.html');
    await page.goto(`file://${filePath}`);
    
    // Wait to allow animations, grids, comets, and scroll-reveal states to resolve
    await page.waitForTimeout(1500);
    
    // Capture screenshot
    await page.screenshot({ path: path.join(screenshotsDir, `${view.name}.png`), fullPage: false });
    await page.close();
  }

  await browser.close();
  console.log('Screenshots captured successfully! Saved in:', screenshotsDir);
})();
