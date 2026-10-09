'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('C:/Users/Admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const { setup, WIDTHS } = require('./group3-browser.cjs');
const output = path.resolve(__dirname, '../../tasks/evidence');

async function run() {
  const base = new URL(process.argv[2]);
  assert.ok(base.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(base.hostname));
  const report = { fixtureOnly: true, cases: [], errors: [] };
  const browser = await chromium.launch({ headless: true, executablePath: 'C:/Users/Admin/AppData/Local/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-win64/chrome-headless-shell.exe' });
  try {
    for (const width of WIDTHS) {
      const s = { failures: new Set(), empty: new Set(), delay: new Set(), revisions: {}, names: {}, conflictPatch: new Set(), conflictArchive: new Set() };
      const { context, page } = await setup(browser, base.origin, width, s);
      page.on('pageerror', error => report.errors.push(error.message));
      page.on('console', message => { if (message.type() === 'error') report.errors.push(message.text()); });
      try {
        await page.goto(`${base.origin}/videos/new`);
        const language = page.locator('#studio_language');
        await language.waitFor();
        assert.equal(await language.inputValue(), 'vi');
        const viTitle = await page.locator('main h1').innerText();
        assert.equal(viTitle, 'Tạo video mới');
        assert.equal(await page.getByText('On-Premise Production', { exact: true }).count(), 0);
        assert.equal(await page.getByText('SSE Live Connected', { exact: true }).count(), 0);
        assert.equal(await page.locator('#product_id').count(), 0);
        if (width === 1280) {
          const navItem = page.locator('aside nav a').first();
          await navItem.hover();
          const tooltip = page.getByRole('tooltip');
          await tooltip.waitFor();
          assert.ok((await tooltip.innerText()).trim().length > 0);
          const bounds = await tooltip.boundingBox();
          assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= width);
          await page.mouse.move(0, 0);
        }
        for (const id of ['title', 'kind', 'target_duration', 'aspect_ratio']) {
          const gap = await page.locator(`#${id}`).evaluate(el => {
            const label = document.querySelector(`label[for="${el.id}"]`);
            return el.getBoundingClientRect().top - label.getBoundingClientRect().bottom;
          });
          assert.ok(gap >= 0 && gap <= 12, `${id} label gap ${gap}`);
        }
        await page.locator('#title').fill('My unchanged user title');
        await language.selectOption('en');
        await page.waitForFunction(() => document.documentElement.lang === 'en');
        assert.notEqual(await page.locator('main h1').innerText(), viTitle);
        assert.equal(await page.locator('#title').inputValue(), 'My unchanged user title');
        assert.equal(await page.locator('#kind').inputValue(), 'QUICK_CLIP');
        assert.equal(await page.locator('#target_duration').inputValue(), '5');
        assert.equal(await page.locator('#aspect_ratio').inputValue(), '9:16');
        await page.reload();
        await page.waitForFunction(() => document.documentElement.lang === 'en');
        assert.equal(await language.inputValue(), 'en');
        for (const route of ['videos', 'products', 'projects']) {
          await page.goto(`${base.origin}/${route}`);
          await page.locator('main h1').waitFor();
          await page.waitForFunction(() => document.documentElement.lang === 'en');
          const english = await page.locator('main h1').innerText();
          const search = page.locator('main input[type="text"]').first();
          const iconInside = await search.evaluate(el => {
            const input = el.getBoundingClientRect();
            const icon = el.parentElement?.querySelector('svg')?.getBoundingClientRect();
            return Boolean(icon && icon.left >= input.left && icon.right <= input.right
              && icon.top >= input.top && icon.bottom <= input.bottom);
          });
          assert.equal(iconInside, true, `${route} search icon escaped its field ${width}`);
          await page.locator('#studio_language').selectOption('vi');
          await page.waitForFunction(() => document.documentElement.lang === 'vi');
          const vietnamese = await page.locator('main h1').innerText();
          assert.notEqual(english, vietnamese, route);
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, `${route} overflow ${width}`);
          await page.locator('#studio_language').selectOption('en');
        }
        await page.goto(`${base.origin}/videos/new`);
        await page.locator('#studio_language').selectOption('vi');
        if (width === 360 || width === 1280) await page.screenshot({ path: path.join(output, `bilingual-create-${width}.png`), fullPage: true });
        report.cases.push({ width, status: 'PASS', scenarios: ['locale switch', 'persistence', 'unchanged form values', 'label spacing', 'route headings', 'overflow'] });
        console.log(`PASS bilingual UI ${width}px`);
      } finally { await context.close(); }
    }
    assert.deepEqual(report.errors, []);
    report.status = 'PASS';
  } finally {
    await browser.close();
    await fs.mkdir(output, { recursive: true });
    await fs.writeFile(path.join(output, 'bilingual-ui-browser.json'), JSON.stringify(report, null, 2));
  }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
