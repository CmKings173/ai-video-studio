'use strict';
// Standalone sidecar: intentionally excluded from npm's *.test.cjs discovery.
// Usage: node frontend/tests/group3-browser.cjs http://127.0.0.1:PORT
// Main owns the production build/server. This runner never starts either.
const { chromium } = require('C:/Users/Admin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const ROOT = path.resolve(__dirname, '../..');
const OUT = path.join(ROOT, 'tasks/evidence');
const RESULTS = path.join(OUT, 'group3-browser-matrix.json');
const WIDTHS = [360, 480, 768, 1024, 1280, 1440];
const TIMEOUT = 15000;
const LONG = 'LongName_' + 'abcdefghij'.repeat(15);
const DATE = '2026-10-08T00:00:00Z';
const report = { status: 'NOT_RUN', browser: null, origin: null, timeoutMs: TIMEOUT, widths: WIDTHS, cases: [], checks: [], calls: [], console: [], pageErrors: [], blockedTraffic: [], fixtureOnly: true, serverOwnership: 'main' };
let currentCase;
function check(name, actual, detail) {
  report.checks.push({ case: currentCase, name, pass: Boolean(actual), detail });
  assert.ok(actual, name + (detail === undefined ? '' : ': ' + JSON.stringify(detail)));
}
async function until(name, fn) {
  const end = Date.now() + TIMEOUT;
  let value;
  while (Date.now() < end) {
    value = await fn();
    if (value) { check(name, true); return value; }
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  check(name, false, value);
}
function resource(kind, id, revision = 3) {
  return { id, name: `${kind}-${id}-${LONG}`, title: `video-${id}-${LONG}`, description: `Fixture ${id}`, archived: false, revision, created_at: DATE, context: { tone: 'fixture tone' }, brand_id: null, project_id: 'project-a', product_id: 'product-a', kind: 'QUICK_CLIP', aspect_ratio: '9:16', target_duration: 8, brief: 'fixture', config: {}, status: kind === 'assets' ? 'READY' : 'DRAFT', filename: `${id}-${LONG}.png`, content_type: 'image/png', role: 'REFERENCE_IMAGE', size_bytes: 1024, current_final_video_id: null };
}
function state() { return { failures: new Set(), empty: new Set(), delay: new Set(), revisions: {}, names: {}, conflictPatch: new Set(), conflictArchive: new Set() }; }
function envelope(code) { return { error: { code, message: `Controlled ${code}`, trace_id: 'fixture-only', details: {} } }; }
async function setup(browser, origin, width, s) {
  const context = await browser.newContext({ viewport: { width, height: 1000 }, serviceWorkers: 'block' });
  context.setDefaultTimeout(TIMEOUT);
  context.setDefaultNavigationTimeout(TIMEOUT);
  const page = await context.newPage();
  page.on('pageerror', err => report.pageErrors.push({ case: currentCase, message: err.message }));
  page.on('console', msg => {
    if (!['error', 'warning'].includes(msg.type())) return;
    const location = msg.location();
    const expected = msg.type() === 'error' && /Failed to load resource: the server responded with a status of (503|412)\b/.test(msg.text()) && location.url.includes('/api/v1/');
    report.console.push({ case: currentCase, type: msg.type(), text: msg.text(), location, category: expected ? 'controlled-http-resource-error' : 'unexpected' });
  });
  // One context-wide gate ensures all API/provider calls are fixtures or blocked.
  await context.route('**/*', async route => {
    const req = route.request();
    const url = new URL(req.url());
    if (!/^https?:$/.test(url.protocol)) return route.continue();
    if (!url.pathname.startsWith('/api/v1/')) {
      if (url.origin === origin && !url.pathname.startsWith('/api/')) return route.continue();
      report.blockedTraffic.push({ case: currentCase, url: req.url(), method: req.method() });
      return route.abort('blockedbyclient');
    }
    const scope = url.pathname;
    const query = Object.fromEntries(url.searchParams);
    const call = { case: currentCase, scope, page: query.page ?? null, page_size: query.page_size ?? null, query, method: req.method(), revision: req.headers()['if-match'] ?? null, payload: req.postData() ? JSON.parse(req.postData()) : null, status: 200 };
    report.calls.push(call);
    const fulfill = async (status, data) => { call.status = status; await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) }); };
    try {
      check('wire excludes size', !url.searchParams.has('size'), { scope, query });
      if (scope === '/api/v1/auth/me') return fulfill(200, { id: 'fixture-user', name: 'Browser Fixture', email: 'fixture@example.invalid', role: 'ADMIN', is_active: true, created_at: DATE });
      const segments = scope.split('/').filter(Boolean).slice(2);
      const [kind, id, child] = segments;
      if (!['projects', 'products', 'videos', 'assets', 'brands'].includes(kind)) {
        report.blockedTraffic.push({ case: currentCase, url: req.url(), method: req.method() });
        return fulfill(500, envelope('UNEXPECTED_FIXTURE_ROUTE'));
      }
      if (s.delay.has(scope)) await new Promise(resolve => setTimeout(resolve, 400));
      if (s.failures.has(scope) || s.failures.has(`${scope}?page=${query.page}`)) return fulfill(503, envelope('SERVICE_UNAVAILABLE'));
      if (req.method() === 'PATCH' || child === 'archive') {
        const detailScope = `/api/v1/${kind}/${id}`;
        const conflicts = child === 'archive' ? s.conflictArchive : s.conflictPatch;
        if (conflicts.has(detailScope)) {
          conflicts.delete(detailScope); s.revisions[detailScope] = 4; s.names[detailScope] = `Server-current-${id}`;
          return fulfill(412, envelope('REVISION_CONFLICT'));
        }
        s.revisions[detailScope] = (s.revisions[detailScope] || 3) + 1;
        if (call.payload?.name) s.names[detailScope] = call.payload.name;
        return fulfill(200, { ...resource(kind, id, s.revisions[detailScope]), name: s.names[detailScope], archived: child === 'archive' });
      }
      if (id && !child) return fulfill(200, { ...resource(kind, id, s.revisions[scope] || 3), ...(s.names[scope] ? { name: s.names[scope] } : {}) });
      check('wire list uses page_size', Number(query.page_size) > 0 && !Number.isNaN(Number(query.page_size)), { scope, query });
      const pageNumber = Number(query.page || 1), size = Number(query.page_size);
      const total = s.empty.has(scope) || query.search === 'no-match' ? 0 : kind === 'brands' ? 2 : 21;
      const itemKind = child || kind;
      const prefix = id ? `${id}-${itemKind}` : itemKind;
      const items = Array.from({ length: total }, (_, index) => resource(itemKind, `${prefix}-${String(index + 1).padStart(2, '0')}`));
      return fulfill(200, { items: items.slice((pageNumber - 1) * size, pageNumber * size), total, page: pageNumber, page_size: size });
    } catch (err) {
      call.fixtureError = err.message;
      await fulfill(500, envelope('FIXTURE_ASSERTION_FAILURE'));
    }
  });
  return { context, page };
}
const button = (page, name) => page.getByRole('button', { name, exact: true });
const nav = page => page.getByRole('navigation', { name: /Phân trang/ });
async function ready(page, url) {
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await until('pagination controls rendered', () => nav(page).isVisible());
  await until('verified first page rendered', async () => /Trang 1 \/ 2/.test(await nav(page).innerText()));
}
async function layout(page, label) {
  const dimensions = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth }));
  check('no overall horizontal overflow ' + label, dimensions.document <= dimensions.viewport + 1 && dimensions.body <= dimensions.viewport + 1, dimensions);
  check('accessible previous control ' + label, await button(page, 'Trang trước').isVisible());
  check('accessible next control ' + label, await button(page, 'Trang sau').isVisible());
}
async function next(page) { await button(page, 'Trang sau').click(); await until('last page reached', async () => /Trang 2 \/ 2/.test(await nav(page).innerText())); }
async function primary(page, origin, kind) {
  await ready(page, `${origin}/${kind}`);
  await layout(page, kind);
  if (kind === 'videos') {
    for (const label of ['Dashboard', 'Dự án', 'Sản phẩm', 'Video', 'Tải tài nguyên', 'Quản trị']) {
      const link = page.locator('.sidebar nav').getByRole('link', { name: label, exact: true });
      await link.hover();
      const tooltip = page.getByRole('tooltip');
      await tooltip.waitFor({ state: 'visible' });
      check(`sidebar ${label} tooltip names destination`, (await tooltip.innerText()).includes(label));
      const rect = await tooltip.boundingBox();
      check(`sidebar ${label} tooltip within viewport`, rect && rect.x >= 0 && rect.x + rect.width <= await page.evaluate(() => innerWidth), rect);
      await link.focus();
      await page.keyboard.press('Escape');
      await tooltip.waitFor({ state: 'hidden' });
    }
  }
  if (kind === 'videos' || kind === 'products') {
    const field = page.getByPlaceholder(kind === 'videos' ? 'Tìm theo tiêu đề...' : 'Tìm kiếm sản phẩm...');
    const bounds = await field.boundingBox();
    const icon = await field.locator('..').locator('svg').boundingBox();
    check('search icon stays within its input', bounds && icon && icon.y >= bounds.y && icon.y + icon.height <= bounds.y + bounds.height, { bounds, icon });
  }
  check('previous disabled first page', await button(page, 'Trang trước').isDisabled());
  const links = () => page.locator(`main a[href^="/${kind}/"]`).filter({ hasText: `${kind === 'videos' ? 'video' : kind}-` });
  const first = await links().allTextContents();
  check('20 first-page items', first.length === 20, first.length);
  await next(page);
  check('next disabled last page', await button(page, 'Trang sau').isDisabled());
  const second = await links().allTextContents();
  check('21st item accessible without duplication', second.length === 1 && !first.includes(second[0]), second);
  await button(page, 'Trang trước').click();
  await until('previous returns verified first page', async () => /Trang 1 \/ 2/.test(await nav(page).innerText()));
  await next(page);
  const search = page.getByPlaceholder(kind === 'projects' ? 'Tìm kiếm dự án...' : kind === 'products' ? 'Tìm kiếm sản phẩm...' : 'Tìm theo tiêu đề...');
  await search.fill('needle');
  await until('search resets to page one', async () => /Trang 1 \/ 2/.test(await nav(page).innerText()));
  check('search wire resets page', report.calls.some(c => c.case === currentCase && c.scope === `/api/v1/${kind}` && c.query.search === 'needle' && c.page === '1'));
  await next(page);
  if (kind === 'projects') await button(page, 'Đã lưu trữ').click();
  else if (kind === 'products') await page.locator('#brand_filter').selectOption('brands-01');
  else await page.getByRole('combobox', { name: 'Lọc theo dự án' }).selectOption('projects-01');
  await until('filter resets to page one', async () => /Trang 1 \/ 2/.test(await nav(page).innerText()));
  const key = kind === 'projects' ? 'archived' : kind === 'products' ? 'brand_id' : 'project_id';
  check('filter wire includes scope and page one', report.calls.some(c => c.case === currentCase && c.scope === `/api/v1/${kind}` && c.page === '1' && c.query[key] === (kind === 'projects' ? 'true' : kind === 'products' ? 'brands-01' : 'projects-01')));
  await search.fill('no-match');
  await until('successful empty state rendered', () => page.getByText(kind === 'projects' ? 'Không tìm thấy dự án' : kind === 'products' ? 'Không tìm thấy sản phẩm' : 'Không tìm thấy video', { exact: true }).isVisible());
  check('no retry on successful empty', await button(page, 'Thử lại').count() === 0);
}
async function related(page, origin, kind, section, s) {
  const id = kind === 'projects' ? 'project-a' : 'product-a';
  const scope = `/api/v1/${kind}/${id}/${section}`;
  s.failures.add(scope);
  s.delay.add(scope);
  await page.goto(`${origin}/${kind}/${id}`, { waitUntil: 'domcontentloaded' });
  if (kind === 'projects' && section === 'assets') await page.getByRole('button', { name: /Tài nguyên tham chiếu/ }).click();
  await until('related loading shown', () => page.getByRole('status').filter({ hasText: 'Đang tải' }).first().isVisible());
  await until('related initial scoped error', () => page.getByText(/Không tải được.*\(trang 1\)/).isVisible());
  check('initial error does not claim successful empty', await page.getByText(/Chưa có (video|tài nguyên)/).count() === 0);
  check('unrelated edit remains usable', await button(page, 'Chỉnh sửa').isEnabled());
  const before = report.calls.length;
  s.failures.delete(scope);
  await button(page, 'Thử lại').click();
  await until('related retry succeeds', async () => /Trang 1 \/ 2/.test(await nav(page).innerText()));
  const retried = report.calls.slice(before).filter(c => c.method === 'GET');
  check('retry only failed section', retried.length > 0 && retried.every(c => c.scope === scope), retried.map(c => c.scope));
  await layout(page, `${kind}-${section}`);
  check('healthy parent and other section preserved', report.calls.slice(before).every(c => c.scope === scope));
  s.failures.add(`${scope}?page=2`);
  await button(page, 'Trang sau').click();
  await until('page two error shown', () => page.getByText(/Không tải được.*\(trang 2\)/).isVisible());
  check('page two retry available', await button(page, 'Thử lại').isVisible());
  s.failures.delete(`${scope}?page=2`);
  await button(page, 'Thử lại').click();
  await until('page two retry remains scoped to page two', async () => /Trang 2 \/ 2/.test(await nav(page).innerText()));
  check('page two has one long filename or video', await page.locator('tbody tr').count() === 1);
  await button(page, 'Trang trước').click();
  await until('cached first page remains navigable', async () => /Trang 1 \/ 2/.test(await nav(page).innerText()));
}
async function confirmation(page, accept, click) {
  const prompt = page.waitForEvent('dialog', { timeout: TIMEOUT });
  const operation = click();
  const dialog = await prompt;
  check('native confirmation present', dialog.type() === 'confirm');
  if (accept) await dialog.accept(); else await dialog.dismiss();
  await operation;
}
async function revision(page, origin, kind, s) {
  const id = kind === 'projects' ? 'project-a' : 'product-a';
  const scope = `/api/v1/${kind}/${id}`;
  await ready(page, `${origin}/${kind}/${id}`);
  await button(page, 'Chỉnh sửa').click();
  const modal = page.getByRole('dialog');
  const name = modal.locator('#name');
  await name.fill('Unsaved draft');
  s.conflictPatch.add(scope);
  await button(modal, 'Lưu thay đổi').click();
  await until('typed conflict renders modal reload', () => button(modal, 'Tải lại').isVisible());
  check('conflict blocks stale submit', await button(modal, 'Lưu thay đổi').isDisabled());
  check('draft retained after conflict', await name.inputValue() === 'Unsaved draft');
  await button(modal, 'Hủy').focus();
  await page.keyboard.press('Tab');
  check('conflict Tab wraps to enabled close icon', await modal.getByRole('button', { name: 'Close dialog', exact: true }).evaluate(el => el === document.activeElement));
  await page.keyboard.press('Shift+Tab');
  check('conflict reverse Tab wraps to enabled Cancel', await button(modal, 'Hủy').evaluate(el => el === document.activeElement));
  if (currentCase === '360-revision-projects') await page.screenshot({ path: path.join(OUT, 'ui-ux-dialog-360.png'), fullPage: true });
  const before = report.calls.length;
  await confirmation(page, false, () => button(modal, 'Tải lại').click());
  check('cancelled reload preserves draft', await name.inputValue() === 'Unsaved draft');
  check('cancelled reload makes no request', report.calls.length === before);
  await page.keyboard.press('Escape');
  await until('conflict dialog closed while draft retained', async () => await modal.count() === 0);
  await confirmation(page, false, () => button(page, 'Tải lại').click());
  check('outside declined reload makes no request', report.calls.length === before);
  await button(page, 'Chỉnh sửa').click();
  check('retained dirty draft survives outside declined reload', await name.inputValue() === 'Unsaved draft');
  await page.keyboard.press('Escape');
  await confirmation(page, true, () => button(page, 'Tải lại').click());
  await until('outside confirmed reload clears conflict', async () => await button(page, 'Tải lại').count() === 0);
  await button(page, 'Chỉnh sửa').click();
  await until('confirmed reload replaces form with latest data', async () => await name.inputValue() === `Server-current-${id}`);
  check('conflict cleared after recovery', await button(modal, 'Tải lại').count() === 0);
  check('reload does not automatically resubmit', report.calls.slice(before).every(c => c.method === 'GET'));
  check('modal displays revision four', /Revision #4/.test(await modal.innerText()));
  await name.fill('Reviewed retry');
  await button(modal, 'Lưu thay đổi').click();
  await until('reviewed retry closes modal', async () => await modal.count() === 0);
  const patches = report.calls.filter(c => c.case === currentCase && c.method === 'PATCH');
  check('retry uses current revision exactly once', patches.length === 2 && patches[0].revision === '"3"' && patches[1].revision === '"4"', patches.map(c => ({ revision: c.revision, status: c.status })));
  s.conflictArchive.add(scope);
  await confirmation(page, true, () => button(page, 'Lưu trữ').click());
  await until('archive conflict exposes page reload', () => button(page, 'Tải lại').isVisible());
  const archiveBefore = report.calls.filter(c => c.case === currentCase && c.scope.endsWith('/archive')).length;
  await button(page, 'Tải lại').click();
  await until('archive reload clears conflict', async () => await button(page, 'Tải lại').count() === 0);
  check('no automatic archive replay', report.calls.filter(c => c.case === currentCase && c.scope.endsWith('/archive')).length === archiveBefore && archiveBefore === 1);
  await until('archive remains available for explicit review', () => button(page, 'Lưu trữ').isEnabled());
  await layout(page, `${kind}-revision`);
}
async function scopeChange(page, origin, kind) {
  const a = kind === 'projects' ? 'project-a' : 'product-a';
  await ready(page, `${origin}/${kind}/${a}`);
  await next(page);
  await page.getByRole('link', { name: kind === 'projects' ? 'Tất cả dự án' : 'Tất cả sản phẩm', exact: true }).click();
  await until('parent list available for client navigation', () => page.locator(`a[href="/${kind}/${kind}-02"]`).isVisible());
  // Fixture list IDs are known; choose a different actual UI link (no programmatic navigation mutation).
  await page.locator(`a[href="/${kind}/${kind}-02"]`).click();
  await page.waitForURL(`${origin}/${kind}/${kind}-02`);
  await until('new parent scoped request observed', () => report.calls.some(c => c.case === currentCase && c.scope === `/api/v1/${kind}/${kind}-02/${kind === 'projects' ? 'videos' : 'assets'}`));
  await until('new parent begins on page one', async () => /Trang 1 \/ 2/.test(await nav(page).innerText()));
  check('old parent rows absent', !(await page.locator('main').innerText()).includes(`${a}-${kind === 'projects' ? 'videos' : 'assets'}-`));
  check('new parent request scoped to page one', report.calls.some(c => c.case === currentCase && c.scope === `/api/v1/${kind}/${kind}-02/${kind === 'projects' ? 'videos' : 'assets'}` && c.page === '1'));
}
async function emptyRelated(page, origin, kind, s) {
  const id = kind === 'projects' ? 'project-empty' : 'product-empty';
  s.empty.add(`/api/v1/${kind}/${id}/assets`);
  s.empty.add(`/api/v1/${kind}/${id}/videos`);
  await page.goto(`${origin}/${kind}/${id}`, { waitUntil: 'domcontentloaded' });
  await until('genuine related successful empty', () => page.getByText(kind === 'projects' ? 'Chưa có video nào trong dự án' : 'Chưa có asset cho sản phẩm này', { exact: true }).isVisible());
  check('empty offers valid existing action', await page.getByRole('link', { name: kind === 'projects' ? 'Tạo video mới' : 'Tải asset lên', exact: true }).isVisible());
  check('empty has no retry', await button(page, 'Thử lại').count() === 0);
  await layout(page, `${kind}-empty`);
}
async function pendingSave(page, origin, kind, s) {
  const id = kind === 'projects' ? 'project-a' : 'product-a';
  const scope = `/api/v1/${kind}/${id}`;
  let release, received;
  let gate = new Promise(resolve => { release = resolve; });
  let started = new Promise(resolve => { received = resolve; });
  await page.route(`**${scope}`, async route => {
    if (route.request().method() === 'PATCH') { received(); await gate; }
    await route.fallback();
  });
  try {
    await ready(page, `${origin}/${kind}/${id}`);
    await button(page, 'Chỉnh sửa').click();
    const name = page.locator('input#name');
    await name.fill('Submitted A');
    await button(page, 'Lưu thay đổi').click();
    await started;
    check('pending save disables text input', await name.isDisabled());
    check('pending save disables cancel', await button(page, 'Hủy').isDisabled());
    await page.keyboard.press('Escape');
    check('Escape preserves pending dialog', await page.getByRole('dialog').isVisible());
    await page.getByRole('button', { name: 'Close dialog', exact: true }).click();
    check('close icon preserves pending dialog', await page.getByRole('dialog').isVisible());
    release();
    await page.getByRole('dialog').waitFor({ state: 'hidden' });
    await button(page, 'Chỉnh sửa').click();
    check('successful acknowledgment retained canonical name', await name.inputValue() === 'Submitted A');
    gate = new Promise(resolve => { release = resolve; });
    started = new Promise(resolve => { received = resolve; });
    s.failures.add(scope);
    await name.fill('Retained on failure');
    await button(page, 'Lưu thay đổi').click();
    await started;
    check('second pending save locks input', await name.isDisabled());
    release();
    await until('failed save unlocks draft', () => name.isEnabled());
    check('failure keeps original draft and dialog', await name.inputValue() === 'Retained on failure' && await page.getByRole('dialog').isVisible());
    check('failure renders actionable message', (await page.getByRole('dialog').innerText()).includes('Controlled SERVICE_UNAVAILABLE'));
    await layout(page, `${kind}-pending-save`);
  } finally { release(); }
}
async function accessibility(page, origin, create, s) {
  for (const kind of ['projects', 'products']) s.failures.add(`/api/v1/${kind}`);
  if (!create) s.failures.add('/api/v1/videos');
  await page.goto(`${origin}/videos${create ? '/new' : ''}`, { waitUntil: 'domcontentloaded' });
  await until('all scoped retry buttons rendered', async () => await button(page, 'Thử lại').count() === (create ? 2 : 3));
  for (const retry of await button(page, 'Thử lại').all()) {
    const bounds = await retry.boundingBox();
    check('retry has usable touch target', bounds && bounds.height >= 36 && bounds.width >= 24, bounds);
  }
  if (create) {
    await button(page, 'Tạo video & Mở Workspace').click();
    for (const id of ['title', 'project_id', 'brief']) {
      const field = page.locator(`#${id}`);
      await until(`${id} exposes invalid state`, async () => await field.getAttribute('aria-invalid') === 'true');
      check(`${id} error is associated and present`, await field.evaluate(el => {
        const ids = (el.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
        return ids.length > 0 && ids.every(id => document.getElementById(id)?.textContent.trim());
      }));
    }
  } else {
    for (const value of ['QUICK_CLIP', 'DRAFT']) {
      const filter = page.locator('select').filter({ has: page.locator(`option[value="${value}"]`) });
      check(`filter ${value} has accessible name`, await filter.evaluate(el => Boolean(el.getAttribute('aria-label')?.trim() || el.getAttribute('aria-labelledby') || el.labels?.length)));
    }
  }
  const dimensions = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }));
  check('validation/error layout has no page overflow', dimensions.document <= dimensions.viewport + 1, dimensions);
  if (currentCase?.startsWith('360-accessibility-')) await page.screenshot({ path: path.join(OUT, `ui-ux-${create ? 'create' : 'videos'}-360.png`), fullPage: true });
}
async function run() {
  const supplied = process.argv[2];
  if (!supplied) throw new Error('BUILD_READY URL required from main; no server is started by this runner.');
  const base = new URL(supplied);
  assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(base.hostname) && base.protocol === 'http:', 'Main must supply local production URL');
  const origin = base.origin;
  report.origin = origin;
  await fs.mkdir(OUT, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: 'C:/Users/Admin/AppData/Local/ms-playwright/chromium_headless_shell-1234/chrome-headless-shell-win64/chrome-headless-shell.exe', timeout: TIMEOUT });
  report.browser = { version: browser.version(), engine: 'Chromium headless shell', isolatedContexts: true };
  try {
    for (const width of WIDTHS) {
      const definitions = [
        ...['videos', 'projects', 'products'].map(k => [`primary-${k}`, p => primary(p, origin, k)]),
        ['related-project-videos', (p, s) => related(p, origin, 'projects', 'videos', s)],
        ['related-project-assets', (p, s) => related(p, origin, 'projects', 'assets', s)],
        ['related-product-assets', (p, s) => related(p, origin, 'products', 'assets', s)],
        ...['projects', 'products'].map(k => [`revision-${k}`, (p, s) => revision(p, origin, k, s)]),
        ...['projects', 'products'].map(k => [`pending-save-${k}`, (p, s) => pendingSave(p, origin, k, s)]),
        ...['projects', 'products'].map(k => [`scope-${k}`, p => scopeChange(p, origin, k)]),
        ...['projects', 'products'].map(k => [`empty-${k}`, (p, s) => emptyRelated(p, origin, k, s)]),
        ['accessibility-videos', (p, s) => accessibility(p, origin, false, s)],
        ['accessibility-create', (p, s) => accessibility(p, origin, true, s)],
      ];
      for (const [name, action] of definitions) {
        currentCase = `${width}-${name}`;
        const start = report.checks.length;
        const s = state();
        const { context, page } = await setup(browser, origin, width, s);
        const result = { id: currentCase, width, name, status: 'PASS' };
        try {
          await action(page, s);
          check('no hydration or runtime page errors', report.pageErrors.filter(e => e.case === currentCase).length === 0);
          check('no unexpected console errors', !report.console.some(e => e.case === currentCase && e.type === 'error' && e.category === 'unexpected'));
          check('no blocked or unmatched backend/provider traffic', !report.blockedTraffic.some(e => e.case === currentCase));
        } catch (err) { result.status = 'FAIL'; result.error = err.stack; }
        finally {
          if (currentCase === '360-primary-videos') { try { await page.screenshot({ path: path.join(OUT, 'group3-viewports.png'), fullPage: true, timeout: TIMEOUT }); result.screenshot = 'tasks/evidence/group3-viewports.png'; } catch (err) { result.screenshotError = err.message; } }
          result.checks = report.checks.length - start;
          report.cases.push(result);
          await context.close();
          console.log(`${result.status} ${currentCase}: ${result.checks} checks${result.error ? ' ' + result.error.split('\n')[0] : ''}`);
          await fs.writeFile(RESULTS, JSON.stringify(report, null, 2));
        }
      }
    }
    report.status = report.cases.every(c => c.status === 'PASS') && report.checks.every(c => c.pass) ? 'PASS' : 'FAIL';
    report.counts = { cases: report.cases.length, passedCases: report.cases.filter(c => c.status === 'PASS').length, failedCases: report.cases.filter(c => c.status === 'FAIL').length, checks: report.checks.length, passedChecks: report.checks.filter(c => c.pass).length, failedChecks: report.checks.filter(c => !c.pass).length, calls: report.calls.length, controlledConsoleErrors: report.console.filter(c => c.category === 'controlled-http-resource-error').length, unexpectedConsoleErrors: report.console.filter(c => c.type === 'error' && c.category === 'unexpected').length, warnings: report.console.filter(c => c.type === 'warning').length, pageErrors: report.pageErrors.length, blockedTraffic: report.blockedTraffic.length };
  } finally {
    await browser.close();
    await fs.writeFile(RESULTS, JSON.stringify(report, null, 2));
    report.coverage = 'Primary navigation/search/filter reset; related loading/errors/scoped retry/page 2; edit/archive conflict; dirty confirm false/true; current revision retry; no automatic archive; client scope change; genuine empty; overflow and console/hydration.';
    report.limitations = 'Fixture API only. No real DB/provider/auth/CSRF or full component/backend matrix claim.';
    await fs.writeFile(RESULTS, JSON.stringify(report, null, 2));
  }
  console.log('UI_BROWSER:', report.status, JSON.stringify(report.counts));
  if (report.status !== 'PASS') process.exitCode = 1;
}
if (require.main === module) run().catch(async err => { console.error(err); process.exitCode = 1; });
module.exports = { setup, ready, WIDTHS };
