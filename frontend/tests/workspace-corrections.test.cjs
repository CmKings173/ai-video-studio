const test = require('node:test');
const assert = require('node:assert/strict');
const { loadTypeScript } = require('./load-typescript.cjs');
const React = require('react');
const { pageHarness } = require('./load-typescript.cjs');
const state = () => loadTypeScript('lib/generation/workspace-state.ts');
const scene = (n, fresh, continuity = 'CUT') => ({ id: `s${n}`, enabled: true, scene_order: n - 1, spec: { continuity }, selected_generation_id: fresh === null ? null : `g${n}`, selected_generation_fresh: fresh });

test('selection identity stays distinct from freshness and historical page membership', async () => {
  const { selectionState, loadSelectedGeneration } = state();
  assert.equal(selectionState(scene(1, null)), 'NO_SELECTION');
  assert.equal(selectionState(scene(1, true)), 'FRESH_SELECTION');
  assert.equal(selectionState(scene(1, false)), 'STALE_SELECTION');
  const older = { id: 'g1', output_asset_id: 'old-asset' };
  const page = Array.from({ length: 20 }, (_, i) => ({ id: `recent${i}` }));
  const fetched = [];
  assert.equal(await loadSelectedGeneration('g1', page, async id => { fetched.push(id); return older; }), older);
  assert.deepEqual(fetched, ['g1']);
  assert.equal(await loadSelectedGeneration('g1', [older], () => { throw Error('must use page'); }), older);
});

test('continuity editing preserves unknown spec and enforces first scene CUT', () => {
  const { editSceneSpec } = state();
  const original = { continuity: 'CONTINUOUS', future: { custom: true }, camera: 'dolly' };
  assert.deepEqual(editSceneSpec(original, { title: 'New', continuity: 'CONTINUOUS' }, 0), { ...original, title: 'New', continuity: 'CUT' });
  assert.equal(editSceneSpec(original, { title: 'New' }, 2).continuity, 'CONTINUOUS');
});

test('batch group reason names the changed member without claiming fresh members missing', () => {
  const { executionGroups } = state();
  const scenes = [scene(1, true), scene(2, false, 'CONTINUOUS'), scene(3, true, 'CONTINUOUS'), scene(4, null)];
  const groups = executionGroups(scenes);
  assert.equal(groups[0].label, 'Cảnh 1–3');
  assert.equal(groups[0].execution, 'Nhóm Director');
  assert.deepEqual(groups[0].reasons, ['Cảnh 2 đã thay đổi']);
  assert.equal(groups[0].members.length, 3);
  assert.equal(groups[1].execution, 'Độc lập');
  assert.deepEqual(groups[1].reasons, ['Cảnh 4 chưa chọn clip']);
  assert.equal(executionGroups([scene(1, true)])[0].eligible, false);
});

function nodes(node) {
  return !node || typeof node !== 'object' ? [] : [node, ...React.Children.toArray(node.props?.children).flatMap(nodes)];
}
function workspace(scenes, withRunning = false) {
  const video = { id: 'video', revision: 4, kind: 'LONG_VIDEO', config: {}, scenes };
  const recent = Array.from({ length: 20 }, (_, i) => ({ id: `recent${i}`, status: 'COMPLETED' }));
  const requests = [], queries = [], states = [], saves = [];
  let stateIndex = 0, historical;
  const h = pageHarness(video, {
    '@/lib/api/generations': {
      listSceneGenerations: async (...args) => { requests.push(args); return { items: recent, total: 25 }; },
      getGeneration: async id => { requests.push(id); return { id, generation_no: 1, status: 'COMPLETED', output_asset_id: 'historical-asset' }; },
    },
    '@/lib/api/scenes': { patchScene: async (...args) => saves.push(args), createScene: async (...args) => saves.push(args) },
    'react-hook-form': { useForm: () => ({ register: name => ({ name }), handleSubmit: fn => fn, reset: () => {}, formState: { errors: {} } }) },
  });
  h.mocks.react.useEffect = () => {};
  h.mocks.react.useState = initial => {
    const index = stateIndex++;
    if (!(index in states)) states[index] = typeof initial === 'function' ? initial() : initial;
    return [states[index], value => { states[index] = typeof value === 'function' ? value(states[index]) : value; }];
  };
  h.mocks['@tanstack/react-query'].useQuery = options => {
    queries.push(options);
    const data = options.queryKey[0] === 'videos' ? video
      : options.queryKey[0] === 'generations' ? historical
      : options.queryKey.includes('generations') ? { items: withRunning && options.queryKey.at(-1).page === 1 ? [{ id: 'running', status: 'RUNNING', progress_current: 1, progress_total: 2 }, ...recent] : recent, total: 25 } : undefined;
    return { data, isLoading: false, refetch: async () => ({ data: video }) };
  };
  const page = loadTypeScript('app/videos/[videoId]/page.tsx', h.mocks).default;
  return {
    ...h, video, requests, queries, saves, states,
    render() { stateIndex = 0; queries.length = 0; h.mutations.length = 0; return page({ params: { videoId: video.id } }); },
    hydrate(value) { historical = value; },
  };
}

test('workspace fetches selected clip beyond 20, previews stale asset, and paginates bounded history', async () => {
  const h = workspace([scene(1, false)]);
  h.render();
  const detail = h.queries.find(q => q.queryKey[0] === 'generations');
  assert.equal(detail.enabled, true);
  h.hydrate(await detail.queryFn());
  const tree = h.render();
  assert.ok(nodes(tree).some(n => n.props?.assetId === 'historical-asset'));
  assert.ok(nodes(tree).some(n => String(n.props?.children).includes('Clip đã chọn không còn khớp')));
  assert.equal(nodes(tree).some(n => n.type === 'span' && String(n.props?.className).includes('text-emerald')), false);
  const history = h.queries.find(q => q.queryKey[0] === 'scenes' && q.queryKey.includes('generations'));
  await history.queryFn();
  nodes(tree).find(n => n.props?.children === 'Trang sau').props.onClick();
  h.render();
  await h.queries.find(q => q.queryKey[0] === 'scenes' && q.queryKey.includes('generations')).queryFn();
  assert.deepEqual(h.requests, ['g1', ['s1', { page: 1, size: 20 }], ['s1', { page: 2, size: 20 }]]);
  assert.deepEqual(history.queryKey.slice(0, 4), ['scenes', 's1', 'generations', undefined]);
});

test('workspace disables zero-eligible Generate All and gives stale chain primary CTA', () => {
  for (const fresh of [true, false]) {
    const h = workspace([scene(1, true), scene(2, fresh, 'CONTINUOUS'), scene(3, true, 'CONTINUOUS')]);
    const tree = h.render();
    const action = nodes(tree).find(n => n.props?.onClick && React.Children.toArray(n.props.children).some(child => child.props?.children === (fresh ? 'Tất cả cảnh đã cập nhật.' : 'Tạo 3 cảnh')));
    assert.ok(action);
    assert.equal(action.props.disabled, fresh);
    assert.equal(action.props.variant, fresh ? 'secondary' : 'primary');
    const assembly = nodes(tree).find(n => n.props?.href === '/videos/video/assembly');
    assert.ok(assembly.props.className.startsWith(fresh ? 'primary-action' : 'secondary-action'));
  }
});

test('preview fallback heading uses one-based scene numbering', () => {
  const h = workspace([scene(1, null)]);
  const tree = h.render();
  assert.ok(nodes(tree).some(n => n.type === 'h2' && n.props.children === 'Cảnh 1'));
  assert.equal(nodes(tree).some(n => n.props?.children === 'Cảnh 0'), false);
});

test('paging older history preserves latest running generation progress', () => {
  const h = workspace([scene(1, null)], true);
  const initial = h.render();
  nodes(initial).find(n => n.props?.children === 'Trang sau').props.onClick();
  const older = h.render();
  assert.ok(nodes(older).some(n => n.props?.value === 50));
});

test('workspace mutations clear prior server errors on retry and success', () => {
  const h = workspace([scene(1, null)]);
  h.render();
  const reorder = h.mutations[0];
  reorder.onError(Error('old conflict'));
  assert.ok(JSON.stringify(h.states).includes('old conflict'));
  for (const mutation of h.mutations) {
    assert.equal(typeof mutation.onMutate, 'function');
    mutation.onMutate();
    assert.equal(JSON.stringify(h.states).includes('old conflict'), false);
  }
  reorder.onError(Error('second conflict'));
  reorder.onSuccess();
  assert.equal(JSON.stringify(h.states).includes('second conflict'), false);
});

test('edit/add dialogs send continuity through API and preserve unknown scene spec', async () => {
  for (const order of [0, 1]) {
    const row = { ...scene(order + 1, null, 'CONTINUOUS'), spec: { continuity: 'CONTINUOUS', future: { custom: true } } };
    const h = workspace([row]);
    const tree = h.render();
    const edit = nodes(tree).find(n => n.type?.name === 'EditSceneDialog');
    const dialog = edit.type({ ...edit.props, isOpen: true });
    const select = nodes(dialog).find(n => n.props?.id === 'scene_continuity');
    assert.equal(nodes(select).some(n => n.props?.value === 'CONTINUOUS'), order > 0);
    await h.mutations.at(-1).mutationFn({ title: 'Edited', purpose: 'HOOK', prompt: 'New', duration_seconds: 5, continuity: 'CONTINUOUS' });
    assert.deepEqual(h.saves[0][1].spec.future, { custom: true });
    assert.equal(h.saves[0][1].spec.continuity, order === 0 ? 'CUT' : 'CONTINUOUS');
    const add = nodes(tree).find(n => n.type?.name === 'AddSceneDialog');
    add.type({ ...add.props, isOpen: true, sceneCount: order });
    await h.mutations.at(-1).mutationFn({ title: 'Added', purpose: 'HOOK', prompt: 'New', duration_seconds: 5, continuity: 'CONTINUOUS' });
    assert.equal(h.saves[1][1].spec.continuity, order === 0 ? 'CUT' : 'CONTINUOUS');
  }
});

test('shared buttons retain visible keyboard focus styles', () => {
  const { Button } = loadTypeScript('components/ui/button.tsx');
  const button = Button.render({ children: 'Action' }, null);
  assert.match(button.props.className, /focus-visible:ring-2/);
  assert.match(button.props.className, /focus-visible:ring-blue-400/);
});
