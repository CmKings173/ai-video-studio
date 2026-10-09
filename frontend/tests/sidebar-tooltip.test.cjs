const assert = require('node:assert/strict');
const test = require('node:test');
const React = require('react');
const { loadTypeScript } = require('./load-typescript.cjs');

test('sidebar tooltip shows label and purpose on hover/focus, dismisses on blur/Escape, and fits narrow viewport', async () => {
  let position = null;
  const listeners = new Map(), refs = [], effects = [];
  let cursor = 0;
  const originalWindow = global.window, originalDocument = global.document;
  global.window = { innerWidth: 360, innerHeight: 700, addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) };
  global.document = { body: {} };
  try {
    const { SidebarNavLink } = loadTypeScript('components/sidebar-nav-link.tsx', {
      react: { ...React, useId: () => 'nav-tip', useState: () => [position, value => { position = value; }], useRef: initial => refs[cursor++] ||= { current: initial }, useEffect: fn => effects.push(fn) },
      'react-dom': { createPortal: element => element },
      'next/link': props => React.createElement('a', props),
    });
    const render = () => { cursor = 0; return SidebarNavLink({ href: '/projects', label: 'Dự án', description: 'Quản lý chiến dịch và video.', active: true, children: null }); };
    const event = { currentTarget: { getBoundingClientRect: () => ({ right: 56, left: 8, top: 120 }) } };
    let tree = render(), link = tree.props.children[0];
    assert.equal(link.props['aria-current'], 'page');
    link.props.onMouseEnter(event);
    tree = render();
    assert.equal(tree.props.children[1].props.role, 'tooltip');
    assert.equal(tree.props.children[0].props['aria-describedby'], 'nav-tip');
    assert.equal(tree.props.children[1].props.children[0].props.children, 'Dự án');
    assert.equal(tree.props.children[1].props.children[1].props.children, 'Quản lý chiến dịch và video.');
    const style = tree.props.children[1].props.style;
    assert.ok(style.left >= 0 && style.left + style.width <= 360);
    for (const effect of effects.splice(0)) effect();
    assert.equal(typeof listeners.get('keydown'), 'function', 'hover-only Escape needs a window listener');
    listeners.get('keydown')({ key: 'Escape' });
    assert.equal(render().props.children[1], null);
    render().props.children[0].props.onMouseEnter(event);
    tree = render();
    tree.props.children[0].props.onMouseLeave();
    tree.props.children[1].props.onMouseEnter();
    await new Promise(resolve => setTimeout(resolve, 200));
    assert.ok(render().props.children[1], 'pointer can remain over description');
    render().props.children[1].props.onMouseLeave();
    await new Promise(resolve => setTimeout(resolve, 200));
    assert.equal(render().props.children[1], null);
    render().props.children[0].props.onMouseEnter(event);
    tree = render();
    tree.props.children[0].props.onMouseLeave();
    tree.props.children[1].props.onMouseEnter();
    for (const effect of effects.splice(0)) effect();
    listeners.get('keydown')({ key: 'Escape' });
    render().props.children[0].props.onFocus(event);
    render().props.children[0].props.onBlur();
    await new Promise(resolve => setTimeout(resolve, 200));
    assert.equal(render().props.children[1], null, 'dismissed portal must not retain hover across focus and blur');
    render().props.children[0].props.onFocus(event);
    tree.props.children[0].props.onKeyDown({ key: 'Escape' });
    assert.equal(render().props.children[1], null);
    render().props.children[0].props.onFocus(event);
    assert.ok(render().props.children[1]);
    render().props.children[0].props.onBlur();
    await new Promise(resolve => setTimeout(resolve, 200));
    assert.equal(render().props.children[1], null);
  } finally {
    if (originalWindow === undefined) delete global.window; else global.window = originalWindow;
    if (originalDocument === undefined) delete global.document; else global.document = originalDocument;
  }
});
