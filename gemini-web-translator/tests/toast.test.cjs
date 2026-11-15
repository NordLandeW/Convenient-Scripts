const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

test('remote error content becomes a text node rather than executable markup', () => {
    const source = fs.readFileSync(path.join(__dirname, '..', 'gemini-web-translator.js'), 'utf8');
    const instrumented = source.replace('    if (isExcludedContext()) {',
        '    globalThis.render = getToastContent; return; if (isExcludedContext()) {');
    const element = () => ({ textContent: '', style: {}, children: [], append(...children) { this.children.push(...children); } });
    const context = vm.createContext({ document: { createElement: element, createDocumentFragment: element } });
    vm.runInContext(instrumented, context);
    const text = '<img src=x onerror="alert(1)"><b>remote error</b>';
    const content = context.render(text, 'error');
    assert.equal(content.children[1].textContent, text);
    assert.equal(content.children[1].children.length, 0);
});
