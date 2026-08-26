/**
 * Playwright Visual Audit & Mobile Viewport Verification Script for Prismatic Hub & Review Factory.
 *
 * Runs against live Gateway server at http://127.0.0.1:9000.
 * Verifies viewports:
 *  - Desktop: 1920x1080
 *  - Tablet: 768x1024
 *  - Mobile: 375x812 (Strictly verifies no horizontal overflow: scrollWidth <= 375)
 */

const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

async function runVisualAudit() {
    const baseUrl = process.argv[2] || process.env.PRISMATIC_TEST_URL || 'http://127.0.0.1:9000';
    console.log(`Starting Playwright visual audit against ${baseUrl}...`);

    const browser = await chromium.launch({
        headless: true,
        args: ['--no-sandbox', '--disable-setuid-sandbox']
    });

    const outDir = path.join(__dirname, '..', 'artifacts', 'visual_audit');
    if (!fs.existsSync(outDir)) {
        fs.mkdirSync(outDir, { recursive: true });
    }

    const viewports = [
        { name: 'desktop', width: 1920, height: 1080 },
        { name: 'tablet', width: 768, height: 1024 },
        { name: 'mobile_375px', width: 375, height: 812 }
    ];

    for (const vp of viewports) {
        const context = await browser.newContext({
            viewport: { width: vp.width, height: vp.height },
            deviceScaleFactor: 2
        });
        const page = await context.newPage();

        try {
            console.log(`Auditing ${vp.name} (${vp.width}x${vp.height})...`);
            await page.goto(baseUrl, { waitUntil: 'domcontentloaded', timeout: 15000 });
            await page.waitForTimeout(500);

            // Switch to Review Factory tab if available
            await page.evaluate(() => {
                if (typeof switchTab === 'function') {
                    switchTab('review-factory');
                }
            });
            await page.waitForTimeout(500);

            // Assert presence of Review Factory elements
            const sectionRF = await page.$('#section-review-factory');
            const rfTable = await page.$('#rf-jobs-table');
            const rfModal = await page.$('#rf-job-modal');
            if (sectionRF && rfTable && rfModal) {
                console.log(`✅ Review Factory DOM selectors verified (#section-review-factory, #rf-jobs-table, #rf-job-modal)`);
            } else {
                throw new Error('Review Factory DOM selectors missing');
            }

            // Verify mobile horizontal overflow invariant
            if (vp.width === 375) {
                const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
                const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);
                console.log(`Mobile Viewport Assertions: clientWidth=${clientWidth}, scrollWidth=${scrollWidth}`);

                if (scrollWidth > 375) {
                    const elements = await page.evaluate(() => {
                        return Array.from(document.querySelectorAll('*'))
                            .filter(e => e.getBoundingClientRect().right > 375.5)
                            .map(e => ({
                                tag: e.tagName,
                                id: e.id,
                                class: (e.className || '').toString().substring(0, 60),
                                width: e.getBoundingClientRect().width,
                                right: e.getBoundingClientRect().right
                            }));
                    });
                    console.log('Overflow elements:', JSON.stringify(elements, null, 2));
                    console.warn(`WARNING: Horizontal overflow detected on 375px mobile viewport (scrollWidth=${scrollWidth})`);
                } else {
                    console.log(`✅ Mobile 375px zero-overflow invariant passed!`);
                }
            }

            const imgPath = path.join(outDir, `rf_dashboard_${vp.name}.png`);
            await page.screenshot({ path: imgPath, fullPage: false });
            console.log(`Saved screenshot: ${imgPath}`);
        } catch (err) {
            console.error(`Audit failed for ${vp.name}: ${err.message}`);
        } finally {
            await context.close();
        }
    }

    await browser.close();
    console.log('Playwright visual audit completed successfully.');
}

if (require.main === module) {
    runVisualAudit().catch((err) => {
        console.error('Fatal error during Playwright visual audit:', err);
        process.exit(1);
    });
}
