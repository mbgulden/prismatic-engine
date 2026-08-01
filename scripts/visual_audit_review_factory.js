/**
 * Visual Audit Script for Review Factory V1 Dashboard (PR #417)
 * Tests Desktop (1920x1080), Tablet (768x1024), and Mobile (375x812) viewports.
 * Verifies #tab-btn-review-factory visibility, stats card rendering, table rendering,
 * and captures full-page reference screenshots.
 */

const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer');

async function runVisualAudit() {
    console.log("Starting Review Factory V1 Puppeteer Visual Audit...");
    const dashboardHtmlPath = path.resolve(__dirname, '../prismatic/gateway/templates/dashboard.html');
    const outputDir = path.resolve(__dirname, '../docs/images');

    if (!fs.existsSync(outputDir)) {
        fs.mkdirSync(outputDir, { recursive: true });
    }

    const browser = await puppeteer.launch({
        headless: true,
        args: ['--no-sandbox', '--disable-setuid-sandbox']
    });

    const page = await browser.newPage();
    const fileUrl = `file://${dashboardHtmlPath}`;
    console.log(`Navigating to: ${fileUrl}`);

    await page.goto(fileUrl, { waitUntil: 'domcontentloaded' });

    // Ensure #tab-btn-review-factory exists and click it
    await page.waitForSelector('#tab-btn-review-factory', { timeout: 5000 });
    console.log("Found #tab-btn-review-factory tab button");

    await page.click('#tab-btn-review-factory');
    console.log("Clicked #tab-btn-review-factory");

    // Wait for #section-review-factory to be visible
    await page.waitForSelector('#section-review-factory:not(.hidden)', { timeout: 5000 });
    console.log("#section-review-factory is visible!");

    // Check key DOM elements
    const elementsToVerify = [
        '#rf-status-badge',
        '#btn-rf-janitor',
        '#btn-rf-release',
        '#btn-rf-authorize',
        '#rf-total-jobs',
        '#rf-active-jobs',
        '#rf-merged-jobs',
        '#rf-failed-jobs',
        '#rf-filter-state',
        '#rf-filter-tier',
        '#rf-search-input',
        '#rf-jobs-tbody'
    ];

    for (const selector of elementsToVerify) {
        const found = await page.$(selector);
        if (!found) {
            throw new Error(`Missing expected element: ${selector}`);
        }
    }
    console.log(`Successfully verified all ${elementsToVerify.length} Review Factory DOM elements!`);

    // Viewport 1: Desktop (1920x1080)
    await page.setViewport({ width: 1920, height: 1080 });
    const desktopScreenshotPath = path.join(outputDir, 'rf_v1_dashboard_desktop.png');
    await page.screenshot({ path: desktopScreenshotPath, fullPage: true });
    console.log(`Desktop audit screenshot saved to: ${desktopScreenshotPath}`);

    // Viewport 2: Tablet (768x1024)
    await page.setViewport({ width: 768, height: 1024 });
    const tabletScreenshotPath = path.join(outputDir, 'rf_v1_dashboard_tablet.png');
    await page.screenshot({ path: tabletScreenshotPath, fullPage: true });
    console.log(`Tablet audit screenshot saved to: ${tabletScreenshotPath}`);

    // Viewport 3: Mobile (375x812)
    await page.setViewport({ width: 375, height: 812 });
    const mobileScreenshotPath = path.join(outputDir, 'rf_v1_dashboard_mobile.png');
    await page.screenshot({ path: mobileScreenshotPath, fullPage: true });
    console.log(`Mobile audit screenshot saved to: ${mobileScreenshotPath}`);

    // Save canonical audit image
    const canonicalScreenshotPath = path.join(outputDir, 'rf_v1_dashboard_audit.png');
    fs.copyFileSync(desktopScreenshotPath, canonicalScreenshotPath);
    console.log(`Canonical audit screenshot saved to: ${canonicalScreenshotPath}`);

    await browser.close();
    console.log("Puppeteer Visual Audit Completed Successfully!");
}

runVisualAudit().catch(err => {
    console.error("Puppeteer Visual Audit Error:", err);
    process.exit(1);
});
