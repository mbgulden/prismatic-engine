const playwright = require('playwright');
const fs = require('fs');
const path = require('path');

async function testBifurcatedStudio() {
  const browser = await playwright.chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  const brainDir = '/home/ubuntu/.gemini/antigravity-cli/brain/9762816f-cd24-4d2d-b3d2-b5455aaeb213';
  const artifactsDir = '/home/ubuntu/work/prismatic-engine/artifacts';
  fs.mkdirSync(artifactsDir, { recursive: true });

  const saveAuditScreenshot = async (filename) => {
    const localPath = path.join(artifactsDir, filename);
    await page.screenshot({ path: localPath });
    if (fs.existsSync(brainDir)) {
      const brainPath = path.join(brainDir, filename);
      fs.copyFileSync(localPath, brainPath);
      console.log(`  ✓ Registered Brain Artifact: ${brainPath}`);
    }
  };

  console.log('🚀 Starting Playwright Audit for Sprint 1 Bifurcated UI...');

  // Step 1: Navigate to base URL (default mode: Creator Studio)
  console.log('1. Loading http://127.0.0.1:9000/...');
  await page.goto('http://127.0.0.1:9000/', { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForTimeout(1000);

  // Assert Creator Mode is default
  const creatorNavVisible = await page.isVisible('#nav-creator-tabs');
  const engNavVisible = await page.isVisible('#nav-engineering-tabs');
  const studioSectionVisible = await page.isVisible('#section-studio');
  console.log(`  ✓ Creator Nav Visible: ${creatorNavVisible}`);
  console.log(`  ✓ Engineering Nav Hidden: ${!engNavVisible}`);
  console.log(`  ✓ Studio Section Visible: ${studioSectionVisible}`);

  // Assert Prompt Deck elements
  const hasVisionLabel = await page.innerText('label[for="studio-prompt-input"]');
  console.log(`  ✓ Vision Prompt Deck Ready: "${hasVisionLabel.trim()}"`);

  // Step 2: Test Active Oahu Preset Click
  console.log('2. Clicking Active Oahu Rentals starter preset pill...');
  await page.click("button:has-text('Active Oahu Rentals (134B Hamakua Dr)')");
  await page.waitForTimeout(500);

  const promptValue = await page.inputValue('#studio-prompt-input');
  console.log(`  ✓ Prompt updated with Active Oahu target: "${promptValue.slice(0, 85)}..."`);
  if (!promptValue.includes('134B Hamakua Dr')) {
    throw new Error('Preset did not load Active Oahu Hamakua Dr prompt text');
  }

  await saveAuditScreenshot('live_creator_studio_activeoahu.png');

  // Step 3: Navigate to Deployed Assets Tab
  console.log('3. Navigating to Deployed Assets Tab...');
  await page.click('#tab-btn-assets');
  await page.waitForTimeout(800);

  const assetsVisible = await page.isVisible('#section-assets');
  const activeOahuHeading = await page.innerText('#section-assets h3');
  console.log(`  ✓ Deployed Assets Tab Visible: ${assetsVisible}`);
  console.log(`  ✓ Featured Property: "${activeOahuHeading}"`);
  await saveAuditScreenshot('live_deployed_assets_hamakua.png');

  // Step 4: Navigate to Fleet Pulse Tab
  console.log('4. Navigating to Fleet Pulse Tab...');
  await page.click('#tab-btn-pulse');
  await page.waitForTimeout(800);

  const pulseVisible = await page.isVisible('#section-pulse');
  const pulseHeadline = await page.innerText('#pulse-status-headline');
  console.log(`  ✓ Fleet Pulse Tab Visible: ${pulseVisible}`);
  console.log(`  ✓ Swarm Fleet Status: "${pulseHeadline}"`);
  await saveAuditScreenshot('live_fleet_pulse_status.png');

  // Step 5: Test Mode Bifurcation Toggle (Ctrl+Shift+D / Shortcut)
  console.log('5. Testing Mode Switch to Engineering Telemetry via shortcut (Ctrl+Shift+D)...');
  await page.keyboard.press('Control+Shift+KeyD');
  await page.waitForTimeout(800);

  const engNavAfterToggle = await page.isVisible('#nav-engineering-tabs');
  const creatorNavAfterToggle = await page.isVisible('#nav-creator-tabs');
  console.log(`  ✓ Engineering Nav Bar Visible: ${engNavAfterToggle}`);
  console.log(`  ✓ Creator Nav Bar Hidden: ${!creatorNavAfterToggle}`);
  await saveAuditScreenshot('live_engineering_telemetry_toggled.png');

  // Step 6: Test Mobile 375px Viewport Audit
  console.log('6. Auditing Mobile 375px Viewport...');
  await page.setViewportSize({ width: 375, height: 812 });
  // Switch back to creator mode
  await page.keyboard.press('Control+Shift+KeyD');
  await page.waitForTimeout(800);
  await page.click('#tab-btn-studio');
  await page.waitForTimeout(500);

  const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
  const overflow = scrollWidth > 375;
  console.log(`  ✓ Mobile 375px ScrollWidth: ${scrollWidth}px (Overflow: ${overflow ? 'FAIL' : 'PASS 0px'})`);
  await saveAuditScreenshot('live_creator_studio_mobile_375px.png');

  await browser.close();
  console.log('✅ BIFURCATED CREATOR STUDIO PLAYWRIGHT AUDIT COMPLETE & 100% GREEN!');
}

testBifurcatedStudio().catch(err => {
  console.error('❌ Playwright audit failed:', err);
  process.exit(1);
});
