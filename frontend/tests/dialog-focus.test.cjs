const assert = require('node:assert/strict');
const test = require('node:test');
const React = require('react');
const { loadTypeScript } = require('./load-typescript.cjs');

function harness() {
  const refs = [], effects = [], listeners = new Map();
  let cursor = 0, effectCursor = 0;
  const document = { activeElement: null, body: { style: { overflow: '' } } };
  const element = (options = {}) => ({
    tabIndex: 0, disabled: false, hidden: false, ...options,
    matches(selector) { return selector === ':disabled' && this.disabled; },
    closest() { return this.inert ? {} : null; },
    getClientRects() { return this.hidden ? [] : [{}]; },
    focus() { if (!this.disabled && !this.hidden) document.activeElement = this; },
  });
  const trigger = element();
  const close = element(), cancel = element(), save = element({ disabled: true });
  const dialog = element({ tabIndex: -1 });
  let controls = [close, cancel, save];
  dialog.querySelectorAll = () => controls;
  dialog.querySelector = () => controls[0];
  dialog.contains = node => node === dialog || controls.includes(node);
  const mocks = {
    react: { ...React,
      useRef(initial) { const index = cursor++; return refs[index] ||= { current: initial }; },
      useEffect(callback, deps) {
        const index = effectCursor++;
        const old = effects[index];
        if (!old || deps.some((value, i) => value !== old.deps[i])) {
          effects[index] = { callback, deps, oldCleanup: old?.cleanup, pending: true };
        }
      },
    },
  };
  const Dialog = loadTypeScript('components/ui/dialog.tsx', mocks).Dialog;
  const original = { document: global.document, window: global.window, getComputedStyle: global.getComputedStyle };
  global.document = document;
  global.window = { addEventListener: (name, callback) => listeners.set(name, callback), removeEventListener: name => listeners.delete(name) };
  global.getComputedStyle = node => ({ visibility: node.hidden ? 'hidden' : 'visible', display: node.hidden ? 'none' : 'block' });
  function render(onClose = () => {}) {
    cursor = effectCursor = 0;
    const tree = Dialog({ isOpen: true, onClose, title: 'Edit', children: null });
    tree.props.children.props.ref.current = dialog;
    for (const effect of effects) if (effect.pending) {
      effect.oldCleanup?.(); effect.cleanup = effect.callback(); effect.pending = false;
    }
  }
  document.activeElement = trigger;
  render();
  return { document, trigger, close, cancel, save, dialog, element, render,
    controls(value) { controls = value; },
    key(key, shiftKey = false) {
      let prevented = false;
      listeners.get('keydown')({ key, shiftKey, preventDefault() { prevented = true; } });
      return prevented;
    },
    cleanup() {
      for (const effect of effects) effect.cleanup?.();
      for (const [key, value] of Object.entries(original)) {
        if (value === undefined) delete global[key]; else global[key] = value;
      }
    },
  };
}

test('dialog wraps past disabled Save to enabled Close, in both directions', () => {
  const h = harness();
  try {
    h.document.activeElement = h.cancel;
    assert.equal(h.key('Tab'), true);
    assert.equal(h.document.activeElement, h.close);
    assert.equal(h.key('Tab', true), true);
    assert.equal(h.document.activeElement, h.cancel);
  } finally { h.cleanup(); }
});

for (const [name, options] of [['hidden', { hidden: true }], ['inert', { inert: true }], ['negative tabindex', { tabIndex: -1 }]]) {
  test(`dialog excludes ${name} controls from Tab boundaries`, () => {
    const h = harness();
    try {
      h.controls([h.close, h.cancel, h.element(options)]);
      h.document.activeElement = h.cancel;
      assert.equal(h.key('Tab'), true);
      assert.equal(h.document.activeElement, h.close);
    } finally { h.cleanup(); }
  });
}

test('dialog recaptures outside focus and focuses container with no enabled targets', () => {
  const h = harness();
  try {
    h.document.activeElement = h.trigger;
    assert.equal(h.key('Tab'), true);
    assert.equal(h.document.activeElement, h.close);
    h.controls([h.save]); h.document.activeElement = h.trigger;
    assert.equal(h.key('Tab'), true);
    assert.equal(h.document.activeElement, h.dialog);
  } finally { h.cleanup(); }
});

test('dialog callback update preserves original trigger for focus restoration', () => {
  const h = harness();
  try {
    h.document.activeElement = h.cancel;
    let closed = 0;
    h.render(() => { closed++; });
    assert.equal(h.document.activeElement, h.cancel, 'callback update must not restore focus while dialog is still open');
    h.key('Escape');
    assert.equal(closed, 1);
  } finally {
    h.cleanup();
    assert.equal(h.document.activeElement, h.trigger);
  }
});
