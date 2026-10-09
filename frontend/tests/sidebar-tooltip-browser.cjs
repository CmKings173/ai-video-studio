'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('C:/Users/Admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const { setup, ready, WIDTHS } = require('./group3-browser.cjs');

async function run() {
  const base = new URL(process.argv[2]);
  assert.ok(base.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(base.hostname));
  const out = path.resolve(__dirname, '../../tasks/evidence');
  await fs.mkdir(out, { recursive: true });
  const report = { fixtureOnly: true, cases: [], pageErrors: [], consoleErrors: [] };
  const browser = await chromium.launch({ headless: true, executablePath: 'C:/Users/Admin/AppData/Local/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-win64/chrome-headless-shell.exe' });
  try {
    for (const width of WIDTHS) {
      const state = { failures: new Set(), empty: new Set(), delay: new Set(), revisions: {}, names: {}, conflictPatch: new Set(), conflictArchive: new Set() };
      const { context, page } = await setup(browser, base.origin, width, state);
      page.on('pageerror', error => report.pageErrors.push(error.message));
      page.on('console', message => { if (message.type() === 'error') report.consoleErrors.push(message.text()); });
      try {
        await ready(page, `${base.origin}/videos`);
        assert.equal(await page.locator('#video_project_search,#video_product_search').count(), 0);
        for (const label of ['Dashboard', 'Dự án', 'Sản phẩm', 'Video', 'Tải tài nguyên', 'Quản trị']) {
          await page.locator('main h1').evaluate(el => { el.tabIndex = -1; el.focus(); });
          const link = page.locator('.sidebar nav').getByRole('link', { name: label, exact: true });
          const tooltip = page.getByRole('tooltip');
          await page.mouse.move(width - 2, 0);
          await link.hover(); await tooltip.waitFor({ state: 'visible' });
          const bounds = await tooltip.boundingBox();
          assert.ok(bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= width && bounds.y + bounds.height <= 1000, JSON.stringify(bounds));
          await page.keyboard.press('Escape'); await tooltip.waitFor({ state: 'hidden' });
          await page.mouse.move(width - 2, 0); await link.hover();
          await tooltip.waitFor({ state: 'visible' }); await tooltip.hover();
          await page.waitForTimeout(220);
          assert.equal(await tooltip.isVisible(), true, 'description stays visible while hovered');
          await page.keyboard.press('Escape'); await tooltip.waitFor({ state: 'hidden' });
          await page.mouse.move(width - 2, 0); await link.focus();
          await tooltip.waitFor({ state: 'visible' });
          assert.equal(await link.evaluate(el => el === document.activeElement), true);
          await page.locator('main h1').focus();
          await tooltip.waitFor({ state: 'hidden' });
          await link.focus(); await tooltip.waitFor({ state: 'visible' });
          await page.keyboard.press('Escape'); await tooltip.waitFor({ state: 'hidden' });
        }
        if (width === 360) {
          const link = page.locator('.sidebar nav').getByRole('link', { name: 'Video', exact: true });
          await page.locator('main h1').focus(); await page.mouse.move(width - 2, 0); await link.hover();
          await page.getByRole('tooltip').waitFor({ state: 'visible' });
          await page.screenshot({ path: path.join(out, 'ui-simplified-videos-360.png') });
          await page.keyboard.press('Escape');
          await page.goto(`${base.origin}/videos/new`, { waitUntil: 'domcontentloaded' });
          await page.getByRole('button', { name: 'Tạo video & Mở Workspace', exact: true }).waitFor();
          assert.equal(await page.locator('#new_video_project_search,#new_video_product_search').count(), 0);
          const cancel = page.getByRole('link', { name: 'Hủy', exact: true });
          assert.equal(await cancel.evaluate(el => getComputedStyle(el).whiteSpace), 'nowrap');
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
          await page.screenshot({ path: path.join(out, 'ui-simplified-create-360.png'), fullPage: true });
        }
        if (width === 1280) {
          await ready(page, `${base.origin}/products`);
          await page.screenshot({ path: path.join(out, 'ui-simplified-products-1280.png') });
        }
        report.cases.push({ width, status: 'PASS', links: 6 });
        console.log(`PASS tooltip hover/Escape/transfer/focus/bounds ${width}px`);
      } finally { await context.close(); }
    }
    assert.deepEqual(report.pageErrors, []); assert.deepEqual(report.consoleErrors, []);
    report.status = 'PASS';
  } finally {
    await browser.close();
    await fs.writeFile(path.join(out, 'sidebar-tooltip-browser.json'), JSON.stringify(report, null, 2));
  }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
