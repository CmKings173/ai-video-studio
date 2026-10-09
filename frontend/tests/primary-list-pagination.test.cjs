const assert = require('node:assert/strict');
const test = require('node:test');
const React = require('react');
const { loadTypeScript } = require('./load-typescript.cjs');

function textOf(node) {
  if (node == null || typeof node === 'boolean') return '';
  if (typeof node !== 'object') return String(node);
  if (Array.isArray(node)) return node.map(textOf).join('');
  if (typeof node.type === 'function') return textOf(node.type(node.props));
  return textOf(node.props?.children);
}
function nodes(node) {
  if (Array.isArray(node)) return node.flatMap(nodes);
  if (!node || typeof node !== 'object') return [];
  if (typeof node.type === 'function') return nodes(node.type(node.props));
  return [node, ...React.Children.toArray(node.props?.children).flatMap(nodes)];
}
const button = (tree, label) => nodes(tree).find(n => n.type === 'button' && textOf(n) === label);
function harness(resource, total) {
  const states = [], cache = new Map(), calls = [], keys = [];
  let cursor = 0, current, failed = false, emptyPage = false, fetching = false;
  const host = type => ({ children, ...props }) => React.createElement(type, props, children);
  const mocks = {
    react: { ...React, useState(initial) {
      const index = cursor++;
      if (!(index in states)) states[index] = typeof initial === 'function' ? initial() : initial;
      return [states[index], value => { states[index] = typeof value === 'function' ? value(states[index]) : value; }];
    } },
    'next/link': host('a'),
    '@tanstack/react-query': {
      useQuery(options) {
        current = options;
        keys.push(options.queryKey);
        const key = JSON.stringify(options.queryKey);
        const data = cache.get(key);
        return { data, isLoading: !data && !failed, isFetching: fetching, error: failed ? new Error('Page request failed') : null,
          refetch: async () => { failed = false; await load(); } };
      },
      useInfiniteQuery: () => ({ data: { pages: [{ items: [], page: 1, page_size: 50, total: 0 }] }, hasNextPage: false }),
      useQueryClient: () => ({ invalidateQueries: () => {} }),
      useMutation: () => ({ mutateAsync: async () => {} }),
    },
    'react-hook-form': { useForm: () => ({ register: () => ({}), handleSubmit: () => () => {}, reset: () => {}, formState: { errors: {}, isSubmitting: false } }) },
    '@/components/list-pagination': { PaginationControls: props => loadTypeScript('components/list-pagination.tsx', { '@/components/ui/button': { Button: host('button') } }).PaginationControls(props) },
    '@/components/ui/button': { Button: host('button') },
    '@/components/ui/dialog': { Dialog: () => null },
    '@/components/ui/alert': { Alert: ({ title, children }) => React.createElement('div', { role: 'alert' }, title, children) },
    '@/components/page-kit': {
      PageHeader: host('header'), StatusPill: () => null,
      EmptyState: ({ title, detail }) => React.createElement('div', { role: 'status' }, title, detail),
      Card: ({ title, href, children }) => React.createElement('a', { href }, title, children),
    },
    '@/lib/api/videos': { listVideos: list },
    '@/lib/api/projects': { listProjects: list, createProject: async () => {} },
    '@/lib/api/products': { listProducts: list, createProduct: async () => {} },
    '@/lib/api/brands': { listBrands: async () => ({}), createBrand: async () => {} },
  };
  async function list(params = {}) {
    calls.push(params);
    const page = params.page ?? 1, size = params.size ?? 20;
    const scope = params.search || params.project_id || params.product_id || params.kind || params.status || params.brand_id || (params.archived === true ? 'archived' : 'default');
    const start = (page - 1) * size;
    const items = emptyPage ? [] : Array.from({ length: Math.max(0, Math.min(size, total - start)) }, (_, i) => ({
      id: `${scope}-${start + i + 1}`, title: `${scope}-${start + i + 1}`, name: `${scope}-${start + i + 1}`,
      created_at: '2026-01-01', kind: 'QUICK_CLIP', status: 'READY', target_duration: 5,
    }));
    return { items, total, page, page_size: size };
  }
  const Page = loadTypeScript(`app/${resource}/page.tsx`, mocks).default;
  function render() { cursor = 0; return Page(); }
  async function load() { const key = JSON.stringify(current.queryKey); cache.set(key, await current.queryFn()); }
  function ids(tree) { return [...new Set(nodes(tree).filter(n => n.type === 'a' && new RegExp(`^/${resource}/[^/]+$`).test(n.props.href)).filter(n => n.props.href !== `/${resource}/new`).map(n => n.props.href.split('/').at(-1)))]; }
  return { render, load, calls, keys, ids, get query() { return current; }, fail() { failed = true; }, busy(value) { fetching = value; }, empty() { emptyPage = true; } };
}
for (const resource of ['videos', 'projects', 'products']) {
  for (const total of [0, 1, 19, 20, 21, 40, 41, 101]) {
    test(`${resource}: ${total} rows reachable through bounded real pagination callbacks`, async () => {
      const h = harness(resource, total);
      h.render(); await h.load(); let tree = h.render();
      assert.equal(h.calls.length, 1, 'one initial request only');
      assert.ok(nodes(tree).some(n => n.type === 'nav' && n.props['aria-label'] === 'Phân trang'));
      assert.match(textOf(tree), new RegExp(`${total} mục`));
      assert.equal(h.calls[0].page, 1); assert.equal(h.calls[0].size, 20);
      const seen = [];
      for (let page = 1; page <= Math.max(1, Math.ceil(total / 20)); page++) {
        const prev = button(tree, 'Trang trước'), next = button(tree, 'Trang sau');
        assert.ok(prev && next, 'render actual pagination buttons');
        assert.equal(Boolean(prev.props.disabled), page === 1);
        assert.equal(Boolean(next.props.disabled), page * 20 >= total);
        assert.deepEqual(JSON.parse(JSON.stringify(h.query.queryKey.at(-1))), JSON.parse(JSON.stringify(h.calls.at(-1))));
        seen.push(...h.ids(tree));
        if (!next.props.disabled) { next.props.onClick(); h.render(); await h.load(); tree = h.render(); assert.equal(h.calls.at(-1).page, page + 1); }
      }
      assert.equal(seen.length, total); assert.equal(new Set(seen).size, total);
      assert.equal(h.calls.length, Math.max(1, Math.ceil(total / 20)), 'no background page downloads');
      if (total > 20) { button(tree, 'Trang trước').props.onClick(); h.render(); await h.load(); tree = h.render(); assert.equal(h.calls.at(-1).page, Math.ceil(total / 20) - 1); }
    });
  }
  test(`${resource}: page two failure offers scoped retry and previous without false empty`, async () => {
    const h = harness(resource, 41); h.render(); await h.load(); let tree = h.render();
    button(tree, 'Trang sau').props.onClick(); h.fail(); tree = h.render();
    assert.match(textOf(tree), /Page request failed/);
    assert.doesNotMatch(textOf(tree), /Không tìm thấy/);
    assert.ok(!button(tree, 'Trang trước').props.disabled);
    assert.ok(button(tree, 'Trang sau').props.disabled);
    await button(tree, 'Thử lại').props.onClick(); tree = h.render();
    assert.equal(h.calls.at(-1).page, 2); assert.equal(h.ids(tree)[0], 'default-21');
    button(tree, 'Trang trước').props.onClick(); h.render(); await h.load(); assert.equal(h.calls.at(-1).page, 1);
  });
  test(`${resource}: empty page two is distinct from empty dataset and permits returning`, async () => {
    const h = harness(resource, 21); h.render(); await h.load(); let tree = h.render();
    button(tree, 'Trang sau').props.onClick(); h.empty(); h.render(); await h.load(); tree = h.render();
    assert.match(textOf(tree), /Trang này không có/);
    assert.ok(!button(tree, 'Trang trước').props.disabled);
  });
  test(`${resource}: in-flight page navigation and retry are disabled`, async () => {
    const h = harness(resource, 41); h.render(); await h.load(); let tree = h.render();
    button(tree, 'Trang sau').props.onClick(); h.render(); await h.load();
    h.busy(true); tree = h.render(); const key = JSON.stringify(h.query.queryKey);
    assert.ok(button(tree, 'Trang trước').props.disabled);
    assert.ok(button(tree, 'Trang sau').props.disabled);
    button(tree, 'Trang trước').props.onClick(); button(tree, 'Trang sau').props.onClick(); h.render();
    assert.equal(JSON.stringify(h.query.queryKey), key, 'disabled callbacks cannot change requested page');
    h.fail(); tree = h.render();
    assert.ok(button(tree, 'Đang tải...').props.disabled);
    const count = h.calls.length;
    button(tree, 'Đang tải...').props.onClick();
    assert.equal(h.calls.length, count, 'active retry cannot issue duplicate requests');
  });
  const filters = resource === 'videos' ? [['select', 'QUICK_CLIP'], ['select', 'READY'], ['select', 'project-new'], ['select', 'product-new']] : resource === 'projects' ? [['button', 'Đã lưu trữ']] : [['select', 'brand-new']];
  for (const [type, value] of [['search', 'needle'], ...filters]) {
    test(`${resource}: ${value} resets page two and isolates results/query key`, async () => {
      const h = harness(resource, 41); h.render(); await h.load(); let tree = h.render();
      button(tree, 'Trang sau').props.onClick(); h.render(); await h.load(); tree = h.render();
      const oldKey = JSON.stringify(h.query.queryKey);
      const control = type === 'search' ? nodes(tree).find(n => n.type === 'input' && n.props.type === 'text')
        : type === 'button' ? button(tree, value)
        : nodes(tree).find(n => n.type === 'select' && (value === 'project-new' ? n.props['aria-label'] === 'Lọc theo dự án' : value === 'product-new' ? n.props['aria-label'] === 'Lọc theo sản phẩm' : value === 'brand-new' ? n.props.id === 'brand_filter' : value === 'READY' ? n.props.id === 'video_status_filter' : value === 'QUICK_CLIP' ? n.props.id === 'video_kind_filter' : textOf(n).includes(value)));
      assert.ok(control, 'filter is rendered');
      if (type === 'button') control.props.onClick(); else control.props.onChange({ target: { value } });
      tree = h.render();
      assert.equal(h.ids(tree).length, 0, 'old scope rows must disappear before new scope loads');
      assert.notEqual(JSON.stringify(h.query.queryKey), oldKey);
      assert.ok(!h.query.placeholderData, 'no previous-scope placeholders');
      await h.load(); tree = h.render(); assert.equal(h.calls.at(-1).page, 1);
      const params = h.calls.at(-1);
      assert.ok(Object.values(params).includes(type === 'button' ? true : value));
      button(tree, 'Trang sau').props.onClick(); h.render(); await h.load();
      assert.deepEqual(h.calls.at(-1), { ...params, page: 2 });
    });
  }
}
