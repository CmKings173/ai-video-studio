'use strict';
// Review aid: report static UI copy that has not passed through explicit locale selection.
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const roots = ['app', 'components'].map(p => path.join(__dirname, '..', p));
const props = new Set(['title', 'description', 'label', 'placeholder', 'aria-label', 'alt', 'eyebrow', 'detail', 'fallbackMessage']);
function inspect(file) {
  const source = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  function visit(node) {
    let copy;
    if (ts.isJsxText(node)) copy = node.text.trim();
    if (ts.isJsxAttribute(node) && props.has(node.name.getText(source)) && node.initializer && ts.isStringLiteral(node.initializer)) copy = node.initializer.text;
    if (copy && /[a-zA-ZÀ-ỹ]/.test(copy)) console.log(`${path.relative(path.join(__dirname, '..'), file)}:${source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1} ${copy.replace(/\s+/g, ' ')}`);
    ts.forEachChild(node, visit);
  }
  visit(source);
}
function walk(dir) {
  for (const item of fs.readdirSync(dir, { withFileTypes: true })) {
    const file = path.join(dir, item.name);
    if (item.isDirectory()) walk(file); else if (file.endsWith('.tsx')) inspect(file);
  }
}
roots.forEach(walk);
