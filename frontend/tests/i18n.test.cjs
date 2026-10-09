const test = require('node:test');
const assert = require('node:assert/strict');
const { loadTypeScript } = require('./load-typescript.cjs');

test('locale defaults to Vietnamese, persists English, notifies and cleans subscriptions', () => {
  const oldWindow = global.window;
  const listeners = new Map(), values = new Map();
  global.window = {
    localStorage: { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) },
    addEventListener: (key, listener) => listeners.set(key, listener),
    removeEventListener: key => listeners.delete(key),
    dispatchEvent: event => listeners.get(event.type)?.(event),
  };
  try {
    const locale = loadTypeScript('lib/i18n.tsx');
    assert.equal(locale.getLocaleSnapshot(), 'vi');
    let notified = 0;
    const cleanup = locale.subscribeLocale(() => notified++);
    locale.setAppLocale('en');
    assert.equal(locale.getLocaleSnapshot(), 'en');
    assert.equal(values.get(locale.LOCALE_KEY), 'en');
    assert.equal(notified, 1);
    values.set(locale.LOCALE_KEY, 'vi');
    listeners.get('storage')({ key: locale.LOCALE_KEY });
    assert.equal(locale.getLocaleSnapshot(), 'vi');
    values.set(locale.LOCALE_KEY, 'invalid');
    assert.equal(locale.getLocaleSnapshot(), 'vi');
    cleanup(); assert.equal(listeners.size, 0);
    global.window.localStorage = { getItem() { throw Error('disabled'); }, setItem() { throw Error('disabled'); } };
    locale.setAppLocale('en'); assert.equal(locale.getLocaleSnapshot(), 'en');
    assert.equal(locale.translateText('Tạo video', 'Create video'), 'Create video');
  } finally {
    if (oldWindow === undefined) delete global.window; else global.window = oldWindow;
  }
});

test('server markup starts in Vietnamese without reading browser storage', () => {
  const React = require('react');
  const { renderToStaticMarkup } = require('react-dom/server');
  const { useI18n } = loadTypeScript('lib/i18n.tsx');
  function Text() { const { t } = useI18n(); return React.createElement('span', null, t('Tên video', 'Video title')); }
  assert.equal(renderToStaticMarkup(React.createElement(Text)), '<span>Tên video</span>');
});

test('default validation copy is localized without rewriting custom messages', () => {
  const { validationCopy } = loadTypeScript('lib/validation-copy.ts');
  assert.equal(validationCopy('Number must be a multiple of 2', 'vi'), 'Giá trị phải chia hết cho 2.');
  assert.equal(validationCopy('Invalid email', 'vi'), 'Email không hợp lệ.');
  assert.equal(validationCopy('Invalid email', 'en'), 'Invalid email');
  assert.equal(validationCopy('Specific provider diagnostic 123', 'vi'), 'Specific provider diagnostic 123');
});
