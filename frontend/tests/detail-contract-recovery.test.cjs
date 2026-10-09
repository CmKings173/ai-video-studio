const assert = require('node:assert/strict');
const test = require('node:test');
const React = require('react');
const { loadTypeScript } = require('./load-typescript.cjs');
const errors = loadTypeScript('lib/api/errors.ts');
function nodes(n) { if (!n || typeof n !== 'object')
    return []; return [n, ...React.Children.toArray(n.props?.children).flatMap(nodes)]; }
function text(n) { if (typeof n === 'string' || typeof n === 'number')
    return String(n); return React.Children.toArray(n?.props?.children).map(text).join(' '); }
function button(tree, label) { return nodes(tree).find(n => n.type === 'button' && text(n).includes(label)); }
function item(section, id = 'cached') { return section === 'videos' ? { id, title: id, target_duration: 5 } : { id, filename: id, size_bytes: 100 }; }
function harness(kind) {
    let states = [], refs = [], cursor = 0, refCursor = 0, parent = 'one', bodyKey, dirty = false, draft = {}, queries = [], mutations = [], reloads = 0, reloadError, reloadWait;
    let saving = false;
    let resource = { id: parent, name: 'Original', description: 'Old', revision: 3, context: { tone: 'warm', keep: true } };
    const related = { videos: {}, assets: {} }, requests = [], retries = [], resets = [];
    const cap = s => s[0].toUpperCase() + s.slice(1);
    const react = { ...React, use: p => p, useState: initial => { const i = cursor++; if (!(i in states))
            states[i] = typeof initial === 'function' ? initial() : initial; return [states[i], v => { states[i] = typeof v === 'function' ? v(states[i]) : v; }]; }, useRef: initial => refs[refCursor++] ||= { current: initial }, useCallback: f => f };
    const mocks = { react, '@/lib/api/errors': errors,
        'next/link': p => React.createElement('a', p, p.children),
        '@/components/ui/button': { Button: p => React.createElement('button', p, p.children) },
        '@/components/ui/alert': { Alert: p => React.createElement('div', { role: 'alert' }, p.title, p.children) },
        '@/components/page-kit': { PageHeader: p => React.createElement('header', {}, p.title, p.eyebrow, p.children), EmptyState: p => React.createElement('div', {}, p.title, p.detail), StatusPill: () => null, QueryErrorNotice: p => React.createElement('div', { role: 'alert' }, p.title, p.message, React.createElement('button', { onClick: p.onRetry, disabled: p.isRetrying }, 'Thử lại')) },
        '@/components/ui/dialog': { Dialog: p => p.isOpen ? React.createElement('dialog', { onClose: p.onClose }, p.description, p.children) : null },
        '@/components/ui/tabs': { Tabs: p => React.createElement('div', {}, p.tabs.map(t => React.createElement('button', { key: t.id, onClick: () => p.onChange(t.id) }, t.label))) },
        'react-hook-form': { useForm: () => ({ register: () => ({}), control: {}, setValue: (k, v) => draft[k] = v, reset: v => { draft = { ...v }; dirty = false; resets.push(v); }, handleSubmit: fn => () => fn(draft), formState: { errors: {}, isSubmitting: saving, isDirty: dirty } }), useWatch: () => '' },
        '@hookform/resolvers/zod': { zodResolver: () => () => { } },
        '@tanstack/react-query': {
            useQueryClient: () => ({ invalidateQueries: async () => { } }),
            useQuery: o => { queries.push(o); const k = o.queryKey; if (k[0] === kind + 's' && k[1] === 'detail')
                return { data: resource, isLoading: false, error: reloadError, refetch: async () => { reloads++; if (reloadWait)
                        await reloadWait; if (reloadError)
                        return { error: reloadError, isError: true, data: resource }; resource = { ...resource, name: 'Current', revision: 4 }; return { data: resource, isSuccess: true }; } }; const section = k[2]; if (section === 'videos' || section === 'assets') {
                const page = k.at(-1)?.page || 1;
                return { data: { items: [], page, page_size: 20, total: 0 }, isLoading: false, isSuccess: true, isFetching: false, ...related[section][page], refetch: async () => { retries.push(section + page); related[section][page] = { data: { items: [item(section, 'retried')], page, page_size: 20, total: 21 } }; } };
            } return { data: undefined }; },
            useInfiniteQuery: () => ({ data: { pages: [] } }),
            useMutation: o => { mutations.push(o); return { isPending: false, mutate: () => o.mutationFn(), mutateAsync: async (d) => { try {
                    await o.mutationFn(d);
                }
                catch (e) {
                    o.onError(e);
                } } }; },
        },
        ['@/lib/api/' + kind + 's']: { ['get' + cap(kind)]: async () => resource, ['patch' + cap(kind)]: async (...a) => requests.push(a), ['archive' + cap(kind)]: async (...a) => requests.push(a), ['get' + cap(kind) + 'Assets']: async (...a) => requests.push(a), getProjectVideos: async (...a) => requests.push(a) },
    };
    mocks['@/components/page-kit'].QueryErrorNotice = loadTypeScript('components/page-kit.tsx', mocks).QueryErrorNotice;
    for (const [alias, file] of [['@/components/related-resource-state', 'components/related-resource-state.tsx'], ['@/components/list-pagination', 'components/list-pagination.tsx']]) {
        if (require('node:fs').existsSync(require('node:path').resolve(__dirname, '..', file)))
            mocks[alias] = loadTypeScript(file, mocks);
    }
    const page = loadTypeScript(`app/${kind}s/[${kind}Id]/page.tsx`, mocks).default;
    function expand(n) { if (!n || typeof n !== 'object')
        return n; if (typeof n.type === 'function') {
        if (n.key && String(n.key).startsWith('detail-') && bodyKey !== n.key) {
            states = [];
            refs = [];
            bodyKey = n.key;
        }
        return expand(n.type(n.props));
    } return { ...n, props: { ...n.props, children: React.Children.toArray(n.props?.children).map(expand) } }; }
    return { set saving(value) { saving = value; }, render: () => { cursor = 0; refCursor = 0; queries = []; mutations = []; return expand(page({ params: { [kind + 'Id']: parent } })); }, related, get queries() { return queries; }, get mutations() { return mutations; }, requests, retries, resets, get draft() { return draft; }, set draft(v) { draft = v; }, set dirty(v) { dirty = v; }, set reloadError(v) { reloadError = v; }, get reloads() { return reloads; }, set reloadWait(v) { reloadWait = v; }, set resource(v) { resource = { ...resource, ...v }; }, changeParent: () => { parent = 'two'; resource = { ...resource, id: parent, name: 'Other' }; related.assets = {}; related.videos = {}; } };
}
for (const kind of ['project', 'product']) {
    test(`${kind}: typed conflict renders Reload inside edit dialog`, () => { const h = harness(kind); button(h.render(), 'Chỉnh sửa').props.onClick(); h.render(); h.mutations[0].onError(new errors.ApiClientError(412, { error: { code: 'REVISION_CONFLICT', message: 'conflict' } })); const tree = h.render(); const dialog = nodes(tree).find(n => n.type === 'dialog'); assert.ok(button(dialog, 'Tải lại')); assert.equal(button(dialog, 'Lưu thay đổi').props.disabled, true); });
    test(`${kind}: related item 21 is reachable with scoped pagination`, async () => { const h = harness(kind); h.related.assets[1] = { data: { items: [item('assets')], page: 1, page_size: 20, total: 21 } }; let tree = h.render(); if (kind === 'project') {
        button(tree, 'Tài nguyên tham chiếu').props.onClick();
        tree = h.render();
    } const next = button(tree, 'Trang sau'); assert.ok(next); next.props.onClick(); h.render(); const q = h.queries.find(q => q.queryKey[2] === 'assets'); assert.deepEqual(q.queryKey.at(-1), { page: 2, size: 20 }); await q.queryFn(); assert.deepEqual(h.requests.at(-1), ['one', { page: 2, size: 20 }]); });
    test(`${kind}: initial related error is not empty`, () => { const h = harness(kind); h.related.assets[1] = { data: undefined, error: new Error('offline'), isSuccess: false }; let tree = h.render(); if (kind === 'project') {
        button(tree, 'Tài nguyên tham chiếu').props.onClick();
        tree = h.render();
    } assert.ok(button(tree, 'Thử lại')); assert.ok(!text(tree).includes('Chưa có')); assert.ok(text(tree).includes('offline')); });
}
for (const kind of ['project', 'product']) {
    test(`${kind}: non-conflict HTTP 412 has no Reload action`, () => { const h = harness(kind); button(h.render(), 'Chỉnh sửa').props.onClick(); h.render(); h.mutations[0].onError(new errors.ApiClientError(412, { error: { code: 'UNRELATED_PRECONDITION', message: 'unrelated' } })); const tree = h.render(); assert.equal(button(tree, 'Tải lại'), undefined); assert.equal(button(tree, 'Lưu thay đổi').props.disabled, false); });
}
const conflictError = () => new errors.ApiClientError(412, { error: { code: 'REVISION_CONFLICT', message: 'conflict' } });
for (const kind of ['project', 'product']) {
    test(`${kind}: pending save locks fields and prevents close or reopen from replacing draft`, () => {
        const h = harness(kind);
        button(h.render(), 'Chỉnh sửa').props.onClick();
        h.draft = { name: 'Submitted A', description: 'A', tone: 'warm' };
        h.dirty = true;
        h.saving = true;
        let tree = h.render();
        assert.equal(nodes(tree).find(n => n.type === 'fieldset').props.disabled, true);
        assert.equal(button(tree, 'Hủy').props.disabled, true);
        const resets = h.resets.length;
        nodes(tree).find(n => n.type === 'dialog').props.onClose();
        tree = h.render();
        assert.ok(nodes(tree).some(n => n.type === 'dialog'), 'Escape/backdrop/close must keep pending form open');
        button(tree, 'Chỉnh sửa').props.onClick();
        assert.equal(h.resets.length, resets, 'open handler must not reset a pending save');
        assert.equal(h.draft.name, 'Submitted A');
        h.saving = false;
        h.mutations[0].onError(new Error('save failed'));
        tree = h.render();
        assert.equal(nodes(tree).find(n => n.type === 'fieldset').props.disabled, false);
        assert.equal(h.draft.name, 'Submitted A', 'failure retains editable draft');
    });
}
for (const kind of ['project', 'product']) {
    test(`${kind}: successful edit clears saved dirty state and prior error before archive recovery`, async () => {
        const h = harness(kind);
        button(h.render(), 'Chỉnh sửa').props.onClick();
        h.draft = { name: 'Saved', description: 'Saved description', tone: 'warm' };
        h.dirty = true;
        h.render();
        h.mutations[0].onError(new errors.ApiClientError(422, { error: { code: 'VALIDATION_ERROR', message: 'old failure' } }));
        h.render();
        h.mutations[0].onSuccess({ id: 'one', name: 'Saved', description: 'Saved description', revision: 4, context: { tone: 'warm' } });
        let tree = h.render();
        assert.doesNotMatch(text(tree), /old failure/);
        h.mutations[1].onError(conflictError());
        tree = h.render();
        await withConfirm(false, async () => button(tree, 'Tải lại').props.onClick());
        assert.equal(h.reloads, 1, 'a saved clean draft must not require discard confirmation');
        assert.deepEqual(h.requests, [], 'archive recovery never retries automatically');
    });
    test(`${kind}: outside Reload protects retained dirty draft after closing conflict dialog`, async () => {
        const h = harness(kind);
        editConflict(h);
        h.draft = { name: 'Retained draft', description: 'Unsaved' };
        h.dirty = true;
        button(h.render(), 'Hủy').props.onClick();
        const resetsBefore = h.resets.length;
        await withConfirm(false, async () => button(h.render(), 'Tải lại').props.onClick());
        assert.equal(h.reloads, 0);
        assert.equal(h.resets.length, resetsBefore);
        assert.equal(h.draft.name, 'Retained draft');
        assert.ok(button(h.render(), 'Tải lại'));
        await withConfirm(true, async () => button(h.render(), 'Tải lại').props.onClick());
        assert.equal(h.reloads, 1);
        assert.equal(h.draft.name, 'Current');
        assert.equal(button(h.render(), 'Tải lại'), undefined);
        assert.deepEqual(h.requests, [], 'recovery never automatically retries a mutation');
    });
}
function editConflict(h, index = 0) {
    button(h.render(), 'Chỉnh sửa').props.onClick();
    h.render();
    h.mutations[index].onError(conflictError());
    return h.render();
}
function showSection(h, kind, section) {
    let tree = h.render();
    if (kind === 'project') {
        button(tree, section === 'assets' ? 'Tài nguyên tham chiếu' : 'Video trong dự án').props.onClick();
        tree = h.render();
    }
    return tree;
}
function withConfirm(value, fn) {
    const previous = global.confirm;
    global.confirm = () => value;
    return Promise.resolve().then(fn).finally(() => { global.confirm = previous; });
}
for (const kind of ['project', 'product']) {
    for (const [status, code] of [[409, 'OTHER_CONFLICT'], [422, 'VALIDATION_ERROR'], [403, 'FORBIDDEN']]) {
        test(`${kind}: typed ${status} ${code} preserves message without Reload`, () => {
            const h = harness(kind);
            button(h.render(), 'Chỉnh sửa').props.onClick();
            h.render();
            h.mutations[0].onError(new errors.ApiClientError(status, { error: { code, message: code } }));
            const tree = h.render();
            assert.equal(button(tree, 'Tải lại'), undefined);
            assert.ok(text(nodes(tree).find(n => n.type === 'dialog')).includes(code));
            assert.equal(button(tree, 'Lưu thay đổi').props.disabled, false);
        });
    }
    test(`${kind}: network mutation error remains actionable inside dialog`, () => {
        const h = harness(kind);
        button(h.render(), 'Chỉnh sửa').props.onClick();
        h.render();
        h.mutations[0].onError(new Error('network unavailable'));
        const tree = h.render();
        assert.ok(text(nodes(tree).find(n => n.type === 'dialog')).includes('network unavailable'));
        assert.equal(button(tree, 'Tải lại'), undefined);
    });
    test(`${kind}: dirty form declines recovery and preserves draft and conflict`, async () => withConfirm(false, async () => {
        const h = harness(kind);
        editConflict(h);
        h.draft = { name: 'Draft', description: 'Unsaved' };
        h.dirty = true;
        await button(h.render(), 'Tải lại').props.onClick();
        assert.equal(h.reloads, 0);
        assert.equal(h.draft.name, 'Draft');
        assert.ok(button(h.render(), 'Tải lại'));
        assert.equal(button(h.render(), 'Lưu thay đổi').props.disabled, true);
    }));
    test(`${kind}: accepted dirty recovery resets only after success; explicit retry uses current revision`, async () => withConfirm(true, async () => {
        const h = harness(kind);
        editConflict(h);
        h.draft = { name: 'Draft', description: 'Unsaved' };
        h.dirty = true;
        await button(h.render(), 'Tải lại').props.onClick();
        assert.equal(h.reloads, 1);
        assert.equal(h.draft.name, 'Current');
        let tree = h.render();
        assert.equal(button(tree, 'Tải lại'), undefined);
        assert.equal(button(tree, 'Lưu thay đổi').props.disabled, false);
        assert.equal(h.requests.length, 0, 'Reload never resubmits');
        const form = nodes(tree).find(n => n.type === 'form');
        await form.props.onSubmit();
        assert.equal(h.requests.at(-1).at(-1), 4);
        assert.equal(h.requests.at(-1)[0], 'one');
        if (kind === 'product')
            assert.equal(h.requests.at(-1)[1].context.keep, true);
    }));
    test(`${kind}: failed confirmed reload preserves draft conflict and cached parent`, async () => withConfirm(true, async () => {
        const h = harness(kind);
        editConflict(h);
        h.draft = { name: 'Draft' };
        h.dirty = true;
        h.reloadError = new Error('reload failed');
        const resetsBefore = h.resets.length;
        await button(h.render(), 'Tải lại').props.onClick();
        const tree = h.render();
        assert.equal(h.draft.name, 'Draft');
        assert.equal(h.resets.length, resetsBefore);
        assert.ok(text(tree).includes('reload failed'));
        assert.ok(text(tree).includes('Original'));
        assert.ok(text(tree).includes('dữ liệu này có thể đã cũ'));
        assert.ok(button(nodes(tree).find(n => n.type === 'dialog'), 'Tải lại'));
        assert.equal(button(tree, 'Lưu thay đổi').props.disabled, true);
        assert.equal(h.requests.length, 0);
    }));
    test(`${kind}: background refetch never advances the dirty form revision`, async () => {
        const h = harness(kind);
        button(h.render(), 'Chỉnh sửa').props.onClick();
        h.draft = { name: 'Old draft', description: 'Draft', tone: 'warm' };
        h.dirty = true;
        h.resource = { revision: 4, name: 'Server changed' };
        const tree = h.render();
        const dialog = nodes(tree).find(n => n.type === 'dialog');
        assert.match(text(dialog), /Phiên bản #3/);
        assert.doesNotMatch(text(dialog), /Phiên bản #4/);
        await nodes(tree).find(n => n.type === 'form').props.onSubmit();
        assert.equal(h.requests.at(-1).at(-1), 3);
        assert.equal(h.requests.at(-1)[1].name, 'Old draft');
    });
    test(`${kind}: recovery locks form and suppresses duplicate reloads`, async () => withConfirm(true, async () => {
        const h = harness(kind);
        editConflict(h);
        h.dirty = true;
        let resolve;
        h.reloadWait = new Promise(r => { resolve = r; });
        const reload = button(h.render(), 'Tải lại');
        const pending = reload.props.onClick();
        await reload.props.onClick();
        const tree = h.render();
        assert.equal(h.reloads, 1);
        assert.equal(button(tree, 'Đang tải lại').props.disabled, true);
        assert.equal(button(tree, 'Chỉnh sửa').props.disabled, true);
        assert.equal(nodes(tree).find(n => n.type === 'fieldset').props.disabled, true);
        resolve();
        await pending;
    }));
    test(`${kind}: archive conflict reload does not repeat archive and manual retry uses revision 4`, async () => withConfirm(true, async () => {
        const h = harness(kind);
        h.render();
        h.mutations[1].onError(conflictError());
        let tree = h.render();
        assert.ok(button(tree, 'Tải lại'));
        assert.equal(button(tree, 'Lưu trữ').props.disabled, true);
        await button(tree, 'Tải lại').props.onClick();
        assert.equal(h.requests.length, 0);
        tree = h.render();
        await button(tree, 'Lưu trữ').props.onClick();
        assert.deepEqual(h.requests, [['one', 4]]);
    }));
    for (const [status, code] of [[409, 'OTHER_CONFLICT'], [412, 'UNRELATED_PRECONDITION'], [422, 'VALIDATION_ERROR'], [403, 'FORBIDDEN']]) {
        test(`${kind}: archive ${status} ${code} does not become revision conflict`, () => {
            const h = harness(kind);
            h.render();
            h.mutations[1].onError(new errors.ApiClientError(status, { error: { code, message: code } }));
            assert.equal(button(h.render(), 'Tải lại'), undefined);
            assert.equal(button(h.render(), 'Lưu trữ').props.disabled, false);
        });
    }
    test(`${kind}: code-based revision conflict also recovers on HTTP 409`, () => {
        const h = harness(kind);
        h.render();
        h.mutations[1].onError(new errors.ApiClientError(409, { error: { code: 'REVISION_CONFLICT', message: 'conflict' } }));
        assert.ok(button(h.render(), 'Tải lại'));
    });
    for (const section of kind === 'project' ? ['videos', 'assets'] : ['assets']) {
        for (const [state, override, expected, forbidden] of [
            ['loading', { data: undefined, isLoading: true }, 'Đang tải', 'Chưa có'],
            ['successful empty', { data: { items: [], page: 1, page_size: 20, total: 0 } }, 'Chưa có', 'Không tải được'],
            ['populated', { data: { items: [item(section)], page: 1, page_size: 20, total: 1 } }, 'cached', 'Chưa có'],
            ['initial error', { data: undefined, error: new Error('offline') }, 'offline', 'Chưa có'],
            ['cached populated refresh error', { data: { items: [item(section)], page: 1, page_size: 20, total: 1 }, error: new Error('refresh failed') }, 'dữ liệu này có thể đã cũ', 'Chưa có'],
            ['cached empty refresh error', { data: { items: [], page: 1, page_size: 20, total: 0 }, error: new Error('refresh failed') }, 'Không thể xác nhận danh sách hiện tại', 'Chưa có'],
        ]) {
            test(`${kind} ${section}: ${state}`, () => {
                const h = harness(kind);
                h.related[section][1] = override;
                const tree = showSection(h, kind, section);
                assert.ok(text(tree).includes(expected));
                assert.ok(!text(tree).includes(forbidden));
                if (state.includes('populated'))
                    assert.ok(text(tree).includes('cached'));
            });
        }
        test(`${kind} ${section}: retry disabled while fetching and scoped retry succeeds`, async () => {
            const h = harness(kind);
            h.related[section][1] = { data: undefined, error: new Error('offline'), isFetching: true };
            let tree = showSection(h, kind, section);
            assert.equal(button(tree, 'Đang thử lại').props.disabled, true);
            button(tree, 'Đang thử lại').props.onClick();
            assert.deepEqual(h.retries, []);
            h.related[section][1].isFetching = false;
            tree = showSection(h, kind, section);
            button(tree, 'Thử lại').props.onClick();
            assert.deepEqual(h.retries, [section + '1']);
            tree = h.render();
            assert.ok(text(tree).includes('retried'));
            assert.equal(button(tree, 'Thử lại'), undefined);
        });
        test(`${kind} ${section}: page 2 failure retries locally and can return to verified page 1`, () => {
            const h = harness(kind);
            h.related[section][1] = { data: { items: [item(section, 'page-one')], page: 1, page_size: 20, total: 21 } };
            let tree = showSection(h, kind, section);
            button(tree, 'Trang sau').props.onClick();
            h.related[section][2] = { data: undefined, error: new Error('second-page failed') };
            tree = h.render();
            assert.ok(text(tree).includes('trang 2'));
            assert.ok(!text(tree).includes('Chưa có'));
            assert.ok(!text(tree).includes('page-one'));
            assert.equal(button(tree, 'Trang sau').props.disabled, true);
            assert.equal(button(tree, 'Trang trước').props.disabled, false);
            button(tree, 'Thử lại').props.onClick();
            assert.deepEqual(h.retries, [section + '2']);
            tree = h.render();
            assert.ok(text(tree).includes('retried'));
            button(tree, 'Trang trước').props.onClick();
            tree = h.render();
            assert.ok(text(tree).includes('page-one'));
        });
        test(`${kind} ${section}: parent change resets pages draft errors and cached rows`, () => {
            const h = harness(kind);
            h.related[section][1] = { data: { items: [item(section, 'old-parent')], page: 1, page_size: 20, total: 21 } };
            let tree = showSection(h, kind, section);
            button(tree, 'Trang sau').props.onClick();
            editConflict(h);
            h.changeParent();
            tree = h.render();
            assert.ok(!text(tree).includes('old-parent'));
            assert.ok(!nodes(tree).some(n => n.type === 'dialog'));
            assert.equal(button(tree, 'Tải lại'), undefined);
            assert.equal(h.queries.find(q => q.queryKey[2] === section).queryKey.at(-1).page, 1);
            assert.equal(h.queries.find(q => q.queryKey[2] === section).queryKey[1], 'two');
        });
        for (const count of [0, 1, 19, 20, 21, 40, 41, 101]) {
            test(`${kind} ${section}: ${count} rows reachable exactly once with last-page boundaries`, async () => {
                const h = harness(kind), all = Array.from({ length: count }, (_, i) => item(section, `row-${i}`));
                for (let p = 1; p <= Math.max(1, Math.ceil(count / 20)); p++)
                    h.related[section][p] = { data: { items: all.slice((p - 1) * 20, p * 20), page: p, page_size: 20, total: count } };
                let tree = showSection(h, kind, section), seen = [];
                for (let p = 1; p <= Math.max(1, Math.ceil(count / 20)); p++) {
                    const current = h.queries.find(q => q.queryKey[2] === section);
                    assert.deepEqual(current.queryKey.at(-1), { page: p, size: 20 });
                    await current.queryFn();
                    assert.deepEqual(h.requests.at(-1), ['one', { page: p, size: 20 }]);
                    for (const row of all.slice((p - 1) * 20, p * 20)) {
                        assert.ok(text(tree).includes(section === 'videos' ? row.title : row.filename));
                        seen.push(row.id);
                    }
                    assert.equal(button(tree, 'Trang trước').props.disabled, p === 1);
                    const last = p >= Math.max(1, Math.ceil(count / 20));
                    assert.equal(button(tree, 'Trang sau').props.disabled, last);
                    if (!last) {
                        button(tree, 'Trang sau').props.onClick();
                        tree = h.render();
                    }
                }
                assert.deepEqual(seen, all.map(row => row.id));
                assert.equal(new Set(seen).size, count);
            });
        }
        test(`${kind} ${section}: successful empty page 2 is not global empty`, () => {
            const h = harness(kind);
            h.related[section][1] = { data: { items: [item(section)], page: 1, page_size: 20, total: 21 } };
            button(showSection(h, kind, section), 'Trang sau').props.onClick();
            h.related[section][2] = { data: { items: [], page: 2, page_size: 20, total: 20 } };
            const tree = h.render();
            assert.ok(text(tree).includes('Không có mục nào ở trang 2'));
            assert.ok(!text(tree).includes('Chưa có'));
            assert.equal(button(tree, 'Trang trước').props.disabled, false);
        });
    }
}
for (const [failed, healthy] of [['videos', 'assets'], ['assets', 'videos']]) {
    test(`project: failed ${failed} does not hide healthy ${healthy}`, () => {
        const h = harness('project');
        h.related[failed][1] = { data: undefined, error: new Error('offline') };
        h.related[healthy][1] = { data: { items: [item(healthy, 'healthy-row')], page: 1, page_size: 20, total: 1 } };
        const tree = showSection(h, 'project', healthy);
        assert.ok(text(tree).includes('healthy-row'));
        assert.equal(button(tree, 'Thử lại'), undefined);
        const failedTree = showSection(h, 'project', failed);
        assert.ok(button(failedTree, 'Thử lại'));
        assert.deepEqual(h.retries, []);
    });
}
test('project: both related queries fail with independent retries', () => {
    const h = harness('project');
    for (const section of ['videos', 'assets'])
        h.related[section][1] = { data: undefined, error: new Error(section + ' offline') };
    for (const section of ['videos', 'assets']) {
        const tree = showSection(h, 'project', section);
        assert.ok(text(tree).includes(section + ' offline'));
        assert.ok(!text(tree).includes('Chưa có'));
        button(tree, 'Thử lại').props.onClick();
    }
    assert.deepEqual(h.retries, ['videos1', 'assets1']);
});
